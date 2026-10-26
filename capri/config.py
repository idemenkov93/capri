"""Загрузка настроек из config.yaml (с разумными значениями по умолчанию)."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "user_name": "Игорь",
    "ollama": {
        "url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "timeout": 120,
        # qwen3 умеет «размышлять» перед ответом — для ассистента это лишняя задержка
        "think": False,
    },
    "claude_code": {
        "enabled": True,
        "command": "claude",
        # plan = только чтение и ответы, Claude ничего не меняет на диске
        "permission_mode": "plan",
        "timeout": 300,
        "workdir": "~/capri-workspace",
    },
    # Белый список: имя -> команда запуска. Ничего вне списка Капри не запускает.
    "apps": {
        "браузер|browser": "https://www.google.com",
        "блокнот|notepad|editor": "notepad",
        "калькулятор|calculator|rechner": "calc",
        "проводник|explorer": "explorer",
        "терминал|terminal": "wt",
        "steam|стим": "steam://open/main",
    },
    "search_url": "https://www.google.com/search?q={query}",
    "confirm_actions": True,
    "notes_file": "~/.capri/notes.md",
    "avatar": {"size": 140, "custom_image": "", "fps": 12},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def expand(path: str) -> Path:
    """~ и %VAR% раскрываются; относительные пути считаются от папки проекта."""
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    user = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    return _merge(DEFAULTS, user)
