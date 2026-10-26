"""Определение языка ответа и подготовка текста к озвучке. Без внешних библиотек."""
from __future__ import annotations

import re

GERMAN_HINTS = re.compile(r"[äöüß]|\b(ich|und|nicht|ist|das|der|die|du|bitte|danke|ja|nein|mit|für|auf|wir|was|bin|hallo|machen|kann|ein|eine|es|zu|auch|noch|heute)\b", re.I)


def detect_lang(text: str, fallback: str = "en") -> str:
    """Грубо определить язык текста: ru / he / de / en."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return fallback
    cyr = sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters)
    heb = sum("֐" <= c <= "׿" for c in letters)
    if cyr / len(letters) > 0.3:
        return "ru"
    if heb / len(letters) > 0.3:
        return "he"
    hints = GERMAN_HINTS.findall(text)
    if re.search(r"[äöüß]", text, re.I) or len(hints) >= 2:
        return "de"
    return "en"


EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+", re.UNICODE
)


def clean_for_speech(text: str, limit: int = 600) -> str:
    """Убрать то, что плохо звучит вслух: markdown, ссылки, код, эмодзи."""
    text = re.sub(r"```.*?```", " (код смотри в чате) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>|]+", "", text)
    text = EMOJI.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:  # длинное читать вслух утомительно
        cut = text[:limit]
        text = cut[: cut.rfind(".") + 1] or cut
    return text
