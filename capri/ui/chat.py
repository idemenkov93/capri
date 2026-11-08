"""Окно чата в киберпанк-стиле."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
                               QWidget)

CODE_RX = re.compile(r"```[^\n]*\n(.*?)```", re.S)
BRAINS = (("auto", "Авто", "Как обычно: простое — локальная модель, сложное — помощник из мастера"),
          ("local", "Локальная", "Только модель на этом компьютере (без интернета)"),
          ("gemini", "Gemini", "Только Gemini (бесплатный ключ)"),
          ("claude", "Claude", "Только Claude (подписка)"))

STYLE = """
QWidget { background: #0b0b14; color: #e6e6f0; font-family: Consolas, 'JetBrains Mono', monospace; font-size: 13px; }
QTextBrowser { border: 1px solid #ff2a6d; padding: 6px; }
QLineEdit { border: 1px solid #00f0ff; padding: 6px; background: #12121f; }
QPushButton { border: 1px solid #00f0ff; padding: 6px 12px; background: #12121f; color: #00f0ff; }
QPushButton:hover { background: #00f0ff; color: #0b0b14; }
QLabel#live { color: #ffd23f; border: 1px dashed #ffd23f; padding: 6px; }
"""

ROUTE_LABEL = {"rules": "⚡", "local": "◆ local", "claude": "✦ Claude", "gemini": "✧ Gemini", "error": "✖"}


class ChatExtras:
    """Общее для обоих окон чата: «📋 копировать» под ответами и переключатель «Отвечает: …»."""

    def _init_extras(self, log: QTextBrowser):
        self._clips: dict[str, str] = {}
        log.setOpenExternalLinks(False)
        log.setOpenLinks(False)
        log.anchorClicked.connect(self._link)

    def _brain_row(self) -> QHBoxLayout:
        brow = QHBoxLayout()
        brow.setSpacing(4)
        lbl = QLabel("Отвечает:")
        lbl.setStyleSheet("color:#5b6b85; font-size:10px;")
        brow.addWidget(lbl)
        self.brain_group = QButtonGroup(self)
        self.brain_buttons: dict[str, QPushButton] = {}
        for code, title, tip in BRAINS:
            b = QPushButton(title, objectName="brain", checkable=True)
            b.setToolTip(tip)
            self.brain_group.addButton(b)
            self.brain_buttons[code] = b
            b.clicked.connect(lambda _=False, c=code: self.brain_changed.emit(c))
            brow.addWidget(b)
        brow.addStretch(1)
        self.brain_buttons["auto"].setChecked(True)
        return brow

    def set_brain(self, code: str):
        if b := self.brain_buttons.get(code):
            b.setChecked(True)

    def copy_links(self, text: str) -> str:
        """Строка «📋 копировать ответ · 📋 код 1 …» под ответом."""
        links = [self._clip_link(text.strip(), "📋 копировать ответ")]
        codes = [c.strip("\n") for c in CODE_RX.findall(text)]
        for i, code in enumerate(codes, 1):
            links.append(self._clip_link(code, "📋 код" if len(codes) == 1 else f"📋 код {i}"))
        return '<p style="font-size:11px">' + ' <span style="color:#444">·</span> '.join(links) + "</p>"

    def _clip_link(self, text: str, title: str) -> str:
        key = str(len(self._clips))
        self._clips[key] = text
        return f'<a href="copy:{key}" style="color:#00f0ff; text-decoration:none">{html.escape(title)}</a>'

    def copy_text(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self.add_system("Скопировано в буфер.")

    def _link(self, url: QUrl):
        if url.scheme() == "copy":
            self.copy_text(self._clips.get(url.path(), ""))
        else:
            QDesktopServices.openUrl(url)


BRAIN_CSS = """
QPushButton#brain { border: 1px solid #1d3550; color: #5b6b85; padding: 2px 8px; font-size: 10px; background: transparent; }
QPushButton#brain:checked { border-color: #ff2a6d; color: #ff2a6d; }
"""


class ChatWindow(ChatExtras, QWidget):
    submitted = Signal(str)
    stop_requested = Signal()
    mic_clicked = Signal()
    brain_changed = Signal(str)

    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("CAPRI // link")
        self.setStyleSheet(STYLE + BRAIN_CSS)
        self.resize(460, 560)

        self.log = QTextBrowser()
        self._init_extras(self.log)
        self.input = QLineEdit(placeholderText="Напиши Капри… (/claude — сразу к Claude)")
        self.send = QPushButton("SEND")
        self.stop = QPushButton("STOP")
        self.stop.setStyleSheet("border-color:#ff2a6d; color:#ff2a6d;")
        self.stop.hide()
        self.mic = QPushButton("🎤")
        self.mic.setToolTip("Сказать голосом (нажми, говори, нажми ещё раз)")
        self.mic.hide()

        row = QHBoxLayout()
        row.addWidget(self.mic)
        row.addWidget(self.input)
        row.addWidget(self.send)
        row.addWidget(self.stop)
        lay = QVBoxLayout(self)
        lay.addWidget(self.log)
        brow = self._brain_row()          # кто отвечает на письменные вопросы
        # «живой» ответ: текст появляется по мере того, как модель его печатает
        self.live = QLabel(objectName="live", wordWrap=True)
        self.live.hide()
        self._live_text = ""
        self._thinking = 0
        lay.addWidget(self.live)
        lay.addLayout(brow)
        lay.addLayout(row)

        self.input.returnPressed.connect(self._submit)
        self.send.clicked.connect(self._submit)
        self.stop.clicked.connect(self.stop_requested.emit)
        self.mic.clicked.connect(self.mic_clicked.emit)

    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.add_user(text)
            self.submitted.emit(text)

    def set_busy(self, busy: bool):
        self.input.setEnabled(not busy)
        self.send.setVisible(not busy)
        self.stop.setVisible(busy)
        if not busy:
            self.input.setFocus()

    def set_recording(self, on: bool):
        self.mic.setText("⏺ REC" if on else "🎤")
        self.mic.setStyleSheet("border-color:#ff0033; color:#ff0033;" if on else "")

    def stream(self, kind: str, piece: str):
        if kind == "reset":  # модель закончила рассуждать — показываем только сам ответ
            self._live_text = piece
            self.live.setText(self._live_text[-600:])
        elif kind == "thinking":
            self._thinking += len(piece)
            if not self._live_text:
                self.live.setText(f"🧠 размышляет… ({self._thinking} симв.)")
        else:
            self._live_text += piece
            self.live.setText(self._live_text[-600:])
        self.live.show()

    def end_stream(self):
        self._live_text, self._thinking = "", 0
        self.live.clear()
        self.live.hide()

    def add_user(self, text: str):
        self._append(f'<p style="color:#00f0ff"><b>&gt; {html.escape(text)}</b></p>')

    def add_capri(self, text: str, route: str = "local"):
        label = ROUTE_LABEL.get(route, "")
        self._append(f'<p style="color:#ff2a6d"><b>CAPRI</b> <span style="color:#666">{label}</span></p>')
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertMarkdown(text)  # ответы Claude приходят в Markdown
        cur.insertBlock()
        self.log.setTextCursor(cur)
        if text.strip() and route != "rules":
            self._append(self.copy_links(text))
        self.log.ensureCursorVisible()


    def add_system(self, text: str):
        self._append(f'<p style="color:#888"><i>{html.escape(text)}</i></p>')

    def _append(self, html_text: str):
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        if cur.block().text():
            cur.insertBlock()
        cur.insertHtml(html_text)
        cur.insertBlock()
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()
