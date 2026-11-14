"""Окно поверх экрана: перевод, имя говорящего, серая недописанная фраза, строка состояния.

Окно не забирает фокус: пока оно на экране, можно печатать в Teams. Перетаскивается мышью,
размер меняется за правый нижний угол, правая кнопка — меню.
"""
from __future__ import annotations

import ctypes
import html
import os

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget

from . import style

FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11


def set_capture_hidden(widget: QWidget, hidden: bool) -> bool:
    """Скрыть окно от демонстрации экрана и записи (функция Windows). Не Windows — ничего не делаем."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            int(widget.winId()), WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE))
    except Exception:  # noqa: BLE001
        return False


class Grip(QLabel):
    """Уголок для изменения размера. Сделан вручную: системное перетаскивание плохо работает
    у окон, которые не забирают фокус."""

    def __init__(self, window: QWidget):
        super().__init__("◢", window)
        self.window_, self._start = window, None
        self.setCursor(Qt.SizeFDiagCursor)
        self.setFixedSize(16, 16)

    def mousePressEvent(self, event) -> None:
        self._start = (event.globalPosition().toPoint(), self.window_.size())

    def mouseMoveEvent(self, event) -> None:
        if self._start:
            delta = event.globalPosition().toPoint() - self._start[0]
            self.window_.resize(max(320, self._start[1].width() + delta.x()),
                                max(110, self._start[1].height() + delta.y()))

    def mouseReleaseEvent(self, event) -> None:
        self._start = None


class Overlay(QWidget):
    menu_requested = Signal(QPoint)
    geometry_changed = Signal(list)

    def __init__(self, font_size: int = 15, opacity: float = 0.9, lines: int = 3, show_original: bool = True,
                 geometry: list | None = None):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Dolmetsch")
        self.font_size, self.max_lines, self.show_original = font_size, lines, show_original
        self.lines: list = []                # последние переводы
        self.partial = ("", "")              # недописанная фраза: (кто, немецкий текст)
        self.draft = ""                      # черновой перевод недописанной фразы
        self.notice = ""                     # временное сообщение

        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.text = QTextEdit(self.card)
        self.text.setReadOnly(True)
        self.text.setFrameStyle(QFrame.NoFrame)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.setTextInteractionFlags(Qt.NoTextInteraction)
        self.text.setAttribute(Qt.WA_TransparentForMouseEvents)     # мышь достаётся окну — для перетаскивания
        self.text.setFocusPolicy(Qt.NoFocus)
        self.status = QLabel(self.card)
        self.status.setAttribute(Qt.WA_TransparentForMouseEvents)
        grip = Grip(self)
        self._drag = None

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(grip, 0, Qt.AlignBottom | Qt.AlignRight)
        inner = QVBoxLayout(self.card)
        inner.setContentsMargins(16, 12, 10, 8)
        inner.setSpacing(4)
        inner.addWidget(self.text, 1)
        inner.addLayout(bottom)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.card)

        self._notice_timer = QTimer(self, singleShot=True, timeout=self._clear_notice)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self._emit_geometry)
        self.set_opacity(opacity)
        self._place(geometry)
        self.set_status("Запуск…", "warn")
        self.render()

    # ---------- вид
    def set_opacity(self, opacity: float) -> None:
        r, g, b = style.CARD
        alpha = int(255 * min(1.0, max(0.3, opacity)))
        self.card.setStyleSheet(
            f"#card {{ background: rgba({r},{g},{b},{alpha}); border: 1px solid rgba(255,255,255,28); border-radius: 14px; }}"
            f"QTextEdit {{ background: transparent; color: {style.INK}; }}"
            f"QLabel {{ color: {style.MUTED}; font: 9pt 'Segoe UI'; }}")

    def _place(self, geometry: list | None) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if geometry and len(geometry) == 4:
            x, y, w, h = geometry
            # сохранённое место годится, если оно видно на каком-то из мониторов
            if any(s.availableGeometry().contains(QPoint(x + 40, y + 20)) for s in QGuiApplication.screens()):
                return self.setGeometry(x, y, max(320, w), max(110, h))
        w, h = min(980, screen.width() - 80), 210
        self.setGeometry(screen.x() + (screen.width() - w) // 2, screen.bottom() - h - 60, w, h)

    def set_font_size(self, size: int) -> None:
        self.font_size = max(9, min(40, size))
        self.render()

    # ---------- данные
    def add_line(self, line) -> None:
        self.lines.append(line)
        del self.lines[:-self.max_lines]
        self.draft = ""
        self.render()

    def set_partial(self, speaker: str, text: str) -> None:
        self.partial = (speaker, text)
        if not text:
            self.draft = ""
        self.render()

    def set_draft(self, text: str) -> None:
        self.draft = text
        self.render()

    def set_status(self, text: str, level: str = "ok") -> None:
        color = {"ok": style.OK, "warn": style.WARN, "bad": style.BAD}.get(level, style.MUTED)
        self.status.setText(f"<span style='color:{color}'>●</span> {html.escape(text)}")

    def flash(self, text: str, seconds: float = 8.0) -> None:
        """Временное сообщение в окне (ошибка, подсказка)."""
        self.notice = text
        self._notice_timer.start(int(seconds * 1000))
        self.render()

    def _clear_notice(self) -> None:
        self.notice = ""
        self.render()

    def clear(self) -> None:
        self.lines, self.partial, self.draft = [], ("", ""), ""
        self.render()

    # ---------- отрисовка
    def render(self) -> None:
        esc, small = html.escape, max(8, self.font_size - 4)
        parts = []
        for ln in self.lines:
            who = f"<span style='color:{style.ACCENT}; font-weight:600'>{esc(ln.speaker)}</span>&nbsp; " if ln.speaker else ""
            if ln.translation:
                parts.append(f"<div style='margin-top:5px'>{who}{esc(ln.translation)}</div>")
                if self.show_original:
                    parts.append(f"<div style='color:{style.MUTED}; font-size:{small}pt'>{esc(ln.text)}</div>")
            else:                            # перевода нет — показываем оригинал, чтобы ничего не потерять
                parts.append(f"<div>{who}<span style='color:{style.MUTED}'>{esc(ln.text)}</span></div>")
        speaker, text = self.partial
        if self.draft:
            parts.append(f"<div style='color:#cbd5e1'><i>{esc(self.draft)} …</i></div>")
        if text:
            who = f"{esc(speaker)}: " if speaker and not (self.lines and self.lines[-1].speaker == speaker) else ""
            parts.append(f"<div style='color:{style.MUTED}; font-size:{small}pt'><i>{who}{esc(text)} …</i></div>")
        if self.notice:
            parts.append(f"<div style='color:{style.WARN}; font-size:{small}pt'>{esc(self.notice)}</div>")
        body = "".join(parts) or f"<div style='color:{style.MUTED}'>Перевод появится здесь.</div>"
        self.text.setHtml(f"<div style=\"font-family:'Segoe UI'; font-size:{self.font_size}pt\">{body}</div>")
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())          # всегда видим самое свежее

    # ---------- мышь и размер
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(event.globalPos())

    def moveEvent(self, event) -> None:
        self._save_timer.start()

    def resizeEvent(self, event) -> None:
        self._save_timer.start()
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _emit_geometry(self) -> None:
        g = self.geometry()
        self.geometry_changed.emit([g.x(), g.y(), g.width(), g.height()])
