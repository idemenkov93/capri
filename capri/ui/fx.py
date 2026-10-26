"""Глитч-эффекты для спрайта Капри: нарезка, RGB-расслоение, рассыпание на пиксели, сканлайн.

k — сила эффекта от 0 (обычная картинка) до 1 (максимум, момент телепорта).
"""
from __future__ import annotations

import random

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPixmap


def tinted(img: QPixmap, color: QColor) -> QPixmap:
    """Силуэт картинки, залитый одним цветом (для RGB-расслоения)."""
    out = QPixmap(img.size())
    out.fill(QColor(0, 0, 0, 0))
    q = QPainter(out)
    q.drawPixmap(0, 0, img)
    q.setCompositionMode(QPainter.CompositionMode_SourceIn)
    q.fillRect(out.rect(), color)
    q.end()
    return out


def paint_effect(p: QPainter, img: QPixmap, x: float, y: float, style: str, k: float, color: QColor, seed: int) -> None:
    rnd = random.Random(seed)
    w, h = img.width(), img.height()
    if style == "slices":
        # горизонтальные полосы разъезжаются в стороны
        bands = 9
        bh = h / bands
        for i in range(bands):
            dx = (rnd.random() * 2 - 1) * 46 * k
            src = QRectF(0, i * bh, w, bh + 1)
            p.setOpacity(max(0.15, 1 - 0.7 * k * rnd.random()))
            p.drawPixmap(QRectF(x + dx, y + i * bh, w, bh + 1), img, src)
        p.setOpacity(1.0)
        if k > 0.6:  # яркая полоса-«сбой»
            band = rnd.randrange(bands)
            p.fillRect(QRectF(x - 10, y + band * bh, w + 20, 3), QColor(color.red(), color.green(), color.blue(), 220))
    elif style == "rgb":
        off = 4 + 26 * k
        p.setOpacity(0.55)
        p.drawPixmap(int(x - off), int(y), tinted(img, QColor(255, 30, 80)))
        p.drawPixmap(int(x + off), int(y + off * 0.2), tinted(img, QColor(0, 240, 255)))
        p.setOpacity(max(0.1, 1 - k))
        p.drawPixmap(int(x), int(y), img)
        p.setOpacity(1.0)
    elif style == "dissolve":
        # рассыпается на квадраты: чем сильнее эффект, тем больше пустых клеток и разлёт
        cell = 10
        for cy in range(0, h, cell):
            for cx in range(0, w, cell):
                if rnd.random() < k * 0.9:
                    continue
                spread = k * 18
                dx, dy = rnd.uniform(-spread, spread), rnd.uniform(-spread, spread) - k * 12
                p.drawPixmap(QRectF(x + cx + dx, y + cy + dy, cell, cell), img, QRectF(cx, cy, cell, cell))
    elif style == "scan":
        # сканлайн стирает её сверху вниз (и рисует обратно на новом месте)
        visible = h * (1 - k)
        p.drawPixmap(QRectF(x, y + h - visible, w, visible), img, QRectF(0, h - visible, w, visible))
        line_y = y + h - visible
        p.fillRect(QRectF(x - 14, line_y - 2, w + 28, 4), QColor(color.red(), color.green(), color.blue(), 240))
        p.setOpacity(0.25)
        for i in range(0, int(h - visible), 6):   # «остаточные» строчки
            if rnd.random() < 0.5:
                p.fillRect(QRectF(x, y + i, w, 1), color)
        p.setOpacity(1.0)
    else:
        p.drawPixmap(int(x), int(y), img)
