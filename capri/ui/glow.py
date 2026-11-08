"""«Капри без персонажа»: светящаяся полоса по краю экрана, как у Siri на iPhone.

Окно на весь экран, но прозрачное и «сквозное»: мышь и клавиатура проходят насквозь, фокус не берёт, окна не закрывает.
Видно только, когда Капри что-то делает: слушает (розовое), думает (жёлтое), отвечает (зелёное). В покое — пусто.
Анимация идёт только пока свечение видно, иначе таймер стоит — ноль нагрузки.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

PALETTE = ("#3ff3ff", "#8a5cff", "#ff3df2", "#ff7a1a", "#ffd23f", "#3ff3ff")
TINT = {"LISTENING": "#ff3df2", "RECOGNIZING": "#ffd23f", "THINKING": "#ffd23f", "CLAUDE": "#ff7a1a",
        "SPEAKING": "#39ff88", "TALKING": "#39ff88", "ERROR": "#ff0033"}
ACTIVE = set(TINT)


class EdgeGlow(QWidget):
    FPS = 30

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.angle = 0.0
        self.alpha = 0.0           # 0…1 — плавно появляется и гаснет
        self.target = 0.0
        self.level = 0.0           # громкость голоса — полоса «дышит»
        self.tint = QColor(PALETTE[0])
        self.suspended = False     # в игре — не рисуем вообще (окно поверх игры отнимает кадры)
        self._timer = QTimer(self, interval=int(1000 / self.FPS), timeout=self._step)

    def _fit(self):
        scr = QGuiApplication.primaryScreen()
        if scr:
            self.setGeometry(scr.geometry())

    def set_status(self, status: str):
        on = status in ACTIVE and not self.suspended
        if status in TINT:
            self.tint = QColor(TINT[status])
        self.target = 1.0 if on else 0.0
        if on and not self.isVisible():
            self._fit()
            self.show()
        if on or self.alpha > 0:
            self._timer.start()

    def set_level(self, level: float):
        self.level = max(0.0, min(1.0, level))

    def suspend(self, on: bool):
        self.suspended = on
        if on:
            self.target = self.alpha = 0.0
            self._timer.stop()
            self.hide()

    def _step(self):
        self.angle = (self.angle + 2.4) % 360
        self.alpha += (self.target - self.alpha) * 0.12
        if self.target == 0 and self.alpha < 0.02:
            self.alpha = 0.0
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, _):
        if self.alpha <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        g = QConicalGradient(r.center(), self.angle)
        n = len(PALETTE)
        for i, c in enumerate(PALETTE):
            col = QColor(c)
            # цвет состояния (слушаю / думаю / говорю) — сильнее в одной половине круга
            mix = 0.55 * (0.5 + 0.5 * math.cos(2 * math.pi * i / (n - 1)))
            col = QColor(int(col.red() * (1 - mix) + self.tint.red() * mix),
                         int(col.green() * (1 - mix) + self.tint.green() * mix),
                         int(col.blue() * (1 - mix) + self.tint.blue() * mix))
            g.setColorAt(i / (n - 1), col)
        breath = 1.0 + 0.6 * self.level
        # несколько обводок — от широкой и прозрачной к узкой и яркой: получается мягкое свечение
        # много тонких слоёв с плавно падающей яркостью — свечение мягкое, без ступенек
        layers = [(3 + i * 3.2, 0.5 * (1 - i / 12) ** 2.4) for i in range(12)]
        for width, a in reversed(layers):
            w = width * breath
            pen = QPen(g, w)
            p.setOpacity(a * self.alpha)
            p.setPen(pen)
            path = QPainterPath()
            inset = w / 2
            path.addRoundedRect(r.adjusted(inset, inset, -inset, -inset), 18, 18)
            p.drawPath(path)
        p.end()

    def preview_point(self) -> QPointF:          # для тестов: где центр градиента
        return QRectF(self.rect()).center()
