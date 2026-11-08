"""Новые термины урока → в бестиарий: галочка — оставить, поправить перевод прямо в таблице, без галочки — выкинуть."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

STYLE = """
QDialog { background: #0b1222; color: #eef6ff; }
QLabel { color: #b9c7e6; }
QTableWidget { background: #070b16; color: #eef6ff; gridline-color: #1d3550; border: 1px solid #1d3550; }
QHeaderView::section { background: #101a30; color: #8fb4ff; border: 0; padding: 4px; }
QPushButton { background: #14223d; color: #eef6ff; border: 1px solid #27406a; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #1c3156; }
QPushButton#main { background: #1f6feb; border-color: #1f6feb; }
"""


class TermsDialog(QDialog):
    def __init__(self, terms: list[dict], title: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("📖 Новые термины урока → в бестиарий")
        self.setStyleSheet(STYLE)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(760, min(140 + 34 * len(terms), 620))
        self.terms = terms
        self.accepted_rows: list[dict] = []
        self.skipped: list[str] = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel((f"Урок «{title}». " if title else "") +
                             "✓ — оставить в бестиарии. Перевод и заметку можно поправить (двойной клик). "
                             "Без галочки — больше не предлагать."))
        self.table = QTableWidget(len(terms), 3)
        self.table.setHorizontalHeaderLabels(["Deutsch", "Русский", "Заметка"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for i, t in enumerate(terms):
            de = QTableWidgetItem(t["de"])
            de.setFlags(de.flags() | Qt.ItemIsUserCheckable)
            de.setCheckState(Qt.Checked)
            self.table.setItem(i, 0, de)
            self.table.setItem(i, 1, QTableWidgetItem(t["ru"]))
            self.table.setItem(i, 2, QTableWidgetItem(t.get("note", "")))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_none = QPushButton("Снять все")
        b_none.clicked.connect(lambda: self._check_all(False))
        b_later = QPushButton("Позже")
        b_later.clicked.connect(self.reject)
        b_ok = QPushButton("➕ Сохранить в бестиарий", objectName="main")
        b_ok.clicked.connect(self._save)
        row.addWidget(b_none)
        row.addStretch(1)
        row.addWidget(b_later)
        row.addWidget(b_ok)
        lay.addLayout(row)

    def _check_all(self, on: bool):
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def result_rows(self) -> tuple[list[dict], list[str]]:
        keep, drop = [], []
        for i, t in enumerate(self.terms):
            cell = lambda c: (self.table.item(i, c).text() if self.table.item(i, c) else "").strip()  # noqa: E731
            if self.table.item(i, 0).checkState() == Qt.Checked and cell(0) and cell(1):
                keep.append({**t, "de": cell(0), "ru": cell(1), "note": cell(2)})
            else:
                drop.append(t["de"])
        return keep, drop

    def _save(self):
        self.accepted_rows, self.skipped = self.result_rows()
        self.accept()
