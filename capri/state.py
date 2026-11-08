"""Настройки, которые меняются из меню (язык, микрофон, «Привет, Капри»…). Хранятся в data/state.json,
чтобы не трогать config.yaml с твоими комментариями."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_STATE = {"language": "ru", "input_device": None, "wake_word": True, "wake_short": False, "wander": True, "speak": None, "speak_typed": False,
                 "attitude": "sassy", "dance": True, "dance_any": False, "voice_tune": None}


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = dict(DEFAULT_STATE)
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        if not self.data.get("wake_v2"):
            # 1.0.2: просто «Капри» ложно срабатывало десятки раз в день — теперь только «Привет, Капри»
            self.data["wake_short"] = False
            self.data["wake_v2"] = True
        if not self.data.get("typed_v2"):
            # 1.1: на письменный вопрос — ответ текстом, без озвучки (голосом отвечаю на голос)
            self.data["speak_typed"] = False
            self.data["typed_v2"] = True

    def get(self, key, default=None):
        v = self.data.get(key, default)
        return default if v is None else v

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # не смогли сохранить — не беда, просто не запомнится
