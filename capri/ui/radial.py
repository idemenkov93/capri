"""Круговое меню в стиле Капри: кольцо секторов вокруг курсора, в центре — сколько файлов / «назад».

Мышь: навёл — подсветка и подсказка снизу, клик — выбрать; сектор с ▸ открывает своё кольцо.
Клавиатура: 1…8 — сектор, Esc — назад / закрыть. Клик мимо или переключился в другое окно — закрылось.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

PINK, CYAN, BG, LINE, TEXT, DIM = "#ff2a6d", "#3ff3ff", "#0b1222", "#1d3550", "#eef6ff", "#5b6b85"


class RadialMenu(QWidget):
    picked = Signal(str)            # code выбранного пункта
    closed = Signal()

    SIZE, R_OUT, R_IN, HINT_H = 360, 160, 56, 46

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.resize(self.SIZE, self.SIZE + self.HINT_H)
        self.stack: list[tuple[str, list]] = []          # (заголовок в центре, пункты) — вложенные кольца
        self.hover = -1
        self.k = 1.0
        self._anim = QVariantAnimation(self, startValue=0.82, endValue=1.0, duration=140,
                                       easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._zoom)

    # ------------------------------------------------------------------ открыть
    def show_at(self, items: list, title: str, pos=None):
        self.stack = [(title, items)]
        self.hover = -1
        pos = pos or QCursor.pos()
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        x = min(max(pos.x() - self.SIZE // 2, g.left()), g.right() - self.width())
        y = min(max(pos.y() - self.SIZE // 2, g.top()), g.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._anim.start()

    def _zoom(self, v):
        self.k = float(v)
        self.setWindowOpacity(min(1.0, (self.k - 0.82) / 0.18 * 0.9 + 0.1))
        self.update()

    @property
    def items(self) -> list:
        return self.stack[-1][1] if self.stack else []

    # ------------------------------------------------------------------ геометрия
    def center(self) -> QPointF:
        return QPointF(self.SIZE / 2, self.SIZE / 2)

    def span(self) -> float:
        return 360.0 / max(1, len(self.items))

    def index_at(self, p: QPointF) -> int:
        """-2 — центр, -1 — мимо кольца, 0…n-1 — сектор (нулевой — сверху, дальше по часовой)."""
        c = self.center()
        dx, dy = p.x() - c.x(), p.y() - c.y()
        d = math.hypot(dx, dy)
        if d <= self.R_IN * self.k:
            return -2
        if d > self.R_OUT * self.k + 4:
            return -1
        ang = (math.degrees(math.atan2(dx, -dy)) + self.span() / 2) % 360      # 0° — вверх, по часовой
        return int(ang // self.span()) if self.items else -1

    def sector_center(self, i: int) -> QPointF:
        a = math.radians(i * self.span())
        r = (self.R_IN + self.R_OUT) / 2 * self.k
        c = self.center()
        return QPointF(c.x() + r * math.sin(a), c.y() - r * math.cos(a))

    # ------------------------------------------------------------------ мышь и клавиши
    def mouseMoveEvent(self, e):
        i = self.index_at(e.position())
        if i != self.hover:
            self.hover = i
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            return self.back()
        i = self.index_at(e.position())
        if i == -2:
            return self.back()
        if i == -1:
            return self.close_menu()
        self.choose(i)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Backspace):
            return self.back()
        if Qt.Key_1 <= e.key() <= Qt.Key_9:
            i = e.key() - Qt.Key_1
            if i < len(self.items):
                self.choose(i)

    def choose(self, i: int):
        it = self.items[i]
        if it.children:
            self.stack.append((it.label, it.children))
            self.hover = -1
            self._anim.start()
            return
        self.hide()
        self.stack = []
        self.picked.emit(it.code)

    def back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.hover = -1
            self.update()
        else:
            self.close_menu()

    def close_menu(self):
        if self.isVisible():
            self.hide()
            self.stack = []
            self.closed.emit()

    def changeEvent(self, e):
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.close_menu()            # кликнул в другое окно — закрываемся
        super().changeEvent(e)

    # ------------------------------------------------------------------ рисуем
    def paintEvent(self, _):
        if not self.stack:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c, k = self.center(), self.k
        ro, ri = self.R_OUT * k, self.R_IN * k
        # мягкое свечение за кольцом
        glow = QRadialGradient(c, ro + 18)
        glow.setColorAt(0.75, QColor(63, 243, 255, 40))
        glow.setColorAt(1.0, QColor(63, 243, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, ro + 18, ro + 18)
        n = len(self.items)
        span = self.span()
        outer = QRectF(c.x() - ro, c.y() - ro, 2 * ro, 2 * ro)
        inner = QRectF(c.x() - ri, c.y() - ri, 2 * ri, 2 * ri)
        for i, it in enumerate(self.items):
            # Qt: 0° — справа, против часовой; наш 0 — сверху, по часовой
            start = 90 - (i * span - span / 2) - (1.2 if n > 1 else 0)
            sweep = -(span - (2.4 if n > 1 else 0))
            path = QPainterPath()
            path.arcMoveTo(outer, start)
            path.arcTo(outer, start, sweep)
            path.arcTo(inner, start + sweep, -sweep)
            path.closeSubpath()
            hot = i == self.hover
            p.setBrush(QColor(58, 12, 38, 245) if hot else QColor(11, 18, 34, 235))
            p.setPen(QPen(QColor(PINK if hot else LINE), 1.6 if hot else 1))
            p.drawPath(path)
            pt = self.sector_center(i)
            p.setPen(QColor(PINK if hot else CYAN))
            f = QFont("Segoe UI Emoji", int(15 * k))
            p.setFont(f)
            icon = it.icon or str(i + 1)
            p.drawText(QRectF(pt.x() - 40, pt.y() - 30, 80, 26), Qt.AlignCenter, icon)
            p.setPen(QColor(TEXT if hot else "#c9d6ea"))
            f = QFont("Segoe UI", max(7, int(8.5 * k)))
            f.setBold(hot)
            p.setFont(f)
            label = it.label + (" ▸" if it.children else "")
            p.drawText(QRectF(pt.x() - 46, pt.y() - 3, 92, 30), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, label)
        # центр
        center_hot = self.hover == -2
        p.setBrush(QColor(7, 11, 22, 245))
        p.setPen(QPen(QColor(PINK if center_hot else CYAN), 1.4))
        p.drawEllipse(c, ri - 4, ri - 4)
        title = self.stack[-1][0]
        p.setPen(QColor(TEXT))
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(c.x() - ri + 8, c.y() - 22, 2 * ri - 16, 26), Qt.AlignCenter | Qt.TextWordWrap, title)
        p.setPen(QColor(PINK if center_hot else DIM))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(QRectF(c.x() - ri, c.y() + 6, 2 * ri, 16), Qt.AlignCenter,
                   "← назад" if len(self.stack) > 1 else "Esc — закрыть")
        # подсказка снизу
        if 0 <= self.hover < n and (hint := self.items[self.hover].hint):
            box = QRectF(10, self.SIZE + 2, self.SIZE - 20, self.HINT_H - 8)
            p.setBrush(QColor(11, 18, 34, 235))
            p.setPen(QPen(QColor(LINE), 1))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor("#9fb3cf"))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(box.adjusted(8, 2, -8, -2), Qt.AlignCenter | Qt.TextWordWrap, hint)
        p.end()
