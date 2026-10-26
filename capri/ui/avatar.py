"""Аватар на рабочем столе: прозрачное окно поверх всех.

Капри «живёт» на экране: гуляет по низу экрана, телепортируется на верхние края окон
и сидит на них (окно двигается — она едет вместе с ним; окно закрыли — падает вниз).
Её можно взять мышью и бросить на любое окно.

Внешность: оригинальная «глитч-звезда» или свои GIF/PNG из assets/custom (см. sprites.py).
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMovie, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtGui import QGuiApplication, QTransform
from PySide6.QtWidgets import QMenu, QWidget

from . import desktop
from .sprites import SpriteSet

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

    SINK = 6          # насколько «проваливается» в край окна, чтобы сидеть, а не парить
    SPEED = 2.2       # пикселей за кадр при ходьбе
    GRAVITY = 1.3

    def __init__(self, size: int = 140, custom_image: str = "", fps: int = 12, wander: bool = True,
                 holo: bool = False):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # holo — клипы с фоном показываются как маленькая голограмма-экран, которая гуляет по рабочему столу
        self.holo = holo
        self.resize(int(size * 1.6), int(size * 1.0)) if holo else self.resize(size, size)
        self.status = "ONLINE"
        self.phase = 0.0
        self.glitch = 0
        self.menu_items = lambda: []  # приложение может добавить свои пункты меню
        self.menu_builder = None      # или построить всё меню само (общее с треем)
        self.sprites = SpriteSet(custom_image, fps)

        # поведение на рабочем столе
        self.wander = wander
        self.mode = "ground"          # ground | perch | fall | drag | teleport
        self.perch_hwnd: int | None = None
        self.perch_dx = 0.0           # смещение от левого края окна, на котором сидит
        self.walk_to: float | None = None
        self.facing = 1
        self.vy = 0.0
        self.next_decision = time.monotonic() + 6
        self._tp_target: tuple[str, int | None, float, float] | None = None
        self._drag_from: QPoint | None = None
        self._moved = False

        self._tick = QTimer(self, interval=33, timeout=self._animate)
        self._tick.start()

    # геометрия ----------------------------------------------------------------
    def screen_rect(self) -> QRect:
        scr = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        return scr.availableGeometry()

    def ground_y(self) -> int:
        return self.screen_rect().bottom() - self.height() + 1

    def place_on_ground(self, x: float | None = None):
        scr = self.screen_rect()
        x = scr.right() - self.width() - 40 if x is None else x
        self.move(int(max(scr.left(), min(x, scr.right() - self.width()))), self.ground_y())
        self.mode = "ground"

    # состояние ------------------------------------------------------------------
    def set_status(self, status: str) -> None:
        if status != self.status:
            self.glitch = 8
        self.status = status
        self.update()

    def busy(self) -> bool:
        return self.status not in ("ONLINE", "LOADING")

    # жизнь на экране --------------------------------------------------------
    def _animate(self) -> None:
        self.phase += 0.08
        if self.glitch:
            self.glitch -= 1
        elif random.random() < 0.006:
            self.glitch = 4

        if self.mode == "perch":
            self._follow_window()
        elif self.mode == "fall":
            self._fall()
        elif self.mode == "teleport":
            self._teleport_step()

        if self.walk_to is not None and self.mode in ("ground", "perch"):
            self._walk_step()

        if (self.wander and self.mode in ("ground", "perch") and self.walk_to is None
                and not self.busy() and time.monotonic() > self.next_decision):
            self._decide()
        self.update()

    def _follow_window(self):
        rect = desktop.window_rect(self.perch_hwnd) if self.perch_hwnd else None
        if rect is None:                      # окно закрыли или свернули — падаем
            self.mode, self.vy, self.perch_hwnd = "fall", 0.0, None
            return
        self.perch_dx = max(0.0, min(self.perch_dx, rect.width() - self.width()))
        self.move(int(rect.left() + self.perch_dx), rect.top() - self.height() + self.SINK)

    def _fall(self):
        self.vy = min(self.vy + self.GRAVITY, 28)
        y = self.y() + self.vy
        if y >= self.ground_y():
            self.place_on_ground(self.x())
            self.glitch = 6
        else:
            self.move(self.x(), int(y))

    def _walk_step(self):
        target = self.walk_to
        dx = target - (self.perch_dx if self.mode == "perch" else self.x())
        if abs(dx) <= self.SPEED:
            self.walk_to = None
            return
        step = self.SPEED if dx > 0 else -self.SPEED
        self.facing = 1 if step > 0 else -1
        if self.mode == "perch":
            self.perch_dx += step
        else:
            self.move(int(self.x() + step), self.ground_y())

    def _decide(self):
        """Раз в несколько секунд решаем, чем заняться: пройтись, перепрыгнуть на окно или спуститься вниз."""
        self.next_decision = time.monotonic() + random.uniform(5, 12)
        self.sprites.shuffle("idle")
        roll = random.random()
        scr = self.screen_rect()
        walk_chance = 0.0 if self.holo else 0.35          # голограмма не «плывёт», а прыгает
        if roll < walk_chance:                             # прогуляться по текущей поверхности
            if self.mode == "perch":
                rect = desktop.window_rect(self.perch_hwnd)
                if rect:
                    self.walk_to = random.uniform(0, max(0, rect.width() - self.width()))
            else:
                dist = random.uniform(150, 400) * random.choice((-1, 1))
                self.walk_to = max(scr.left(), min(self.x() + dist, scr.right() - self.width()))
        elif roll < 0.9:                                   # телепорт на верхний край другого окна
            spots = [w for w in desktop.perchable(desktop.list_windows(), scr, self.height()) if w.hwnd != self.perch_hwnd]
            if spots:
                w = random.choice(spots)
                dx = random.uniform(20, max(20, w.rect.width() - self.width() - 20))
                self._start_teleport(("perch", w.hwnd, dx, 0))
            else:                                          # окон, куда сесть, нет — прыжок в другое место внизу
                self._start_teleport(("ground", None, random.uniform(scr.left(), scr.right() - self.width()), 0))
        elif self.mode == "perch":                         # спрыгнуть вниз
            self.mode, self.vy, self.perch_hwnd = "fall", -6.0, None

    def _start_teleport(self, target):
        self._tp_target = target
        self.mode = "teleport"
        self._tp_frames = 24
        self.glitch = 24

    def _teleport_step(self):
        self._tp_frames -= 1
        if self._tp_frames == 12 and self._tp_target:     # середина глитча — переносимся
            kind, hwnd, dx, _ = self._tp_target
            if kind == "perch":
                self.perch_hwnd, self.perch_dx = hwnd, dx
                self.mode = "perch"
                self._follow_window()
            else:
                self.perch_hwnd = None
                self.place_on_ground(dx)
            self.mode = "teleport"
        if self._tp_frames <= 0:
            self.mode = "perch" if self.perch_hwnd else "ground"
            self._tp_target = None

    def _land_after_drop(self):
        """Бросили мышью: если под ногами верхний край окна — садимся на него, иначе падаем вниз."""
        feet = QPoint(self.x() + self.width() // 2, self.y() + self.height())
        for w in desktop.list_windows():
            r = w.rect
            if r.left() <= feet.x() <= r.right() and abs(feet.y() - r.top()) < 40:
                self.perch_hwnd, self.perch_dx = w.hwnd, self.x() - r.left()
                self.mode = "perch"
                self.glitch = 6
                return
            if r.contains(feet):
                break  # бросили на середину окна — не цепляемся
        self.mode, self.vy, self.perch_hwnd = "fall", 0.0, None

    # отрисовка -----------------------------------------------------------------
    def sprite_kind(self) -> str:
        if self.walk_to is not None:
            return "walk"
        if self.mode == "teleport":
            return "teleport"
        if self.status in ("THINKING", "CLAUDE", "RECOGNIZING", "LOADING"):
            return "think"
        if self.status in ("SPEAKING", "TALKING"):
            return "talk"
        if self.status == "LISTENING":
            return "listen"
        if self.mode == "perch":
            return "sit"
        return "idle"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        color = STATUS_COLORS.get(self.status, STATUS_COLORS["ONLINE"])
        jitter = random.uniform(-5, 5) if self.glitch else 0
        if self.mode == "teleport":
            # 24→12: схлопывание в светящуюся линию; 12→0: разворачивание на новом месте
            t = self._tp_frames
            k = (t - 12) / 12 if t > 12 else (12 - t) / 12      # 1 → 0 → 1
            k = max(0.0, min(1.0, 1 - k))                         # 0 → 1 → 0 (сжатие)
            sy = max(0.04, 1 - k)
            sx = 1 + 0.25 * k
            p.translate(w / 2, h / 2)
            p.scale(sx, sy)
            p.translate(-w / 2, -h / 2)
            if k > 0.75:  # вспышка в момент переноса
                p.fillRect(QRectF(0, h / 2 - 3, w, 6), QColor(color.red(), color.green(), color.blue(), 230))
        if self.holo:
            self._paint_holo(p, w, h, color, jitter)
            return

        sprite = self.sprites.get(self.sprite_kind()) if self.sprites else None
        if sprite is not None:
            img = sprite.frame()
            if not img.isNull():
                img = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if self.facing < 0:
                    img = img.transformed(QTransform().scale(-1, 1))
                x, y = (w - img.width()) / 2 + jitter, h - img.height()    # ногами вниз
                if self.walk_to is not None and "walk" not in self.sprites.kinds():
                    y -= abs(math.sin(self.phase * 4)) * 6           # нет анимации ходьбы — хотя бы «шагает»
                if self.glitch:   # цветные «тени» глитча
                    p.setOpacity(0.35)
                    p.drawPixmap(int(x - 4), int(y), img)
                    p.drawPixmap(int(x + 4), int(y - 1), img)
                    p.setOpacity(1.0)
                p.drawPixmap(int(x), int(y), img)
        else:
            bob = math.sin(self.phase) * 4 if self.mode != "perch" else 0
            r = w * 0.32
            cx, cy = w / 2 + jitter, h - r * 1.25 + bob
            glow = QRadialGradient(QPointF(cx, cy), r * 1.4)
            glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 120))
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), r * 1.4, r * 1.4)
            draw_star(p, cx, cy, r, color, self.phase, bool(self.glitch))

        if self.status not in ("ONLINE",):          # маленький индикатор статуса
            p.setOpacity(1.0)
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(w - 10, 10), 5, 5)

    def _paint_holo(self, p: QPainter, w: int, h: int, color: QColor, jitter: float):
        """Голограмма: клип в рамке со скошенными углами, сканлайны, уголки, глитч и «проектор» снизу."""
        sprite = self.sprites.get(self.sprite_kind()) if self.sprites else None
        frame = QRectF(4, 4, w - 8, h - 16)
        cut = 10
        path = QPainterPath()
        path.moveTo(frame.left() + cut, frame.top())
        path.lineTo(frame.right(), frame.top())
        path.lineTo(frame.right(), frame.bottom() - cut)
        path.lineTo(frame.right() - cut, frame.bottom())
        path.lineTo(frame.left(), frame.bottom())
        path.lineTo(frame.left(), frame.top() + cut)
        path.closeSubpath()

        p.save()
        p.setClipPath(path)
        p.fillRect(frame, QColor("#05070f"))
        if sprite is not None and not sprite.frame().isNull():
            img = sprite.frame().scaled(int(frame.width()), int(frame.height()),
                                        Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = frame.left() + (frame.width() - img.width()) / 2 + jitter
            y = frame.top() + (frame.height() - img.height()) / 2
            p.setOpacity(p.opacity() * 0.92)
            p.drawPixmap(int(x), int(y), img)
            if self.glitch:  # сдвинутые полосы
                for _ in range(4):
                    hh = random.randint(3, 10)
                    yy = random.uniform(frame.top(), frame.bottom() - hh)
                    p.drawPixmap(int(x + random.randint(-14, 14)), int(yy), img, 0, int(yy - y), img.width(), hh)
        p.setOpacity(1.0)
        p.setPen(QColor(255, 255, 255, 14))
        for yy in range(int(frame.top()), int(frame.bottom()), 3):
            p.drawLine(int(frame.left()), yy, int(frame.right()), yy)
        p.restore()

        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 160), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(color, 2))
        L = 12
        for (x, y, dx, dy) in ((frame.left(), frame.top(), 1, 1), (frame.right(), frame.top(), -1, 1),
                               (frame.left(), frame.bottom(), 1, -1), (frame.right(), frame.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + L * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + L * dy))
        # «проектор» под голограммой — светящаяся полоска, на которой она стоит/сидит
        glow = QRadialGradient(QPointF(w / 2, h - 5), w * 0.45)
        glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 160))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(w / 2, h - 5), w * 0.45, 5)

    # мышь ---------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            if not self._moved:
                self.mode, self.walk_to, self.perch_hwnd = "drag", None, None
            self.move(e.globalPosition().toPoint() - self._drag_from)
            self._moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if not self._moved:
                self.clicked.emit()
            elif self.mode == "drag":
                self._land_after_drop()
                self.next_decision = time.monotonic() + 15   # дать посидеть там, куда положили
        self._drag_from = None

    def contextMenuEvent(self, e):
        if self.menu_builder:
            menu = self.menu_builder(self)
            menu.setAttribute(Qt.WA_DeleteOnClose)
            menu.exec(e.globalPos())
            return
        menu = QMenu(self)
        menu.addAction("Чат", self.clicked.emit)
        for label, callback in self.menu_items():
            menu.addAction(label, callback)
        menu.addAction("🚶 Гулять: выкл" if self.wander else "🚶 Гулять: вкл", self._toggle_wander)
        menu.addAction("Выход", self.quit_requested.emit)
        menu.exec(e.globalPos())

    def _toggle_wander(self):
        self.wander = not self.wander
        self.walk_to = None
