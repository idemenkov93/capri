"""Внешний вид: цвета и значок. Всё рисуется кодом — картинок в проекте нет."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

INK = "#f1f5f9"          # основной текст (перевод)
MUTED = "#94a3b8"        # оригинал и недописанная фраза
ACCENT = "#7dd3fc"       # имя говорящего
OK, WARN, BAD = "#4ade80", "#fbbf24", "#f87171"
CARD = (15, 18, 25)      # фон окна (r, g, b); прозрачность задаётся настройкой


def icon(active: bool = True) -> QIcon:
    """Значок: тёмный квадрат с буквой D; серый, когда перевод на паузе."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(*CARD))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor(ACCENT if active else MUTED))
    font = QFont("Segoe UI", 30)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, "D")
    p.end()
    return QIcon(pix)
