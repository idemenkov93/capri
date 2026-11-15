"""Значки: Fluent UI System Icons (Microsoft, лицензия MIT), 20 px, контурные. Цвет подставляется при загрузке."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

DIR = Path(__file__).parent / "icons"
_cache: dict = {}


def pixmap(name: str, color: str, size: int = 20, scale: float = 2.0) -> QPixmap:
    """Картинка значка нужного цвета. scale=2 — чтобы было чётко на экранах с масштабом 150–200 %."""
    key = (name, color, size)
    if key not in _cache:
        from PySide6.QtSvg import QSvgRenderer
        pix = QPixmap(int(size * scale), int(size * scale))
        pix.fill(Qt.transparent)
        try:
            svg = (DIR / f"{name}.svg").read_text(encoding="utf-8").replace("<path ", f'<path fill="{color}" ')
            painter = QPainter(pix)
            QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, pix.width(), pix.height()))
            painter.end()
        except OSError:
            pass                                  # нет файла — пустой значок, программа не падает
        pix.setDevicePixelRatio(scale)
        _cache[key] = pix
    return _cache[key]


def icon(name: str, color: str, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size))
    return ic


def app_icon(letter: str, accent: str, active: bool = True, bars: bool = False) -> QIcon:
    """Значок приложения семьи: тёмный скруглённый квадрат с буквой.
    bars=True — полоска «субтитров» внизу (Dolmetsch). Серый — когда приложение на паузе."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1A1A1A"))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    color = QColor(accent if active else "#8A8A8A")
    f = QFont()
    f.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans"])
    f.setPixelSize(34)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(color)
    p.drawText(QRectF(2, 0 if bars else 2, 60, 50 if bars else 60), Qt.AlignCenter, letter)
    if bars:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(14, 46, 36, 5, 2.5, 2.5)
    p.end()
    return QIcon(pix)


SIZE = QSize(20, 20)
