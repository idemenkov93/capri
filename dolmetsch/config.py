"""Настройки: %APPDATA%\\Dolmetsch\\config.json. Ключ DeepL — отдельно, в хранилище Windows (keyring)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "hotkeys": {
        "toggle": "Win+Alt+J",      # старт / стоп перевода
        "window": "Win+Alt+I",      # показать / спрятать окно
        "profile": "Win+Alt+Y",     # профиль «Урок» ↔ «Важное»
    },
    "profile": "auto",              # auto | lesson | important
    "lesson_hours": {"days": [0, 1, 2, 3, 4], "from": 9, "to": 17},   # будни 9–17 — профиль «Урок»
    "autostart": True,              # начинать перевод сразу при запуске программы
    "save_transcript": True,        # сохранять текст встречи в файл
    "show_original": True,          # показывать немецкий оригинал под переводом
    "hide_from_capture": True,      # не показывать окно при демонстрации экрана
    "font_size": 15,
    "opacity": 0.9,
    "lines": 3,                     # сколько последних переводов держать в окне
    "window": None,                 # положение и размер окна: [x, y, ширина, высота]
    "device": "auto",               # auto | cpu | cuda
    "threads": 0,                   # 0 = половина потоков процессора
    "live_partial": "auto",         # переводить недописанную фразу: auto (только на видеокарте) | on | off
    "deepl": {},                    # служебное: счётчик символов за месяц
}

KEYRING_SERVICE, KEYRING_USER = "dolmetsch", "deepl"


def app_dir() -> Path:
    path = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Dolmetsch"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "config.json"
        self.data = copy.deepcopy(DEFAULTS)
        try:
            _merge(self.data, json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass                            # файла нет или он испорчен — работаем с настройками по умолчанию

    def __getitem__(self, key):
        return self.data[key]

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def get_deepl_key() -> str:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
    except Exception:  # noqa: BLE001
        return ""


def set_deepl_key(key: str) -> bool:
    try:
        import keyring
        if key.strip():
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.strip())
        else:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        return True
    except Exception:  # noqa: BLE001
        return False
