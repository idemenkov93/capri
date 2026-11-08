"""⌨ Таблица горячих клавиш: действие — сочетание — подсказка. Кликни в поле и нажми новое сочетание;
⚠ — пересекается с Windows / браузером / Teams или уже занято в Капри. Сохраняется в config.yaml → hotkeys."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import hotkeys as H

STYLE = """
QDialog, QWidget#page { background: #0b1222; color: #eef6ff; }
QLabel { color: #d7e3fb; }
QLabel#group { color: #8fb4ff; font-weight: 600; padding-top: 8px; }
QLabel#hint { color: #6f82a8; font-size: 11px; }
QLabel#warn { color: #ffb454; font-size: 11px; }
QLineEdit { background: #070b16; color: #eef6ff; border: 1px solid #1d3550; border-radius: 5px; padding: 4px 6px;
            font-family: Consolas, monospace; }
QLineEdit:focus { border-color: #1f6feb; }
QPushButton { background: #14223d; color: #eef6ff; border: 1px solid #27406a; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { background: #1c3156; }
QPushButton#main { background: #1f6feb; border-color: #1f6feb; }
QPushButton#small { padding: 2px 6px; }
"""

QT_NAMES = {Qt.Key_Space: "Space", **{getattr(Qt, f"Key_F{i}"): f"F{i}" for i in range(1, 13)}}


def combo_from_event(e) -> str:
    """Нажатие в поле → «Win+Alt+X». Только модификаторы (кроме Ctrl+Win для диктовки) → ''."""
    m = e.modifiers()
    mods = [n for flag, n in ((Qt.ControlModifier, "Ctrl"), (Qt.ShiftModifier, "Shift"), (Qt.AltModifier, "Alt"),
                              (Qt.MetaModifier, "Win")) if m & flag]
    k = e.key()
    if Qt.Key_A <= k <= Qt.Key_Z:
        key = chr(k)
    elif Qt.Key_0 <= k <= Qt.Key_9:
        key = chr(k)
    elif k in QT_NAMES:
        key = QT_NAMES[k]
    elif k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R):
        return "+".join(mods) if len(mods) >= 2 else ""       # «Ctrl+Win» для диктовки — только модификаторы
    else:
        return ""
    return "+".join(mods + [key])


class KeyEdit(QLineEdit):
    """Поле, которое ловит сочетание. Backspace / Delete — очистить (выключить)."""
    changed = Signal(str)

    def __init__(self, value: str):
        super().__init__(value)
        self.setReadOnly(True)
        self.setPlaceholderText("нажми сочетание…")
        self.setFixedWidth(150)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Backspace, Qt.Key_Delete) and not e.modifiers():
            self.setText("")
            self.changed.emit("")
            return
        if e.key() == Qt.Key_Escape:
            self.clearFocus()
            return
        c = combo_from_event(e)
        if c:
            self.setText(H.norm(c))
            self.changed.emit(self.text())

    def focusInEvent(self, e):
        self.setPlaceholderText("жми…  (Backspace — выключить)")
        super().focusInEvent(e)


class HotkeysDialog(QDialog):
    """saved(dict) — новые сочетания (id → «Win+Alt+X» или «» — выключено)."""
    saved = Signal(dict)

    def __init__(self, keys: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("⌨ Горячие клавиши Капри")
        self.setStyleSheet(STYLE)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(820, 640)
        self.keys = dict(keys)
        self.edits: dict[str, KeyEdit] = {}
        self.warns: dict[str, QLabel] = {}
        lay = QVBoxLayout(self)
        intro = QLabel("Работают в любой программе. Все сочетания Капри — <b>Win+Alt+буква</b>: их не используют ни "
                       "браузеры, ни Teams, ни Office, а Windows занятые мы обходим. Кликни в поле и нажми новое "
                       "сочетание · Backspace — выключить · ⚠ — с чем пересекается.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        page = QWidget(objectName="page")
        grid = QGridLayout(page)
        grid.setColumnStretch(0, 1)
        row, group = 0, ""
        for a in H.ACTIONS:
            if a.group != group:
                group = a.group
                grid.addWidget(QLabel(group, objectName="group"), row, 0, 1, 3)
                row += 1
            t = QLabel(a.title)
            t.setWordWrap(True)
            grid.addWidget(t, row, 0)
            ed = KeyEdit(self.keys.get(a.id, ""))
            ed.changed.connect(lambda v, i=a.id: self._set(i, v))
            grid.addWidget(ed, row, 1)
            reset = QPushButton("↺", objectName="small")
            reset.setFixedWidth(36)
            reset.setToolTip(f"По умолчанию: {a.default}")
            reset.clicked.connect(lambda _=False, i=a.id: self._reset(i))
            grid.addWidget(reset, row, 2)
            row += 1
            hint = QLabel(a.hint + (f" · по умолчанию {a.default}" if a.hint else f"по умолчанию {a.default}"),
                          objectName="hint")
            grid.addWidget(hint, row, 0)
            warn = QLabel("", objectName="warn")
            warn.setWordWrap(True)
            grid.addWidget(warn, row, 1, 1, 2)
            self.edits[a.id], self.warns[a.id] = ed, warn
            row += 1
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setStyleSheet("QScrollArea { border: 0; background: #0b1222; }")
        lay.addWidget(scroll, 1)
        bar = QHBoxLayout()
        b_def = QPushButton("Всё по умолчанию")
        b_def.clicked.connect(self._defaults)
        b_cancel = QPushButton("Отмена")
        b_cancel.clicked.connect(self.reject)
        self.b_save = QPushButton("💾 Сохранить", objectName="main")
        self.b_save.clicked.connect(self._save)
        bar.addWidget(b_def)
        bar.addStretch(1)
        bar.addWidget(b_cancel)
        bar.addWidget(self.b_save)
        lay.addLayout(bar)
        self._check()

    def _set(self, aid: str, value: str):
        self.keys[aid] = value
        self._check()

    def _reset(self, aid: str):
        self.edits[aid].setText(H.BY_ID[aid].default)
        self._set(aid, H.BY_ID[aid].default)

    def _defaults(self):
        for a in H.ACTIONS:
            self.edits[a.id].setText(a.default)
            self.keys[a.id] = a.default
        self._check()

    def _check(self):
        probs = H.problems(self.keys)
        for aid, w in self.warns.items():
            w.setText(("⚠ " + probs[aid]) if aid in probs else ("выключено" if not self.keys.get(aid) else ""))
        dup = any("уже занято" in p for p in probs.values())
        self.b_save.setEnabled(not dup)
        self.b_save.setToolTip("Два действия на одном сочетании — поправь ⚠" if dup else "")

    def _save(self):
        self.saved.emit(dict(self.keys))
        self.accept()
