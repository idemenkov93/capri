"""Общее для переводчиков."""
from __future__ import annotations

import re
from dataclasses import dataclass


class TranslateError(Exception):
    """kind: timeout | quota | auth | network | model."""

    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


@dataclass
class Result:
    text: str           # перевод (пусто — перевести не удалось)
    engine: str         # кто перевёл: "офлайн", "DeepL" или ""
    ms: int             # сколько миллисекунд занял перевод
    error: str = ""     # что пошло не так (для строки состояния)


class Translator:
    """Интерфейс переводчика DE → RU."""
    name = ""

    def ready(self) -> bool:
        raise NotImplementedError

    def translate(self, texts: list[str], context: str = "") -> list[str]:
        raise NotImplementedError


def chunks(text: str, limit: int = 350) -> list[str]:
    """Очень длинный текст режем на куски по предложениям, а если их нет — по запятым и словам.
    Модель перевода плохо держит больше ~500 токенов за раз."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, cur = [], ""
    for piece in re.split(r"(?<=[.!?…;])\s+|(?<=,)\s+", text):
        while len(piece) > limit:                      # кусок без знаков — режем по словам
            cut = piece.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append((cur + " " + piece[:cut]).strip() if cur else piece[:cut])
            cur, piece = "", piece[cut:].strip()
        if len(cur) + len(piece) + 1 > limit and cur:
            parts.append(cur)
            cur = piece
        else:
            cur = (cur + " " + piece).strip()
    if cur:
        parts.append(cur)
    return parts
