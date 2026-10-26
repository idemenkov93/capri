"""Даты и время из фраз: «завтра в 15:00», «в пятницу в 3 часа дня», «28 октября в 9 утра», «послезавтра»."""
from __future__ import annotations

import datetime as dt
import re

WEEKDAYS = [r"понедельник\w*|montag|monday", r"вторник\w*|dienstag|tuesday", r"сред\w+|mittwoch|wednesday",
            r"четверг\w*|donnerstag|thursday", r"пятниц\w+|freitag|friday", r"суббот\w+|samstag|saturday",
            r"воскресень\w+|sonntag|sunday"]
MONTHS = [r"январ\w*|jan\w*", r"феврал\w*|feb\w*", r"март\w*|märz|mar\w*", r"апрел\w*|apr\w*", r"ма[яй]|mai|may",
          r"июн\w*|jun\w*", r"июл\w*|jul\w*", r"август\w*|aug\w*", r"сентябр\w*|sep\w*", r"октябр\w*|okt\w*|oct\w*",
          r"ноябр\w*|nov\w*", r"декабр\w*|dez\w*|dec\w*"]
WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def parse_day(text: str, today: dt.date) -> dt.date | None:
    t = text.lower()
    if re.search(r"послезавтра|übermorgen|day after tomorrow", t):
        return today + dt.timedelta(days=2)
    if re.search(r"\bзавтра|morgen\b|tomorrow", t):
        return today + dt.timedelta(days=1)
    if re.search(r"\bсегодня|heute|today", t):
        return today
    if m := re.search(r"через\s+(\d+)\s+(дн|день|дня)", t):
        return today + dt.timedelta(days=int(m.group(1)))
    if m := re.search(r"\b(\d{1,2})\s+(" + "|".join(f"(?:{x})" for x in MONTHS) + r")", t):
        day = int(m.group(1))
        month = next(i + 1 for i, rx in enumerate(MONTHS) if re.fullmatch(rx, m.group(2)))
        d = dt.date(today.year, month, day)
        return d if d >= today else dt.date(today.year + 1, month, day)
    if m := re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", t):
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        return dt.date(y, int(m.group(2)), int(m.group(1)))
    for i, rx in enumerate(WEEKDAYS):
        if re.search(rf"\b(?:{rx})", t):
            ahead = (i - today.weekday()) % 7 or 7          # «в пятницу» — ближайшая будущая пятница
            return today + dt.timedelta(days=ahead)
    return None


def parse_time(text: str) -> tuple[int, int] | None:
    t = text.lower()
    if m := re.search(r"\b(?:в|um|at)?\s*(\d{1,2})[:.](\d{2})\b", t):
        return int(m.group(1)) % 24, int(m.group(2))
    if m := re.search(r"\b(?:в|um|at)\s+(\d{1,2})\s*(час\w*|утра|дня|вечера|ночи|uhr|am|pm)?\b", t):
        h = int(m.group(1))
        tail = t[m.end():m.end() + 12] + " " + (m.group(2) or "")
        if re.search(r"дня|вечера|pm", tail) and h < 12:
            h += 12
        if re.search(r"ночи", tail) and h == 12:
            h = 0
        return h % 24, 0
    if re.search(r"\bв полдень|mittags|noon", t):
        return 12, 0
    return None


def fmt_day(d: dt.date, lang: str = "ru") -> str:
    return f"{WD_SHORT[d.weekday()]} {d:%d.%m}" if lang == "ru" else f"{d:%a %d.%m}"
