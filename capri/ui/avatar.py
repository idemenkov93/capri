"""Аватар на рабочем столе: прозрачное окно поверх всех, перетаскивается мышью.

Стандартная внешность — оригинальная «глитч-звезда». Свою картинку можно указать
в config.yaml (avatar.custom_image) — например, файл из папки assets/custom/,
которую git игнорирует.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMovie, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QMenu, QWidget

STATUS_COLORS = {
    "ONLINE": QColor("#00f0ff"),
    "THINKING": QColor("#ffd23f"),
    "LOADING": QColor("#8a5cff"),
    "CLAUDE": QColor("#ff7a1a"),
    "TALKING": QColor("#39ff88"),
    "ERROR": QColor("#ff0033"),
    "LISTENING": QColor("#ff3df2"),
    "RECOGNIZING": QColor("#ffd23f"),
    "SPEAKING": QColor("#39ff88"),
}


def draw_star(p: QPainter, cx: float, cy: float, r: float, color: QColor, phase: float, glitch: bool) -> None:
    """Оригинальная «глитч-звезда» — внешность Капри по умолчанию."""
    path = QPainterPath()
    spin = phase * 0.3
    for i in range(8):
        ang = spin + i * math.pi / 4
        rad = r if i % 2 == 0 else r * 0.32
        pt = QPointF(cx + rad * math.cos(ang), cy + rad * math.sin(ang))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    if glitch:  # смещённая «тень» в другом цвете
        p.setBrush(QColor(255, 42, 109, 140))
        p.setPen(Qt.NoPen)
        p.drawPath(path.translated(3, -2))
    p.setBrush(QColor(10, 10, 20, 220))
    p.setPen(QPen(color, 2.5))
    p.drawPath(path)
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(cx, cy), r * 0.12, r * 0.12)


def load_custom_frames(path: str) -> tuple[list[QPixmap], "QMovie | None"]:
    """Своя внешность: PNG, анимированный GIF или папка с кадрами."""
    if not path:
        return [], None
    pth = Path(path)
    if pth.is_dir():
        frames = [QPixmap(str(f)) for f in sorted(pth.glob("*.png"))]
        return [f for f in frames if not f.isNull()], None
    if pth.suffix.lower() == ".gif":
        movie = QMovie(str(pth))
        movie.start()
        return [], movie
    if pth.exists():
        return [QPixmap(str(pth))], None
    return [], None


class Avatar(QWidget):
    clicked = Signal()
    quit_requested = Signal()

    def __init__(self, size: int = 140, custom_image: str = "", fps: int = 12):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(size, size + 22)
        self.status = "ONLINE"
        self.phase = 0.0
        self.glitch = 0
        self._drag_from: QPoint | None = None
        self._moved = False
        self.menu_items = lambda: []  # приложение может добавить свои пункты меню
        self.frames: list[QPixmap] = []
        self.frame_i = 0
        self.movie: QMovie | None = None
        self._load_custom(custom_image, fps)

        self._tick = QTimer(self, interval=40, timeout=self._animate)
        self._tick.start()

    def _load_custom(self, path: str, fps: int) -> None:
        """Своя внешность: PNG, анимированный GIF или папка с кадрами."""
        if not path:
            return
        p = Path(path)
        if p.is_dir():
            self.frames = [QPixmap(str(f)) for f in sorted(p.glob("*.png"))]
            self.frames = [f for f in self.frames if not f.isNull()]
            if self.frames:
                QTimer(self, interval=max(1, 1000 // max(1, fps)), timeout=self._next_frame).start()
        elif p.suffix.lower() == ".gif":
            self.movie = QMovie(str(p))
            self.movie.frameChanged.connect(lambda _: self.update())
            self.movie.start()
        elif p.exists():
            self.frames = [QPixmap(str(p))]

    def _next_frame(self) -> None:
        self.frame_i = (self.frame_i + 1) % len(self.frames)

    def _current_image(self) -> QPixmap | None:
        if self.movie and self.movie.isValid():
            return self.movie.currentPixmap()
        if self.frames:
            return self.frames[self.frame_i]
        return None

    # состояние ------------------------------------------------------------------
    def set_status(self, status: str) -> None:
        self.status = status
        self.glitch = 8
        self.update()

    def _animate(self) -> None:
        self.phase += 0.08
        if self.glitch:
            self.glitch -= 1
        elif random.random() < 0.01:
            self.glitch = 4
        self.update()

    # отрисовка -----------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        color = STATUS_COLORS.get(self.status, STATUS_COLORS["ONLINE"])
        bob = math.sin(self.phase) * 4
        cx, cy = w / 2, w / 2 + bob
        r = w * 0.36
        jitter = random.uniform(-4, 4) if self.glitch else 0

        glow = QRadialGradient(QPointF(cx, cy), r * 1.4)
        glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 120))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r * 1.4, r * 1.4)

        current = self._current_image()
        if current is not None and not current.isNull():
            side = int(r * 2.4)
            img = current.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int(cx - img.width() / 2 + jitter), int(cy - img.height() / 2), img)
        else:
            self._draw_star(p, cx + jitter, cy, r, color)

        # кольцо статуса
        pen = QPen(color, 2)
        pen.setDashPattern([3, 4])
        pen.setDashOffset(self.phase * 10)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r * 1.15, r * 1.15)

        p.setPen(color)
        p.drawText(0, w + 4, w, 18, Qt.AlignCenter, self.status)

    def _draw_star(self, p: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
        draw_star(p, cx, cy, r, color, self.phase, bool(self.glitch))

    # мышь ---------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            self._moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and not self._moved:
            self.clicked.emit()
        self._drag_from = None

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("Чат", self.clicked.emit)
        for label, callback in self.menu_items():
            menu.addAction(label, callback)
        menu.addAction("Выход", self.quit_requested.emit)
        menu.exec(e.globalPos())
