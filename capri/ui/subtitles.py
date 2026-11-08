"""Окно субтитров: полоса внизу экрана поверх всех окон (Teams, браузер). Оригинал — мелко серым, перевод — крупно.

Наведи мышь — появятся кнопки: ⏸ пауза, A−/A+ размер, ☰ вся запись, 📝 конспект, ✕ стоп.
Перетаскивается мышью; размер и место запоминаются. Строка, где назвали твоё имя или слово-сигнал, — жёлтая.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizeGrip, QTextBrowser, QVBoxLayout, QWidget)

FLAG = {"de": "🇩🇪", "en": "🇬🇧", "ru": "🇷🇺", "he": "🇮🇱", "uk": "🇺🇦"}
WHO = {"system": "", "mic": "Ты: "}

BTN = """QPushButton { border: 1px solid #1d3550; color: #9fb3cf; background: rgba(11,18,34,220); padding: 1px 7px;
font-size: 11px; border-radius: 3px; } QPushButton:hover { border-color: #3ff3ff; color: #3ff3ff; }"""


class SubtitleBar(QWidget):
    stop_clicked = Signal()
    pause_toggled = Signal(bool)
    notes_clicked = Signal()
    geometry_saved = Signal(list)          # [x, y, w, h, шрифт]

    SHOW = 3                               # сколько последних фраз на экране

    def __init__(self, font_size: int = 20):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Capri — субтитры")
        self.font_size = font_size
        self.entries: dict[int, dict] = {}
        self.order: list[int] = []
        self.partial = ("", "")
        self.status = ""
        self.paused = False
        self.flash = 0
        self._drag: QPoint | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 10)
        lay.setSpacing(2)
        self.bar = QWidget()
        b = QHBoxLayout(self.bar)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(4)
        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("color:#5b6b85; font-size:11px;")
        b.addWidget(self.lbl_status, 1)
        self.btn_pause = self._btn("⏸", "Пауза", self._pause)
        for w in (self.btn_pause, self._btn("A−", "Мельче", lambda: self.zoom(-2)), self._btn("A+", "Крупнее", lambda: self.zoom(2)),
                  self._btn("☰", "Вся запись урока", self.show_history), self._btn("📝", "Конспект (остановит субтитры)",
                                                                                 self.notes_clicked.emit),
                  self._btn("✕", "Выключить субтитры", self.stop_clicked.emit)):
            b.addWidget(w)
        lay.addWidget(self.bar)
        self.text = QLabel(wordWrap=True, textFormat=Qt.RichText)
        self.text.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        self.text.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.text, 1)
        grip = QSizeGrip(self)
        lay.addWidget(grip, 0, Qt.AlignRight | Qt.AlignBottom)
        self.bar.setVisible(False)
        self.history = None
        self._flash_timer = QTimer(self, interval=120, timeout=self._flash_step)
        self.place()
        self.render()

    def _btn(self, text, tip, fn):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setStyleSheet(BTN)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def place(self, geo: list | None = None):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        if geo and len(geo) >= 4:
            self.setGeometry(*geo[:4])
            if len(geo) > 4:
                self.font_size = int(geo[4])
            return
        w = int(scr.width() * 0.62)
        h = 190
        self.setGeometry(scr.left() + (scr.width() - w) // 2, scr.bottom() - h - 24, w, h)

    # ------------------------------------------------------------------ данные
    def add_line(self, id_: int, source: str, text: str, lang: str = "", trigger: str = ""):
        self.entries[id_] = {"source": source, "text": text, "lang": lang, "tr": None, "trigger": trigger}
        self.order.append(id_)
        if self.history is not None:
            self._append_history(id_)
        if trigger:
            self.flash = 14
            self._flash_timer.start()
            self.raise_()
        self.render()

    def set_translation(self, id_: int, text: str):
        if id_ in self.entries:
            self.entries[id_]["tr"] = text
            self.render()
            if self.history is not None:
                self._fill_history()

    def set_partial(self, source: str, text: str):
        self.partial = (source, text)
        self.render()

    def set_status(self, text: str):
        self.status = text
        self.lbl_status.setText(text)
        self.render()

    # ------------------------------------------------------------------ вид
    def zoom(self, d: int):
        self.font_size = max(12, min(40, self.font_size + d))
        self.render()
        self._save()

    def _pause(self):
        self.paused = not self.paused
        self.btn_pause.setText("▶" if self.paused else "⏸")
        self.pause_toggled.emit(self.paused)
        self.render()

    def render(self):
        fs, small = self.font_size, max(10, int(self.font_size * 0.62))
        parts = []
        for id_ in self.order[-self.SHOW:]:
            e = self.entries[id_]
            mine = e["source"] == "mic"
            col = "#ffd23f" if e["trigger"] else ("#ff7ab0" if mine else "#9fb3cf")
            flag = FLAG.get(e["lang"], "")
            bell = f" 🔔 <b>{html.escape(e['trigger'])}</b>" if e["trigger"] else ""
            parts.append(f'<div style="color:{col}; font-size:{small}px; margin-top:6px;">{flag} {WHO.get(e["source"], "")}'
                         f'{html.escape(e["text"])}{bell}</div>')
            if e["tr"] is None and not mine:
                parts.append(f'<div style="color:#5b6b85; font-size:{fs}px;">…</div>')
            elif e["tr"]:
                tcol = "#fff3b0" if e["trigger"] else ("#ffd0e4" if mine else "#ffffff")
                parts.append(f'<div style="color:{tcol}; font-size:{fs}px; font-weight:600;">{html.escape(e["tr"])}</div>')
        src, ptxt = self.partial
        if ptxt:
            parts.append(f'<div style="color:#7d8db0; font-size:{small}px; font-style:italic;">'
                         f'{"Ты: " if src == "mic" else ""}{html.escape(ptxt)} …</div>')
        if not parts:
            msg = "⏸ Пауза" if self.paused else (self.status or "Слушаю… Включи урок или видео — субтитры появятся здесь.")
            parts.append(f'<div style="color:#5b6b85; font-size:{small}px;">{html.escape(msg)}</div>')
        self.text.setText("".join(parts))
        self.update()

    def _flash_step(self):
        self.flash -= 1
        if self.flash <= 0:
            self._flash_timer.stop()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, QColor(7, 11, 22, 215))
        on = self.flash > 0 and self.flash % 2 == 0
        p.setPen(QPen(QColor("#ffd23f" if on else ("#3ff3ff" if self.underMouse() else "#1d3550")), 2 if on else 1))
        p.drawPath(path)
        p.end()

    # ------------------------------------------------------------------ мышь
    def enterEvent(self, _):
        self.bar.setVisible(True)
        self.update()

    def leaveEvent(self, _):
        self.bar.setVisible(False)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        if self._drag is not None:
            self._drag = None
            self._save()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(400, self._save)

    def _save(self):
        g = self.geometry()
        self.geometry_saved.emit([g.x(), g.y(), g.width(), g.height(), self.font_size])

    # ------------------------------------------------------------------ вся запись
    def show_history(self):
        if self.history is None:
            self.history = QTextBrowser()
            self.history.setWindowTitle("Capri — запись урока")
            self.history.setStyleSheet("QTextBrowser { background:#0b1222; color:#eef6ff; font-size:13px; padding:8px; }")
            self.history.resize(640, 720)
        self._fill_history()
        self.history.show()
        self.history.raise_()

    def _entry_html(self, id_: int) -> str:
        e = self.entries[id_]
        who = "Ты" if e["source"] == "mic" else "Они"
        col = "#ffd23f" if e["trigger"] else ("#ff7ab0" if e["source"] == "mic" else "#9fb3cf")
        tr = f'<br><span style="color:#ffffff">{html.escape(e["tr"])}</span>' if e["tr"] else ""
        return f'<p><span style="color:{col}"><b>{who}:</b> {html.escape(e["text"])}</span>{tr}</p>'

    def _fill_history(self):
        if self.history is None:
            return
        bar = self.history.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.history.setHtml("".join(self._entry_html(i) for i in self.order))
        if at_end:
            bar.setValue(bar.maximum())

    def _append_history(self, id_: int):
        self._fill_history()

    def clear(self):
        self.entries, self.order, self.partial = {}, [], ("", "")
        self.render()
