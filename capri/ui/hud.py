"""HUD «CAPRI // PERSONAL LINK» — основное окно в киберпанк-стиле.

Устроено как панель связи: сверху «входящая передача» с последней репликой,
посередине экран с аватаром (уголки-скобки, статус, глитч, индикатор звука),
снизу строка ввода. Полная история переписки — по кнопке ☰.
Интерфейс совместим с ChatWindow, поэтому app.py работает с любым из них.
"""
from __future__ import annotations

import html
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from .avatar import STATUS_COLORS, draw_star
from .sprites import SpriteSet

BG = QColor("#070b16")
PANEL = QColor("#0b1222")
CYAN = QColor("#3ff3ff")
DIM = QColor("#5b6b85")
PINK = QColor("#ff2a6d")
YELLOW = QColor("#f5e663")

STATE_LABEL = {
    "ONLINE": "STANDBY", "LOADING": "BOOTING", "THINKING": "THINKING", "RECOGNIZING": "DECODING",
    "CLAUDE": "UPLINK // CLAUDE", "TALKING": "SPEAKING", "SPEAKING": "SPEAKING",
    "LISTENING": "LISTENING", "ERROR": "SIGNAL LOST", "TYPING": "TEXT INPUT",
}
STATE_LINE = {
    "ONLINE": "Capri is online.", "LOADING": "Capri is booting up…", "THINKING": "Capri is thinking…",
    "RECOGNIZING": "Decoding your voice…", "CLAUDE": "Capri is asking Claude…",
    "TALKING": "Capri is replying…", "SPEAKING": "Capri is speaking…", "LISTENING": "Capri is listening…",
    "ERROR": "Something broke.", "TYPING": "You are typing…",
}
ROUTE_LABEL = {"rules": "⚡ DIRECT", "local": "◆ LOCAL", "claude": "✦ CLAUDE", "error": "✖ ERROR"}

STYLE = """
QWidget { color: #d8e3f5; font-family: 'Cascadia Mono', Consolas, 'JetBrains Mono', monospace; font-size: 12px; }
QLineEdit { background: #0b1222; border: 1px solid #1d3550; border-left: 3px solid #ff2a6d;
            padding: 8px 10px; color: #e8f4ff; selection-background-color: #3ff3ff; }
QLineEdit:focus { border-color: #3ff3ff; border-left-color: #ff2a6d; }
QPushButton { background: transparent; border: 1px solid #1d3550; color: #3ff3ff; padding: 6px 10px; }
QPushButton:hover { border-color: #3ff3ff; background: #0f2235; }
QPushButton#icon { border: none; padding: 2px 6px; color: #5b6b85; font-size: 14px; }
QPushButton#icon:hover { color: #3ff3ff; background: transparent; }
QPushButton#stop { border-color: #ff2a6d; color: #ff2a6d; }
QTextBrowser { background: #0b1222; border: 1px solid #1d3550; padding: 6px; }
QLabel#status { color: #5b6b85; font-size: 10px; letter-spacing: 1px; }
QScrollBar:vertical { background: #0b1222; width: 6px; }
QScrollBar::handle:vertical { background: #1d3550; }
"""


def _cut_corner_path(r: QRectF, cut: float) -> QPainterPath:
    """Прямоугольник со скошенными уголками — фирменная «киберпанк»-рамка."""
    path = QPainterPath()
    path.moveTo(r.left() + cut, r.top())
    path.lineTo(r.right(), r.top())
    path.lineTo(r.right(), r.bottom() - cut)
    path.lineTo(r.right() - cut, r.bottom())
    path.lineTo(r.left(), r.bottom())
    path.lineTo(r.left(), r.top() + cut)
    path.closeSubpath()
    return path


class Transmission(QWidget):
    """Карточка «входящей передачи»: реплика пользователя + ответ Капри с эффектом печати."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 18)
        lay.setSpacing(3)
        self.you = QLabel(wordWrap=True)
        self.you.setStyleSheet("color:#5b6b85; font-size:10px;")
        self.head = QLabel()
        self.head.setStyleSheet("color:#3ff3ff; font-size:10px; letter-spacing:1px;")
        self.body = QLabel(wordWrap=True)
        self.body.setTextFormat(Qt.PlainText)
        self.body.setStyleSheet("color:#eef6ff; font-size:13px;")
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for w in (self.you, self.head, self.body):
            lay.addWidget(w)
        self._target = ""
        self._typer = QTimer(self, interval=12, timeout=self._type_step)
        self.set_you("")
        self.set_capri("Link established. Жду команду.", "rules", instant=True)

    def set_you(self, text: str):
        self.you.setText(f"YOU  ·  {text}" if text else "YOU  ·  —")

    def set_capri(self, text: str, route: str = "local", instant: bool = False):
        self.head.setText(f"CAPRI  /  INCOMING TRANSMISSION   {ROUTE_LABEL.get(route, '')}")
        self._target = text
        if instant:
            self.body.setText(text)
        else:
            self.body.setText("")
            self._typer.start()

    def set_live(self, text: str):
        self._typer.stop()
        self.head.setText("CAPRI  /  INCOMING TRANSMISSION   ···")
        self.body.setText(text[-700:])

    def _type_step(self):
        cur = self.body.text()
        if len(cur) >= len(self._target):
            self._typer.stop()
            return
        step = max(1, len(self._target) // 120)  # длинный текст печатается быстрее
        self.body.setText(self._target[: len(cur) + step])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -9)
        path = _cut_corner_path(r, 10)
        p.fillPath(path, PANEL)
        p.setPen(QPen(CYAN, 1.2))
        p.drawPath(path)
        p.fillRect(QRectF(r.left(), r.top() + 14, 3, r.height() - 28), PINK)  # акцент слева
        tail = QPainterPath()  # хвостик «облачка» вниз к экрану
        tx = r.right() - 46
        tail.moveTo(tx, r.bottom())
        tail.lineTo(tx + 16, r.bottom())
        tail.lineTo(tx + 20, r.bottom() + 8)
        tail.closeSubpath()
        p.fillPath(tail, CYAN)


class Viewport(QWidget):
    """Экран с аватаром: сетка, уголки-скобки, метка статуса, глитч и индикатор звука."""

    def __init__(self, custom_image: str = "", fps: int = 12):
        super().__init__()
        self.setFixedHeight(300)  # примерно 4:3 — под кадры из видео
        self.state = "LOADING"
        self.phase = 0.0
        self.level = 0.0
        self.glitch = 0
        self.sprites = SpriteSet(custom_image, fps)
        self.transition = 0
        self.stars = [(random.random(), random.random(), random.random()) for _ in range(70)]
        QTimer(self, interval=33, timeout=self._tick).start()

    def set_state(self, state: str):
        if state != self.state:
            self.glitch = 10
            # короткий «переход» между состояниями, если есть анимация teleport
            if self.sprites and "teleport" in self.sprites.kinds() and state != "TYPING":
                self.transition = 22  # кадров по 33 мс ≈ 0.7 сек
                self.sprites.shuffle("teleport")
            for kind in ("idle", "talk", "think", "listen"):
                self.sprites.shuffle(kind)  # каждый раз — случайный вариант анимации
        self.state = state

    def sprite_kind(self) -> str:
        if self.transition > 0:
            return "teleport"
        if self.state in ("THINKING", "CLAUDE", "RECOGNIZING", "LOADING"):
            return "think"
        if self.state in ("SPEAKING", "TALKING"):
            return "talk"
        if self.state == "LISTENING":
            return "listen"
        return "idle"

    def _tick(self):
        self.phase += 0.06
        if self.transition:
            self.transition -= 1
        if self.glitch:
            self.glitch -= 1
        elif self.state in ("THINKING", "CLAUDE", "RECOGNIZING") and random.random() < 0.25:
            self.glitch = 3
        elif random.random() < 0.004:
            self.glitch = 3
        if self.state in ("SPEAKING", "TALKING"):
            self._shown_level = 0.35 + 0.6 * abs(math.sin(self.phase * 3.1) * math.sin(self.phase * 1.7))
        elif self.state == "LISTENING":
            self._shown_level = self.level  # реальная громкость с микрофона
        else:
            self._shown_level = 0.0
        self.update()

    def color(self) -> QColor:
        if self.state in ("SPEAKING", "TALKING"):
            return YELLOW
        return STATUS_COLORS.get(self.state, CYAN)

    def _scene(self, w: int, h: int, col: QColor) -> QPixmap:
        """Нарисовать «картинку на экране» в отдельный буфер (его потом режет глитч)."""
        buf = QPixmap(max(1, w), max(1, h))
        buf.fill(Qt.transparent)
        p = QPainter(buf)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, w, h)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#0a1020"))
        g.setColorAt(1, QColor("#05070f"))
        p.fillRect(r, g)
        for sx, sy, sb in self.stars:
            a = int(60 + 120 * abs(math.sin(self.phase * (0.5 + sb) + sx * 10)))
            p.fillRect(QRectF(sx * w, sy * h, 1.5, 1.5), QColor(180, 220, 255, a))
        cx, cy = w / 2, h / 2 + math.sin(self.phase) * 4
        size = min(w, h) * 0.78
        sprite = self.sprites.get(self.sprite_kind()) if self.sprites else None
        img = sprite.frame() if sprite else None
        if img is not None and not img.isNull():
            if img.hasAlphaChannel() and img.toImage().pixelColor(0, 0).alpha() == 0:
                # прозрачный спрайт — рисуем фигурку по центру экрана
                img = img.scaled(int(size * 1.15), int(size * 1.15), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(int(cx - img.width() / 2), int(cy - img.height() / 2), img)
            else:
                # кадр из видео с фоном — заполняем весь экран (лишнее обрезается), как в трансляции
                img = img.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                p.drawPixmap(int((w - img.width()) / 2), int((h - img.height()) / 2), img)
        else:
            # голографические кольца-орбиты вокруг стандартного аватара
            for k, (rad, speed, dash) in enumerate(((0.52, 0.6, [2, 6]), (0.62, -0.35, [12, 8]), (0.72, 0.2, [1, 10]))):
                pen = QPen(QColor(col.red(), col.green(), col.blue(), 90 - k * 20), 1.4)
                pen.setDashPattern(dash)
                pen.setDashOffset(self.phase * 20 * speed)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), size * rad, size * rad * 0.92)
                ang = self.phase * speed * 2 + k * 2.1
                dot = QPointF(cx + math.cos(ang) * size * rad, cy + math.sin(ang) * size * rad * 0.92)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawEllipse(dot, 2.5, 2.5)
            glow = QRadialGradient(QPointF(cx, cy), size * 0.5)
            glow.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 70))
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(glow)
            p.drawEllipse(QPointF(cx, cy), size * 0.5, size * 0.5)
            draw_star(p, cx, cy, size * 0.36, col, self.phase, bool(self.glitch))
        p.setPen(QColor(255, 255, 255, 10))  # сканлайны
        for y in range(0, h, 3):
            p.drawLine(0, y, w, y)
        p.end()
        return buf

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 10, -6, -6)
        col = self.color()
        scene = self._scene(int(r.width()), int(r.height()), col)
        p.drawPixmap(int(r.left()), int(r.top()), scene)

        # глитч: сдвинутые полосы и цветные «разрывы»
        if self.glitch:
            for _ in range(5):
                hh = random.randint(3, 14)
                y = random.randint(0, max(1, int(r.height()) - hh))
                dx = random.randint(-18, 18)
                p.drawPixmap(int(r.left() + dx), int(r.top() + y), scene, 0, y, int(r.width()), hh)
            for c in (QColor(255, 42, 109, 90), QColor(63, 243, 255, 90), QColor(245, 230, 99, 70)):
                y = random.uniform(r.top(), r.bottom())
                p.fillRect(QRectF(r.left(), y, r.width(), random.uniform(1, 4)), c)

        # уголки-скобки
        p.setPen(QPen(col, 2.2))
        L = 22
        for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + L * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + L * dy))

        # метка статуса
        label = f"CAPRI // {STATE_LABEL.get(self.state, self.state)}"
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        w = p.fontMetrics().horizontalAdvance(label) + 14
        tag = QRectF(r.left() + 10, r.top() - 8, w, 16)
        p.fillRect(tag, QColor("#070b16"))
        p.setPen(QPen(col, 1))
        p.drawRect(tag)
        p.drawText(tag, Qt.AlignCenter, label)

        # индикатор звука
        lvl = getattr(self, "_shown_level", 0.0)
        bars = 14
        for i in range(bars):
            h = 3 + 22 * lvl * (0.4 + 0.6 * abs(math.sin(self.phase * 4 + i * 0.9)))
            x = r.right() - 14 - (bars - i) * 5
            p.fillRect(QRectF(x, r.bottom() - 10 - h, 3, h), col if lvl > 0.02 else DIM)


class HudWindow(QWidget):
    submitted = Signal(str)
    stop_requested = Signal()
    mic_clicked = Signal()
    speak_toggled = Signal()

    def __init__(self, custom_image: str = "", fps: int = 12):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("CAPRI // PERSONAL LINK")
        self.setStyleSheet(STYLE)
        self.resize(430, 560)
        self._drag = None
        self._live_text = ""
        self._thinking = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(8)

        # шапка
        head = QHBoxLayout()
        title = QLabel("<b style='color:#eef6ff;letter-spacing:2px'>▍CAPRI</b>"
                       "&nbsp;&nbsp;<span style='color:#5b6b85;font-size:9px;letter-spacing:2px'>PERSONAL LINK</span>")
        head.addWidget(title)
        head.addStretch()
        self.btn_mic_top = self._icon("🎙", "Сказать голосом", self.mic_clicked.emit)
        self.btn_voice = self._icon("🔊", "Озвучка вкл/выкл", self.speak_toggled.emit)
        self.btn_hist = self._icon("☰", "История переписки", self.toggle_history)
        self.btn_hide = self._icon("—", "Свернуть", self.hide)
        for b in (self.btn_mic_top, self.btn_voice, self.btn_hist, self.btn_hide):
            head.addWidget(b)
        root.addLayout(head)

        self.card = Transmission()
        root.addWidget(self.card)

        self.view = Viewport(custom_image, fps)
        root.addWidget(self.view, 1)

        self.log = QTextBrowser(openExternalLinks=True)
        self.log.hide()
        root.addWidget(self.log, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit(placeholderText="Talk to Capri…")
        self.mic = QPushButton("🎤")
        self.mic.setToolTip("Нажми, говори, нажми ещё раз")
        self.mic.hide()
        self.send = QPushButton("➤")
        self.stop = QPushButton("■", objectName="stop")
        self.stop.hide()
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(self.send)
        row.addWidget(self.stop)
        root.addLayout(row)

        self.status = QLabel(objectName="status")
        root.addWidget(self.status)
        self.set_status("LOADING")

        self.resize(430, self.sizeHint().height())  # окно ровно по содержимому

        self.input.returnPressed.connect(self._submit)
        self.input.textEdited.connect(lambda t: self.set_status("TYPING" if t else "ONLINE"))
        self.send.clicked.connect(self._submit)
        self.stop.clicked.connect(self.stop_requested.emit)
        self.mic.clicked.connect(self.mic_clicked.emit)

    def _icon(self, text, tip, slot):
        b = QPushButton(text, objectName="icon")
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    # фон окна ---------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = _cut_corner_path(r, 16)
        p.fillPath(path, QColor(7, 11, 22, 242))
        p.setPen(QPen(QColor("#1d3550"), 1))
        p.drawPath(path)
        p.fillRect(QRectF(r.left() + 16, r.top(), 60, 2), PINK)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 40:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    # совместимость с ChatWindow -------------------------------------------
    def set_status(self, state: str):
        self.view.set_state(state)
        self.status.setText(STATE_LINE.get(state, state).upper() + "   ·   ENTER — SEND   ·   ESC — HIDE")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def set_level(self, level: float):
        self.view.level = max(0.0, min(1.0, level))

    def set_speak_icon(self, on: bool):
        self.btn_voice.setText("🔊" if on else "🔇")

    def toggle_history(self):
        show = not self.log.isVisible()
        self.log.setVisible(show)
        self.view.setVisible(not show)

    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.add_user(text)
            self.submitted.emit(text)

    def set_busy(self, busy: bool):
        self.input.setEnabled(not busy)
        self.send.setVisible(not busy)
        self.stop.setVisible(busy)
        if not busy:
            self.input.setFocus()

    def set_recording(self, on: bool):
        self.mic.setText("⏺" if on else "🎤")
        self.mic.setStyleSheet("border-color:#ff2a6d; color:#ff2a6d;" if on else "")

    def stream(self, kind: str, piece: str):
        if kind == "reset":
            self._live_text = piece
        elif kind == "thinking":
            self._thinking += len(piece)
            if not self._live_text:
                self.card.set_live(f"🧠 размышляет… ({self._thinking} симв.)")
                return
        else:
            self._live_text += piece
        self.card.set_live(self._live_text)

    def end_stream(self):
        self._live_text, self._thinking = "", 0

    def add_user(self, text: str):
        self.card.set_you(text)
        self._log(f'<p style="color:#3ff3ff"><b>YOU ›</b> {html.escape(text)}</p>')

    def add_capri(self, text: str, route: str = "local"):
        self.card.set_capri(text, route)
        self._log(f'<p style="color:#ff2a6d"><b>CAPRI</b> <span style="color:#5b6b85">{ROUTE_LABEL.get(route, "")}</span></p>')
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertMarkdown(text)
        cur.insertBlock()
        self.log.setTextCursor(cur)

    def add_system(self, text: str):
        self._log(f'<p style="color:#5b6b85"><i>{html.escape(text)}</i></p>')
        self.status.setText(text.upper()[:90])

    def _log(self, html_text: str):
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        if cur.block().text():
            cur.insertBlock()
        cur.insertHtml(html_text)
        cur.insertBlock()
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()
