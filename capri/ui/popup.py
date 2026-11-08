"""Маленькое окно у курсора: перевод, «что в буфере», ответ на письмо — прочитать и скопировать, без озвучки."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout,
                               QWidget)

STYLE = """
QWidget#popup { background: #0b1222; border: 1px solid #3ff3ff; }
QLabel { color: #eef6ff; font-family: Consolas, 'JetBrains Mono', monospace; }
QLabel#title { color: #3ff3ff; font-size: 11px; letter-spacing: 1px; }
QLabel#warn { color: #ffd23f; font-size: 11px; }
QTextBrowser, QPlainTextEdit, QLineEdit { background: #070b16; color: #eef6ff; border: 1px solid #1d3550; padding: 4px;
    font-family: Segoe UI, Arial; font-size: 13px; }
QPushButton { border: 1px solid #1d3550; color: #3ff3ff; background: #0f1a30; padding: 4px 10px; font-size: 12px; }
QPushButton:hover { border-color: #3ff3ff; }
QPushButton#close { border: none; color: #5b6b85; padding: 0 4px; }
QComboBox { background: #070b16; color: #eef6ff; border: 1px solid #1d3550; padding: 2px 6px; font-size: 12px; }
QComboBox:hover { border-color: #3ff3ff; }
QComboBox QAbstractItemView { background: #0b1222; color: #eef6ff; selection-background-color: #1d3550; }
QLabel#lang { color: #7d8db0; font-size: 11px; }
"""


class Popup(QWidget):
    """Одно окно на всё: show_busy → show_text / show_reply / ask."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("popup")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(380)
        self.setMaximumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel(objectName="title")
        close = QPushButton("✕", objectName="close")
        close.clicked.connect(self.hide)
        head.addWidget(self.title, 1)
        head.addWidget(close)
        lay.addLayout(head)
        # перевод: «С: Авто ▾ → На: Русский ▾ ⇄»
        self.lang_row = QHBoxLayout()
        self.cb_from, self.cb_to = QComboBox(), QComboBox()
        self.btn_swap = QPushButton("⇄")
        self.btn_swap.setToolTip("Поменять языки местами")
        for w in (QLabel("С:", objectName="lang"), self.cb_from, QLabel("→ На:", objectName="lang"), self.cb_to, self.btn_swap):
            self.lang_row.addWidget(w)
        self.lang_row.addStretch(1)
        lay.addLayout(self.lang_row)
        self._on_lang = None
        self.cb_from.activated.connect(lambda _: self._lang_changed())
        self.cb_to.activated.connect(lambda _: self._lang_changed())
        self.btn_swap.clicked.connect(self._swap)
        self.body = QTextBrowser(openExternalLinks=True)
        self.body.setMaximumHeight(320)
        lay.addWidget(self.body)
        self.warn = QLabel(objectName="warn", wordWrap=True)
        lay.addWidget(self.warn)
        # ответ на письмо: тема и текст — каждое со своей кнопкой «копировать»
        self.subject = QLineEdit()
        self.reply = QPlainTextEdit()
        self.reply.setMinimumHeight(180)
        self.subj_row = self._row(self.subject, lambda: self.copy(self.subject.text(), "Тема скопирована."))
        self.body_row = self._row(self.reply, lambda: self.copy(self.reply.toPlainText(), "Текст скопирован."))
        lay.addLayout(self.subj_row)
        lay.addLayout(self.body_row)
        self.input = QLineEdit()
        lay.addWidget(self.input)
        self.buttons = QHBoxLayout()
        lay.addLayout(self.buttons)
        self.status = QLabel(objectName="title")
        lay.addWidget(self.status)
        QShortcut(QKeySequence("Esc"), self, self.hide)
        self._reset()

    @staticmethod
    def _row(w, copy_fn) -> QVBoxLayout:
        col = QVBoxLayout()
        top = QHBoxLayout()
        lbl = QLabel("Тема:" if isinstance(w, QLineEdit) else "Текст письма:")
        b = QPushButton("📋")
        b.setToolTip("Копировать")
        b.clicked.connect(copy_fn)
        top.addWidget(lbl, 1)
        top.addWidget(b)
        col.addLayout(top)
        col.addWidget(w)
        return col

    def _set_row_visible(self, row: QVBoxLayout, on: bool):
        for i in range(row.count()):
            it = row.itemAt(i)
            if it.widget():
                it.widget().setVisible(on)
            elif it.layout():
                self._set_row_visible(it.layout(), on)

    def _reset(self):
        self.text_value = ""
        self.warn.hide()
        self.input.hide()
        self.status.setText("")
        self._set_row_visible(self.subj_row, False)
        self._set_row_visible(self.body_row, False)
        self._set_row_visible(self.lang_row, False)
        self._on_lang = None
        self.body.show()
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w:
                w.deleteLater()

    # ------------------------------------------------------------------ показать
    def _place(self, activate: bool = False):
        self.adjustSize()
        pos = QCursor.pos() + QPoint(16, 16)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        x = min(max(pos.x(), g.left()), g.right() - self.width())
        y = min(max(pos.y(), g.top()), g.bottom() - self.height())
        appear = not self.isVisible()
        if appear:
            self.move(x, y)
        self.show()
        self.raise_()
        if activate:
            self.activateWindow()
        from .anim import fade_in, keep_on_screen
        keep_on_screen(self)
        QTimer.singleShot(0, self._refit)                      # после того как Qt досчитал ширину
        if appear:
            fade_in(self)

    def _refit(self):
        from .anim import keep_on_screen
        if self.body.isVisible():
            self._fit_body()
            self.adjustSize()
        keep_on_screen(self)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isVisible():
            from .anim import keep_on_screen
            keep_on_screen(self)                 # окно выросло (длинный ответ) — не уезжает за низ экрана

    def show_busy(self, title: str, what: str = "Думаю…"):
        self._reset()
        self.title.setText(title.upper())
        self.body.setPlainText(what)
        self._fit_body()
        self._place()

    def _fit_body(self):
        """Высота текста — по содержимому (короткий ответ — маленькое окно), но не выше 320."""
        doc = self.body.document()
        w = self.body.viewport().width() if self.body.isVisible() and self.body.viewport().width() > 100 else 340
        doc.setTextWidth(w - 4)
        self.body.setFixedHeight(int(min(360, max(48, doc.size().height() + 20))))

    def show_text(self, title: str, text: str, actions: list | None = None, warn: str = "", markdown: bool = True,
                  copy: bool = True, copy_text: str | None = None):
        """text — что показать; copy_text — что копировать по «📋» (по умолчанию — text)."""
        self._reset()
        self.title.setText(title.upper())
        self.text_value = copy_text if copy_text is not None else text
        if markdown:
            self.body.setMarkdown(text)
        else:
            self.body.setPlainText(text)
        self._fit_body()
        if warn:
            self.warn.setText("⚠ " + warn)
            self.warn.show()
        if copy:
            self.add_button("📋 Копировать", lambda: self.copy(self.text_value))
        for label, fn in actions or []:
            self.add_button(label, fn)
        self.buttons.addStretch(1)
        self._place()

    def show_translation(self, text: str, src: str, tgt: str, langs: dict, on_change):
        """Перевод + выбор языков. on_change(с, на) — перевести заново («auto» — определить самой)."""
        names = {k: v.capitalize() for k, v in langs.items()}
        self.show_text("Перевод", text, markdown=False)
        for cb, items, cur in ((self.cb_from, {"auto": "Авто" + (f" ({names.get(src, src)})" if src else ""), **names}, "auto"),
                               (self.cb_to, names, tgt)):
            cb.blockSignals(True)
            cb.clear()
            for code, label in items.items():
                cb.addItem(label, code)
            cb.setCurrentIndex(max(0, cb.findData(cur)))
            cb.blockSignals(False)
        self._detected = src
        self._set_row_visible(self.lang_row, True)
        self._on_lang = on_change
        self._place()

    def _lang_changed(self):
        if self._on_lang:
            self._on_lang(self.cb_from.currentData(), self.cb_to.currentData())

    def _swap(self):
        src = self.cb_from.currentData()
        src = self._detected if src == "auto" else src
        tgt = self.cb_to.currentData()
        if not src or not self._on_lang:
            return
        self._on_lang(tgt, src)

    def show_reply(self, subject: str, body: str, attach: list[str]):
        self._reset()
        self.title.setText("ОТВЕТ НА ПИСЬМО — ТЕКСТ УЖЕ В БУФЕРЕ")
        self.body.hide()
        self.subject.setText(subject)
        self.reply.setPlainText(body)
        self._set_row_visible(self.subj_row, True)
        self._set_row_visible(self.body_row, True)
        if attach:
            self.warn.setText("📎 Приложи: " + ", ".join(attach))
            self.warn.show()
        self.add_button("📋 Тема + текст", lambda: self.copy(f"{self.subject.text()}\n\n{self.reply.toPlainText()}"))
        self.buttons.addStretch(1)
        QGuiApplication.clipboard().setText(body)
        self._place()

    def ask(self, title: str, placeholder: str, on_text):
        """Строка ввода: «Что ответить? (например: согласен на четверг)»."""
        self.status.setText(title)
        self.input.clear()
        self.input.setPlaceholderText(placeholder)
        self.input.show()
        try:
            self.input.returnPressed.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.input.returnPressed.connect(lambda: on_text(self.input.text().strip()))
        self._place(activate=True)
        self.input.setFocus()

    def add_button(self, label: str, fn):
        b = QPushButton(label)
        b.clicked.connect(fn)
        self.buttons.addWidget(b)
        return b

    def copy(self, text: str, note: str = "Скопировано."):
        QGuiApplication.clipboard().setText(text or "")
        self.status.setText(note)
