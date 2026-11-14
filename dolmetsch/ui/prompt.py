"""Уведомление с кнопками: «Субтитров Teams нет. Включить субтитры Windows?» — Да / Нет / Как включить в Teams."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import style
from .overlay import FLAGS


class AskBar(QWidget):
    answered = Signal(bool)
    how = Signal()

    def __init__(self):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        card = QFrame(self)
        card.setObjectName("card")
        r, g, b = style.CARD
        card.setStyleSheet(
            f"#card {{ background: rgba({r},{g},{b},240); border: 1px solid rgba(255,255,255,40); border-radius: 12px; }}"
            f"QLabel {{ color: {style.INK}; font: 11pt 'Segoe UI'; }}"
            f"QPushButton {{ color: {style.INK}; background: rgba(255,255,255,26); border: none; border-radius: 8px;"
            f" padding: 6px 16px; font: 10pt 'Segoe UI'; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,56); }}"
            f"QPushButton#yes {{ background: {style.ACCENT}; color: #0f1219; font-weight: 600; }}")
        self.label = QLabel("Субтитров Teams нет. Включить субтитры Windows?", card)
        yes, no, how = QPushButton("Да", card), QPushButton("Нет", card), QPushButton("Как включить в Teams", card)
        yes.setObjectName("yes")
        for button in (yes, no, how):
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
        yes.clicked.connect(lambda: self._done(True))
        no.clicked.connect(lambda: self._done(False))
        how.clicked.connect(self.how.emit)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(8)
        row.addWidget(self.label, 1)
        for button in (yes, no, how):
            row.addWidget(button)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    def ask(self, above: QWidget, teams_running: bool) -> None:
        """Показать над окном перевода."""
        self.label.setText("Субтитров Teams нет. Включить субтитры Windows?" if teams_running
                           else "Встреча не в Teams? Включить субтитры Windows?")
        self.adjustSize()
        g = above.geometry()
        self.move(g.x() + (g.width() - self.width()) // 2, max(0, g.y() - self.height() - 8))
        self.show()

    def _done(self, yes: bool) -> None:
        self.hide()
        self.answered.emit(yes)
