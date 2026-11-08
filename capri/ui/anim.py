"""Плавное появление окон: прозрачность 0 → 1 за 140 мс и лёгкий подъезд на 10 px. Не блокирует —
анимация идёт сама, окно сразу работает. Выключить: переменная окружения CAPRI_NO_ANIM=1."""
from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect
from PySide6.QtGui import QGuiApplication

MS = 140


def fade_in(w, slide: int = 10):
    if os.environ.get("CAPRI_NO_ANIM"):
        w.setWindowOpacity(1.0)
        return
    old = getattr(w, "_capri_anims", None)
    if old:
        for a in old:
            a.stop()
    op = QPropertyAnimation(w, b"windowOpacity", w)
    op.setDuration(MS)
    op.setStartValue(0.0)
    op.setEndValue(1.0)
    op.setEasingCurve(QEasingCurve.OutCubic)
    anims = [op]
    if slide:
        end = w.pos()
        mv = QPropertyAnimation(w, b"pos", w)
        mv.setDuration(MS + 40)
        mv.setStartValue(end + QPoint(0, slide))
        mv.setEndValue(end)
        mv.setEasingCurve(QEasingCurve.OutCubic)
        anims.append(mv)
    w.setWindowOpacity(0.0)
    for a in anims:
        a.start()
    w._capri_anims = anims


def keep_on_screen(w, margin: int = 8):
    """Окно целиком на экране (тот, где оно сейчас); выросло вниз за край — подвинуть вверх."""
    g = w.frameGeometry()
    scr = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
    a = scr.availableGeometry().adjusted(margin, margin, -margin, -margin)
    r = QRect(g)
    if r.height() > a.height():
        w.resize(w.width(), a.height() - (g.height() - w.height()))
        r = w.frameGeometry()
    x = min(max(r.left(), a.left()), a.right() - r.width())
    y = min(max(r.top(), a.top()), a.bottom() - r.height())
    if (x, y) != (r.left(), r.top()):
        w.move(x, y)
