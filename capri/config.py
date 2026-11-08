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
        "text_model": "",      # модель для письменных вопросов (посильнее, может быть медленнее); пусто — та же
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
    # Кто решает сложные задачи и смотрит на экран (выбирается в мастере настройки):
    #   claude — Claude Code по подписке Pro/Max; gemini — ключ Google AI Studio (бесплатный или платный);
    #   ollama — только локально. Если выбранного нет (не установлен, нет ключа) — берётся следующий.
    "brain": {
        "provider": "claude",
        "vision_model": "",          # локальная модель со зрением для «посмотри на экран» без Claude/Gemini
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
    # Gemini (Google AI Studio): бесплатный ключ на aistudio.google.com/apikey. Впиши его в чат Капри:
    # «ключ gemini AIza…» — сохранится в Диспетчере учётных данных Windows (не в этом файле).
    "gemini": {
        "enabled": True,
        "model": "gemini-flash-latest",   # бесплатно; лимиты маленькие — для игр и запаса хватает
        "in_games": True,                 # в играх — Gemini вместо Claude: быстрый HTTP-запрос, без процессов
        "when_claude_limit": True,        # лимит Claude кончился — отвечает Gemini
        "timeout": 60,
    },
    # горячие клавиши в любой программе: перевод выделенного и диктовка (держать)
    "hotkeys": {
        "enabled": True,
        "translate": "Ctrl+Shift+T",
        "dictate": "Ctrl+Win",
        "chat": "Ctrl+Shift+Space",   # открыть чат из любой программы
        "convert": "Ctrl+Shift+Q",    # конвертер: выделенные файлы → круговое меню
        "answer": "Ctrl+Shift+1",     # на уроке: «хочу ответить» по-русски → фраза по-немецки на субтитрах
    },
    "games": {
        "enabled": True,       # игровой помощник: сам замечает игру и не мешает ей
        "strict": ["*"],        # лёгкий режим для всех игр: модели уходят из видеокарты, только простые команды
        "cs2_assistant": False,  # таймер бомбы, экономика и статистика CS2 (выключено: только мешало)
        "questions": False,      # в игре — только команды (громкость, музыка, окна), без Gemini/Claude
        "extra": [],           # что ещё считать игрой (если не узнала сама)
        "ignore": [],          # что точно не игра
        "cs2_port": 3127,      # порт для CS2 Game State Integration
        "c4_time": 40,         # таймер бомбы, секунд
        "gsi_delay": 0.5,      # на сколько раньше говорить (сообщение от игры приходит чуть позже)
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


FROZEN = bool(getattr(__import__("sys"), "frozen", False))   # запущено из Capri.exe (PyInstaller)


def _roots() -> tuple[Path, Path]:
    """(PROJECT_ROOT, RESOURCE_ROOT).

    Из исходников (git clone + run_capri.bat) — обе папки = папка проекта, как раньше.
    Из Capri.exe: настройки, data, workspace, свои анимации — в %LOCALAPPDATA%\\Capri (туда можно писать
    без прав администратора и обновление программы их не трогает), а встроенные файлы — рядом с exe."""
    import sys
    if FROZEN:
        res = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        home = Path(os.environ.get("CAPRI_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Capri")
        return home, res
    here = Path(__file__).resolve().parent.parent
    return Path(os.environ["CAPRI_HOME"]) if os.environ.get("CAPRI_HOME") else here, here


PROJECT_ROOT, RESOURCE_ROOT = _roots()


def expand(path: str) -> Path:
    """~ и %VAR% раскрываются; относительные пути считаются от папки проекта."""
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    return p if p.is_absolute() else PROJECT_ROOT / p


def resource(path: str) -> Path:
    """Встроенный файл программы (config.example.yaml, assets/…)."""
    return RESOURCE_ROOT / path


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    user = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    return _merge(DEFAULTS, user)
