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
        "workdir": "workspace",
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
    "city": "München",   # для «какая погода» без города
    # свои сайты: «как ты говоришь|синонимы»: адрес. Встроенные (ютуб, гитхаб, почта…) знает и так.
    "sites": {},
    "confirm_actions": True,
    "notes_file": "data/notes.md",
    "avatar": {"size": 150, "custom_image": "", "fps": 12, "wander": True, "desktop_mode": "auto"},
    "ui": {"style": "hud"},  # hud — панель «Personal Link», classic — простой чат
    "games": {
        "enabled": True,       # игровой помощник: сам замечает игру и не мешает ей
        "strict": ["cs2.exe"],  # строгий режим (ноль нагрузки на видеокарту): можно дописать свои exe
        "extra": [],           # что ещё считать игрой (если не узнала сама)
        "ignore": [],          # что точно не игра
        "cs2_port": 3127,      # порт для CS2 Game State Integration
        "c4_time": 40,         # таймер бомбы, секунд
        "gsi_delay": 0.3,      # на сколько раньше говорить (сообщение от игры приходит чуть позже)
    },
    "voice": {
        "enabled": True,
        "hotkey": "Ctrl+Space",
        "whisper_model": "small",      # tiny | base | small | medium — больше = точнее, но медленнее
        "whisper_device": "auto",      # auto — видеокарта NVIDIA, если есть (requirements-gpu.txt), иначе процессор
        "languages": ["ru", "de", "en", "he"],
        "preferred_language": "ru",     # получает фору при распознавании коротких фраз
        "tts_engine": "edge",           # edge — живые нейроголоса (нужен интернет), piper — офлайн
        "edge_voices": {
            "ru": "ru-RU-SvetlanaNeural",
            "de": "de-DE-AmalaNeural",
            "en": "en-US-AvaNeural",
            "he": "he-IL-HilaNeural",
        },
        "edge_rate": "+8%",             # чуть быстрее обычного — бодрее
        "edge_pitch": "+6Hz",           # чуть выше — моложе и звонче
        "speak": True,                 # озвучивать ответы
        "speak_only_voice": True,      # озвучивать, только если спросил голосом
        "max_speech_chars": 600,
        "models_dir": "data/models",
        "piper_voices": {
            "ru": "ru_RU-irina-medium",
            "de": "de_DE-kerstin-low",
            "en": "en_US-amy-medium",
        },
    },
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
