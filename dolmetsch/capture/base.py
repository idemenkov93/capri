"""Общие типы для источников субтитров."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Item:
    """Одна реплика в панели субтитров: кто сказал и что."""
    speaker: str
    text: str


@dataclass
class Event:
    """Что источник сообщает дальше по конвейеру.
    partial — фраза ещё дописывается (показываем серым, не переводим);
    final   — законченный кусок (уходит в перевод и в файл)."""
    kind: str
    speaker: str
    text: str
    seen_at: float = 0.0


# конец предложения: точка, «!», «?», «…» перед пробелом или концом текста
SENT_END = re.compile(r"[.!?…]+(?=\s|$)")

# после этих слов точка — не конец предложения («z. B.», «Nr. 5», «bzw.»)
ABBREVIATIONS = {
    "z", "b", "d", "h", "u", "a", "o", "ä", "s", "v", "nr", "bzw", "ca", "ggf", "usw", "etc", "dr", "prof",
    "evtl", "inkl", "exkl", "max", "min", "sog", "vgl", "tel", "str", "abs", "art", "bsp", "zb", "mr", "mrs",
}


def norm(text: str) -> str:
    """Текст для сравнения: без знаков и регистра."""
    return re.sub(r"\W+", " ", text.lower()).strip()


def sentence_ends(text: str, start: int = 0) -> list[int]:
    """Позиции сразу после конца каждого предложения, начиная с start.
    Сокращения («z. B.») и номера («3. Schritt») концом предложения не считаются."""
    ends = []
    for m in SENT_END.finditer(text, start):
        if m.group().startswith("."):
            word = re.search(r"(\w+)$", text[:m.start()])
            if word and (word.group(1).isdigit() or len(word.group(1)) == 1
                         or word.group(1).lower() in ABBREVIATIONS):
                continue
        ends.append(m.end())
    return ends
