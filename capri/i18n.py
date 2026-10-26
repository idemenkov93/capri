"""Тексты меню и системных сообщений на русском и английском. Язык переключается в меню."""
from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "call": {"ru": "📣 Позвать Капри", "en": "📣 Call Capri"},
    "chat": {"ru": "💬 Написать команду…", "en": "💬 Type a command…"},
    "speak_now": {"ru": "🎤 Сказать голосом", "en": "🎤 Speak"},
    "quiet": {"ru": "🤫 Замолчать", "en": "🤫 Be quiet"},
    "pc_check": {"ru": "🩺 Проверить ПК", "en": "🩺 Check PC"},
    "mic": {"ru": "🎙 Микрофон", "en": "🎙 Microphone"},
    "mic_default": {"ru": "По умолчанию (Windows)", "en": "Windows default"},
    "mic_none": {"ru": "Микрофоны не найдены", "en": "No microphones found"},
    "wake": {"ru": "👂 Слушать «Привет, Капри»", "en": "👂 Listen for “Hey Capri”"},
    "speak_on": {"ru": "🔊 Озвучивать ответы", "en": "🔊 Speak replies"},
    "wander": {"ru": "🚶 Гулять по экрану", "en": "🚶 Walk around"},
    "hide": {"ru": "🙈 Спрятать", "en": "🙈 Hide"},
    "show": {"ru": "👀 Показать", "en": "👀 Show"},
    "voice_lab": {"ru": "🎛 Голос Капри…", "en": "🎛 Capri's voice…"},
    "attitude": {"ru": "😈 Характер", "en": "😈 Attitude"},
    "att_soft": {"ru": "Мягкая", "en": "Soft"},
    "att_sassy": {"ru": "Дерзкая", "en": "Sassy"},
    "att_rude": {"ru": "Грубая", "en": "Rude"},
    "att_set": {"ru": "Характер: {name}.", "en": "Attitude: {name}."},
    "voice_saved": {"ru": "Голос сохранён.", "en": "Voice saved."},
    "language": {"ru": "🌐 Язык / Language", "en": "🌐 Language / Язык"},
    "quit": {"ru": "⏻ Выход", "en": "⏻ Quit"},
    # сообщения
    "mic_set": {"ru": "Микрофон: {name}", "en": "Microphone: {name}"},
    "wake_on": {"ru": "Скажи «Привет, Капри» — и говори команду.", "en": "Say “Hey Capri” (or «Привет, Капри»), then your command."},
    "wake_off": {"ru": "Больше не слушаю «Привет, Капри».", "en": "Not listening for the wake phrase anymore."},
    "wake_missing": {"ru": "Для «Привет, Капри» нужна модель vosk — перезапусти run_capri.bat, он её скачает.",
                     "en": "The wake phrase needs the vosk model — restart run_capri.bat to download it."},
    "lang_set": {"ru": "Меню теперь на русском.", "en": "Menus are in English now."},
    "speak_set_on": {"ru": "Озвучка включена.", "en": "Speech is on."},
    "speak_set_off": {"ru": "Озвучка выключена.", "en": "Speech is off."},
    "pc_check_cmd": {"ru": "проверь компьютер", "en": "check my pc"},
    "checking": {"ru": "Проверяю компьютер… это займёт несколько секунд.", "en": "Checking the PC… this takes a few seconds."},
}


def t(key: str, lang: str, **kw) -> str:
    entry = TEXTS.get(key, {})
    s = entry.get(lang) or entry.get("en") or key
    return s.format(**kw) if kw else s
