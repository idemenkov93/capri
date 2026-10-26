"""Настройки, которые меняются из меню (язык, микрофон, «Привет, Капри»…). Хранятся в data/state.json,
чтобы не трогать config.yaml с твоими комментариями."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_STATE = {"language": "ru", "input_device": None, "wake_word": True, "wander": True, "speak": None,
                 "attitude": "sassy", "voice_tune": None}


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = dict(DEFAULT_STATE)
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

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
