"""Окно субтитров: полоса поверх всех окон (Teams, браузер). Оригинал — мелко, перевод — крупно.

Текст рисуется на всю ширину окна и растёт вместе с окном: потянул за любой край или угол — строки
перестроились, шрифт стал крупнее. Новая фраза плавно въезжает снизу, старые уходят вверх с затуханием.
Наведи мышь — кнопки: ⏸, A−/A+ (свой масштаб поверх авто), ☰ вся запись, 💡, 📝, ✕. Место и размер запоминаются.
Строка, где назвали твоё имя или слово-сигнал, — жёлтая.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QTextDocument)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

FLAG = {"de": "🇩🇪", "en": "🇬🇧", "ru": "🇷🇺", "he": "🇮🇱", "uk": "🇺🇦"}
WHO = {"system": "", "mic": "Ты: "}
# цвет имени по номеру голоса — одинаковый у одного человека весь урок
SPK_COLORS = ("#3ff3ff", "#b48cff", "#7dff9b", "#ffa36b", "#6bb6ff", "#ff8ad8", "#e0ff6b", "#ffd0a0")

BTN = """QPushButton { border: 1px solid #1d3550; color: #9fb3cf; background: rgba(11,18,34,235); padding: 2px 8px;
font-size: 12px; border-radius: 4px; } QPushButton:hover { border-color: #3ff3ff; color: #3ff3ff; }"""
BG = QColor(7, 11, 22, 222)
EDGE = 8                                   # за сколько пикселей от края можно тянуть размер


class SubtitleBar(QWidget):
    stop_clicked = Signal()
    pause_toggled = Signal(bool)
    notes_clicked = Signal()
    geometry_saved = Signal(list)          # [x, y, w, h, масштаб]
    rename_requested = Signal(int)         # клик по имени в записи урока — «как зовут спикера N?»
    hint_clicked = Signal()                # 💡 — что спросили и как ответить

    BASE_H = 190                           # при такой высоте шрифт = font_size

    def __init__(self, font_size: int = 20):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 110)
        self.setWindowTitle("Capri — субтитры")
        self.font_size = font_size
        self.entries: dict[int, dict] = {}
        self.order: list[int] = []
        self.names: dict[int, str] = {}        # номер голоса → имя («Спикер 2», «Herr Müller»)
        self.partial = ("", "")
        self.status = ""
        self.paused = False
        self.flash = 0
        self.html = ""
        self._drag: QPoint | None = None
        self._resize: tuple[str, QPoint, QRect] | None = None
        self._doc = QTextDocument()
        self._doc.setDocumentMargin(0)
        self._last_h = 0.0
        self.scroll = 0.0                      # плавный «подъезд» новой строки (пиксели)
        self._anim = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_scroll)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        self.bar = QWidget()
        b = QHBoxLayout(self.bar)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(4)
        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("color:#7d8db0; font-size:11px;")
        b.addWidget(self.lbl_status, 1)
        self.btn_pause = self._btn("⏸", "Пауза", self._pause)
        for w in (self.btn_pause, self._btn("A−", "Мельче", lambda: self.zoom(-2)), self._btn("A+", "Крупнее", lambda: self.zoom(2)),
                  self._btn("☰", "Вся запись урока", self.show_history),
                  self._btn("💡", "Что спросили и как ответить (окно, не вслух)", self.hint_clicked.emit),
                  self._btn("📝", "Конспект (остановит субтитры)", self.notes_clicked.emit),
                  self._btn("✕", "Выключить субтитры", self.stop_clicked.emit)):
            b.addWidget(w)
        lay.addWidget(self.bar)
        lay.addStretch(1)
        self.bar.setVisible(False)
        self.history = None
        self._flash_timer = QTimer(self, interval=120, timeout=self._flash_step)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        self.place()
        self.render()

    def _btn(self, text, tip, fn):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setStyleSheet(BTN)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def place(self, geo: list | None = None):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        if geo and len(geo) >= 4:
            x, y, w, h = (int(v) for v in geo[:4])
            w, h = max(320, min(w, scr.width())), max(110, min(h, scr.height()))
            x = min(max(x, scr.left()), scr.right() - w)              # не за краем экрана
            y = min(max(y, scr.top()), scr.bottom() - h)
            self.setGeometry(x, y, w, h)
            if len(geo) > 4:
                self.font_size = int(geo[4])
            return
        w = int(scr.width() * 0.66)
        h = 230
        self.setGeometry(scr.left() + (scr.width() - w) // 2, scr.bottom() - h - 24, w, h)

    # ------------------------------------------------------------------ данные
    def add_line(self, id_: int, source: str, text: str, lang: str = "", trigger: str = "", spk: int = 0,
                 speaker: str = ""):
        self.entries[id_] = {"source": source, "text": text, "lang": lang, "tr": None, "trigger": trigger, "spk": spk}
        if spk and speaker:
            self.names.setdefault(spk, speaker)
        self.order.append(id_)
        if self.history is not None:
            self._fill_history()
        if trigger:
            self.flash = 14
            self._flash_timer.start()
            self.raise_()
        self.render()

    def set_translation(self, id_: int, text: str):
        if id_ in self.entries:
            self.entries[id_]["tr"] = text
            self.render()
            if self.history is not None:
                self._fill_history()

    def set_partial(self, source: str, text: str):
        self.partial = (source, text)
        self.render()

    def set_status(self, text: str):
        self.status = text
        self.lbl_status.setText(text)
        self.render()

    # ------------------------------------------------------------------ вид
    def zoom(self, d: int):
        self.font_size = max(12, min(48, self.font_size + d))
        self.render()
        self._save()

    def _pause(self):
        self.paused = not self.paused
        self.btn_pause.setText("▶" if self.paused else "⏸")
        self.pause_toggled.emit(self.paused)
        self.render()

    def sizes(self) -> tuple[int, int]:
        """(перевод, оригинал) в пикселях: растут с высотой окна, A−/A+ — свой масштаб поверх."""
        k = max(0.8, min(2.8, (self.height() / self.BASE_H) ** 0.85))
        big = int(round(self.font_size * k))
        return big, max(11, int(big * 0.6))

    def text_rect(self) -> QRectF:
        return QRectF(16, 10, max(50, self.width() - 32), max(20, self.height() - 20))

    def render(self):
        fs, small = self.sizes()
        parts = []
        for id_ in self.order[-12:]:                 # сколько влезет — решает высота окна
            e = self.entries[id_]
            mine = e["source"] == "mic"
            col = "#ffd23f" if e["trigger"] else ("#ff7ab0" if mine else "#9fb3cf")
            flag = FLAG.get(e["lang"], "")
            bell = f" 🔔 <b>{html.escape(e['trigger'])}</b>" if e["trigger"] else ""
            parts.append(f'<p style="color:{col}; font-size:{small}px; margin-top:{int(small * 0.7)}px; margin-bottom:1px;">'
                         f'{flag} {self._who(e)}{html.escape(e["text"])}{bell}</p>')
            if e["tr"] is None and not mine:
                parts.append(f'<p style="color:#5b6b85; font-size:{fs}px; margin:0;">…</p>')
            elif e["tr"]:
                tcol = "#fff3b0" if e["trigger"] else ("#ffd0e4" if mine else "#ffffff")
                parts.append(f'<p style="color:{tcol}; font-size:{fs}px; font-weight:600; margin:0;">{html.escape(e["tr"])}</p>')
        src, ptxt = self.partial
        if ptxt:
            parts.append(f'<p style="color:#7d8db0; font-size:{small}px; font-style:italic; margin-top:{int(small * 0.5)}px;">'
                         f'{"Ты: " if src == "mic" else ""}{html.escape(ptxt)} …</p>')
        if not parts:
            msg = "⏸ Пауза" if self.paused else (self.status or "Слушаю… Включи урок или видео — субтитры появятся здесь.")
            parts.append(f'<p style="color:#7d8db0; font-size:{small}px;">{html.escape(msg)}</p>')
        self.html = "".join(parts)
        self._doc.setDefaultFont(QFont("Segoe UI", 10))
        self._doc.setHtml(self.html)
        self._doc.setTextWidth(self.text_rect().width())
        h = self._doc.size().height()
        grew = h - self._last_h
        if 0 < grew < self.height() and self.isVisible() and self._last_h:
            self._anim.stop()                          # новая строка — плавно поднимаем текст снизу
            self._anim.setStartValue(float(grew) + self.scroll)
            self._anim.setEndValue(0.0)
            self._anim.start()
        self._last_h = h
        self.update()

    def _on_scroll(self, v):
        self.scroll = float(v)
        self.update()

    def _who(self, e: dict, link: bool = False) -> str:
        if e["source"] == "mic":
            return '<b style="color:#ff7ab0">Ты:</b> '
        name = self.names.get(e.get("spk", 0))
        if not name:
            return ""
        col = SPK_COLORS[(e["spk"] - 1) % len(SPK_COLORS)]
        label = html.escape(name)
        if link:
            label = f'<a href="spk:{e["spk"]}" style="color:{col}; text-decoration:none">{label}</a>'
        return f'<b style="color:{col}">{label}:</b> '

    def rename_speakers(self, names: dict[int, str]):
        """После «спикер 2 — это Herr Müller»: новые имена (и слитые голоса) — на экране и в записи."""
        self.names.update(names)
        self.render()
        self._fill_history()

    def _flash_step(self):
        self.flash -= 1
        if self.flash <= 0:
            self._flash_timer.stop()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, BG)
        # текст: прижат к низу, лишнее сверху уходит за край с затуханием
        tr = self.text_rect()
        dpr = self.devicePixelRatioF()
        img = QImage(int(tr.width() * dpr), int(tr.height() * dpr), QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing)
        q.translate(0, tr.height() - self._doc.size().height() + self.scroll)
        self._doc.drawContents(q)
        q.resetTransform()
        q.setCompositionMode(QPainter.CompositionMode_DestinationIn)      # верх плавно гаснет
        fade = QLinearGradient(0, 0, 0, 34)
        fade.setColorAt(0, QColor(0, 0, 0, 0))
        fade.setColorAt(1, QColor(0, 0, 0, 255))
        q.fillRect(QRectF(0, 0, tr.width(), 34), fade)
        q.end()
        p.drawImage(tr.topLeft(), img)
        on = self.flash > 0 and self.flash % 2 == 0
        p.setPen(QPen(QColor("#ffd23f" if on else ("#3ff3ff" if self.underMouse() else "#1d3550")), 2 if on else 1))
        p.drawPath(path)
        if self.underMouse():                          # уголок «тяни отсюда»
            p.setPen(QPen(QColor("#3ff3ff"), 1.4))
            for k in (6, 11, 16):
                p.drawLine(int(r.right()) - k, int(r.bottom()) - 3, int(r.right()) - 3, int(r.bottom()) - k)
        p.end()

    # ------------------------------------------------------------------ мышь: тащить и тянуть за края
    def _edge(self, pos: QPoint) -> str:
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        e = ("t" if y < EDGE else "b" if y > h - EDGE else "") + ("l" if x < EDGE else "r" if x > w - EDGE else "")
        return e

    def _cursor(self, edge: str):
        shapes = {"t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                  "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}
        self.setCursor(shapes.get(edge, Qt.ArrowCursor))

    def enterEvent(self, _):
        self.bar.setVisible(True)
        self.update()

    def leaveEvent(self, _):
        self.bar.setVisible(False)
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        edge = self._edge(pos)
        if edge:
            self._resize = (edge, e.globalPosition().toPoint(), self.geometry())
        else:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        g = e.globalPosition().toPoint()
        if self._resize is not None:
            edge, start, geo = self._resize
            dx, dy = g.x() - start.x(), g.y() - start.y()
            r = QRect(geo)
            if "l" in edge:
                r.setLeft(min(geo.left() + dx, geo.right() - self.minimumWidth()))
            if "r" in edge:
                r.setRight(max(geo.right() + dx, geo.left() + self.minimumWidth()))
            if "t" in edge:
                r.setTop(min(geo.top() + dy, geo.bottom() - self.minimumHeight()))
            if "b" in edge:
                r.setBottom(max(geo.bottom() + dy, geo.top() + self.minimumHeight()))
            self.setGeometry(r)
        elif self._drag is not None:
            self.move(g - self._drag)
        else:
            self._cursor(self._edge(e.position().toPoint()))

    def mouseReleaseEvent(self, _):
        if self._drag is not None or self._resize is not None:
            self._drag = self._resize = None
            self._save()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._last_h = 0                               # перестроить без анимации
        self.render()
        self._save_timer.start()

    def _save(self):
        g = self.geometry()
        self.geometry_saved.emit([g.x(), g.y(), g.width(), g.height(), self.font_size])

    # ------------------------------------------------------------------ вся запись
    def show_history(self):
        if self.history is None:
            self.history = QTextBrowser()
            self.history.setWindowTitle("Capri — запись урока")
            self.history.setStyleSheet("QTextBrowser { background:#0b1222; color:#eef6ff; font-size:13px; padding:8px; }")
            self.history.setOpenLinks(False)
            self.history.anchorClicked.connect(lambda url: url.scheme() == "spk" and
                                               self.rename_requested.emit(int(url.path() or 0)))
            self.history.setToolTip("Нажми на имя говорящего, чтобы назвать его (голос запомню)")
            self.history.resize(640, 720)
        self._fill_history()
        self.history.show()
        self.history.raise_()

    def _entry_html(self, id_: int) -> str:
        e = self.entries[id_]
        col = "#ffd23f" if e["trigger"] else ("#ff7ab0" if e["source"] == "mic" else "#9fb3cf")
        who = self._who(e, link=True) or "<b>Они:</b> "
        tr = f'<br><span style="color:#ffffff">{html.escape(e["tr"])}</span>' if e["tr"] else ""
        return f'<p>{who}<span style="color:{col}">{html.escape(e["text"])}</span>{tr}</p>'

    def _fill_history(self):
        if self.history is None:
            return
        bar = self.history.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.history.setHtml("".join(self._entry_html(i) for i in self.order))
        if at_end:
            bar.setValue(bar.maximum())

    def clear(self):
        self.entries, self.order, self.partial, self.names = {}, [], ("", ""), {}
        self.render()
