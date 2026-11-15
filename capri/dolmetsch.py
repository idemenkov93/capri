"""Связка с Dolmetsch — отдельной программой для перевода субтитров встреч.

Субтитры уроков переехали из Капри в Dolmetsch: он читает готовые субтитры Teams или Windows,
почти не грузит ноутбук и пишет текст встречи в общую папку Документы\\Capri.
Капри умеет его запускать: первый раз — открыть и начать перевод, дальше — старт и стоп.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RELEASES = "https://github.com/idemenkov93/dolmetsch/releases"


def candidates(cfg: dict | None = None) -> list[Path]:
    """Где искать Dolmetsch.exe: путь из настроек, файл-подсказка самого Dolmetsch, папки рядом с Капри."""
    out = []
    custom = ((cfg or {}).get("dolmetsch") or {}).get("path")
    if custom:
        out.append(Path(custom))
    hint = Path(os.environ.get("APPDATA") or Path.home()) / "Dolmetsch" / "path.txt"
    try:
        out.append(Path(hint.read_text(encoding="utf-8").strip()))     # Dolmetsch записывает сюда свой путь при запуске
    except OSError:
        pass
    here = Path(sys.executable).resolve().parent
    out += [here.parent / "Dolmetsch" / "Dolmetsch.exe", here / "Dolmetsch" / "Dolmetsch.exe"]
    return out


def find(cfg: dict | None = None) -> Path | None:
    for path in candidates(cfg):
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


def run(command: str = "toggle", cfg: dict | None = None, popen=None) -> str | None:
    """Передать команду Dolmetsch (toggle / start / stop / show). → текст для чата; None — Dolmetsch не найден."""
    exe = find(cfg)
    if exe is None:
        return None
    args = [str(exe)] + ([f"--{command}"] if command in ("toggle", "start", "stop") else [])
    try:
        (popen or subprocess.Popen)(args, cwd=str(exe.parent))
    except OSError as e:
        return f"⚠ Не получилось запустить Dolmetsch: {e}"
    return {"toggle": "🎧 Dolmetsch: перевод субтитров — старт / стоп. Окно перевода внизу экрана.",
            "start": "🎧 Dolmetsch: включаю перевод субтитров. Окно перевода внизу экрана.",
            "stop": "⏹ Dolmetsch: перевод остановлен. Текст встречи — в общей папке."}.get(command, "🎧 Открываю Dolmetsch.")


def missing_text() -> str:
    return ("Dolmetsch не найден. Это отдельная программа для перевода субтитров встреч: "
            f"скачай её ({RELEASES}), распакуй и запусти один раз — дальше я буду включать её сама.")
