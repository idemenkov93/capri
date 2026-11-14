"""Текст встречи: пишется в файл по ходу, строка за строкой (сбой не теряет написанное).

Формат и папка — как у Capri, чтобы она могла сделать конспект и пополнить бестиарий:
    Документы\\Capri\\Уроки\\2026-11-14 0900 — Тема\\транскрипт.md
Папки Capri нет — пишем в Документы\\Dolmetsch\\.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import os
import re
from pathlib import Path


def documents_dir() -> Path:
    """Папка «Документы» (у Игоря она в OneDrive — спрашиваем у Windows, а не гадаем)."""
    if os.name == "nt":
        try:
            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                            ("d", ctypes.c_ubyte * 8)]
            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


def lessons_root(docs: Path | None = None) -> Path:
    docs = docs or documents_dir()
    return docs / "Capri" / "Уроки" if (docs / "Capri").exists() else docs / "Dolmetsch"


def _stamp(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class Transcript:
    def __init__(self, root: Path | None = None, now=dt.datetime.now):
        self.root, self.now = root, now
        self.path: Path | None = None
        self.started: dt.datetime | None = None
        self.title = ""
        self.lines = 0
        self.error = ""

    def set_title(self, title: str) -> None:
        """Название встречи (из заголовка окна). Действует, пока файл ещё не создан."""
        if self.path is None:
            self.title = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]+', " ", title or "")).strip()[:60]

    def _open(self) -> None:
        self.started = self.now()
        root = self.root or lessons_root()
        name = f"{self.started:%Y-%m-%d %H%M}" + (f" — {self.title}" if self.title else "")
        folder, n = root / name, 2
        while (folder / "транскрипт.md").exists():
            folder, n = root / f"{name} ({n})", n + 1
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "транскрипт.md"
        self.path.write_text(
            f"# Транскрипт {self.started:%d.%m.%Y %H:%M}\n\n"
            "_Текст из субтитров встречи (Dolmetsch). Звук не сохранялся._\n\n", encoding="utf-8")

    def add(self, speaker: str, text: str, translation: str) -> None:
        """Дописать реплику. Ошибка записи не останавливает перевод — только запоминается."""
        try:
            if self.path is None:
                self._open()
            at = _stamp((self.now() - self.started).total_seconds())
            block = f"**[{at}] {speaker or 'Говорящий'}:** {text}\n"
            if translation:
                block += f"> {translation}\n"
            with self.path.open("a", encoding="utf-8") as f:
                f.write(block + "\n")
            self.lines += 1
            self.error = ""
        except OSError as e:
            self.error = f"текст встречи не пишется: {e.strerror or e}"

    def close(self) -> Path | None:
        """Закончить встречу: следующий старт начнёт новый файл."""
        path, self.path, self.started, self.lines = self.path, None, None, 0
        return path
