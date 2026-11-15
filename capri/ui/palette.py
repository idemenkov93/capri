"""Палитра команд: одно окно поиска вместо длинных меню.

В ней есть всё, что умеет Капри: действия с горячими клавишами, команды-фразы и настройки
из старого меню. Набери пару букв — Enter. Esc закрывает.
Команды приходят снаружи (функция provider) — палитра про Капри ничего не знает.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from .family import tokens, win

ROLE = Qt.UserRole + 1
LIMIT = 80              # сколько строк показываем за раз


@dataclass
class Command:
    title: str
    group: str = ""                     # откуда команда: «Урок», «Настройки › Голос»
    hint: str = ""                      # клавиша или фраза, показывается справа
    run: Callable | None = None
    checked: bool | None = None         # для переключателей: включено / выключено
    keywords: str = ""                  # слова, по которым команда тоже находится
    settings: bool = False              # пункт настроек (отдельный список по кнопке «Настройки»)


def match(commands: list[Command], query: str, limit: int = LIMIT) -> list[Command]:
    """Команды, в которых есть все слова запроса. Сначала — где совпало начало названия."""
    words = query.lower().split()
    if not words:
        return commands[:limit]
    scored = []
    for i, c in enumerate(commands):
        title = c.title.lower()
        hay = f"{title} {c.group.lower()} {c.keywords.lower()} {c.hint.lower()}"
        if not all(w in hay for w in words):
            continue
        clean = title.lstrip("🎧🎓🎯📚📋🌐✉📄🔕❓💡💬🗣🔄⌨☀📝📖📥💾▶⏹🙋☎🃏🔁🇩🇪✨🧍🙈 ")
        score = 0 if clean.startswith(words[0]) else 1 if any(p.startswith(words[0]) for p in clean.split()) else \
            2 if words[0] in title else 3
        scored.append((score, i, c))
    return [c for _s, _i, c in sorted(scored, key=lambda x: x[:2])[:limit]]


class Delegate(QStyledItemDelegate):
    """Строка палитры: название, под ним серым группа; справа — клавиша или галочка."""

    def __init__(self, colors: dict, parent=None):
        super().__init__(parent)
        self.c = colors

    def sizeHint(self, option, index) -> QSize:
        return QSize(100, 46)

    def paint(self, painter: QPainter, option, index) -> None:
        cmd, c = index.data(ROLE), self.c
        r = option.rect.adjusted(6, 2, -6, -2)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            painter.setPen(Qt.NoPen)
            painter.setBrush(tokens.qcolor(c["hover"]))
            painter.drawRoundedRect(r, tokens.RADIUS_CONTROL, tokens.RADIUS_CONTROL)
            if option.state & QStyle.State_Selected:                    # акцентная полоска слева, как в Windows 11
                painter.setBrush(tokens.qcolor(c["accent"]))
                painter.drawRoundedRect(QRect(r.left(), r.top() + 12, 3, r.height() - 24), 1.5, 1.5)
        right = r.right() - 12
        hint = ("✓ включено" if cmd.checked else "выключено") if cmd.checked is not None else cmd.hint
        if hint:
            painter.setFont(tokens.font(12))
            width = painter.fontMetrics().horizontalAdvance(hint)
            painter.setPen(tokens.qcolor(c["accent"] if cmd.checked else c["text3"]))
            painter.drawText(QRect(right - width, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignRight, hint)
            right -= width + 16
        text = QRect(r.left() + 14, r.top() + 4, right - r.left() - 14, r.height() - 8)
        painter.setFont(tokens.font(14))
        painter.setPen(tokens.qcolor(c["text"]))
        title = painter.fontMetrics().elidedText(cmd.title, Qt.ElideRight, text.width())
        painter.drawText(text, (Qt.AlignTop if cmd.group else Qt.AlignVCenter) | Qt.AlignLeft, title)
        if cmd.group:
            painter.setFont(tokens.font(12))
            painter.setPen(tokens.qcolor(c["text3"]))
            group = painter.fontMetrics().elidedText(cmd.group, Qt.ElideRight, text.width())
            painter.drawText(text, Qt.AlignBottom | Qt.AlignLeft, group)
        painter.restore()


class Palette(QWidget):
    ran = Signal(str)                   # название выполненной команды — для списка «недавние»

    def __init__(self, provider: Callable[[], list[Command]]):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.provider = provider
        self.commands: list[Command] = []
        self.only_settings = False
        self.setFixedSize(640, 440)
        self.search = QLineEdit()
        self.search.setFrame(False)
        self.search.setFont(tokens.font(18))
        self.search.textChanged.connect(self.refill)
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(lambda item: self.run(item.data(ROLE)))
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 12, 8, 8)
        box.setSpacing(8)
        box.addWidget(self.search)
        box.addWidget(self.list, 1)
        self.restyle()

    def restyle(self) -> None:
        c = self.c = tokens.colors()
        self.list.setItemDelegate(Delegate(c, self.list))
        self.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {c['text']}; padding: 6px 14px; border: none;"
            f" border-bottom: 1px solid {c['stroke']}; selection-background-color: {c['accent']}; }}"
            f"QListWidget {{ background: transparent; outline: none; }}")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), tokens.qcolor(self.c["surface"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(tokens.qcolor(self.c["stroke"]), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), tokens.RADIUS_CARD, tokens.RADIUS_CARD)
        p.end()

    # ---------- показ
    def open(self, settings: bool = False, query: str = "") -> None:
        """Показать палитру по центру экрана. settings=True — только настройки."""
        self.only_settings = settings
        self.restyle()
        self.commands = self.provider()
        self.search.setPlaceholderText("Поиск по настройкам…" if settings else "Что сделать? Набери пару букв…")
        self.search.setText(query)
        self.refill()
        screen = (QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        self.move(screen.x() + (screen.width() - self.width()) // 2, screen.y() + int(screen.height() * 0.2))
        self.show()
        win.round_corners(self)
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def refill(self) -> None:
        self.list.clear()
        query = self.search.text()
        # пустая строка: команды или настройки — что открыли; начал печатать в «командах» — ищем везде
        pool = [c for c in self.commands if c.settings == self.only_settings or (query.strip() and not self.only_settings)]
        for cmd in match(pool, query):
            item = QListWidgetItem()
            item.setData(ROLE, cmd)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def titles(self) -> list[str]:
        return [self.list.item(i).data(ROLE).title for i in range(self.list.count())]

    # ---------- клавиши
    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
                step = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: 8, Qt.Key_PageUp: -8}[key]
                row = min(self.list.count() - 1, max(0, self.list.currentRow() + step))
                self.list.setCurrentRow(row)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self.run(item.data(ROLE))
                return True
            if key == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.hide()                          # кликнули мимо — палитра закрывается
        super().changeEvent(event)

    def run(self, cmd: Command) -> None:
        self.hide()
        if cmd.run is not None:
            self.ran.emit(cmd.title)
            cmd.run()
