"""🔕 «Не беспокоить» и учебный таймер.

Пока включено: Капри не напоминает об отдыхе, не танцует и не гуляет по экрану, не пишет про обновления.
Твои таймеры и будильники звенят, на «Привет, Капри» она отвечает как обычно.
Время хранится в data/state.json (dnd_until): переживает перезапуск. -1 — «пока не выключу».
"""
from __future__ import annotations

import datetime as dt
import re

FOREVER = -1.0
EVENING, MORNING = 18, 8

# что предлагать в меню: (подпись, минуты | "evening" | "morning" | "forever")
CHOICES = (("30 минут", 30), ("1 час", 60), ("2 часа", 120), ("8 часов", 480),
           ("до вечера (18:00)", "evening"), ("до утра (8:00)", "morning"), ("пока не выключу", "forever"))

RE_DND = re.compile(
    r"^(?:не\s+беспокой(?:те)?(?:\s+меня)?|режим\s+не\s+беспокоить|включи\s+(?:режим\s+)?не\s+беспокоить|"
    r"не\s+мешай(?:\s+мне)?|тишина)(?:\s+(?P<what>.+))?$", re.I)
RE_DND_OFF = re.compile(r"^(?:можно\s+беспокоить|выключи\s+(?:режим\s+)?не\s+беспокоить|отключи\s+не\s+беспокоить|"
                        r"хватит\s+тишины|снова\s+можно)$", re.I)
RE_STUDY = re.compile(r"^(?:начни|начинаю|включи|запусти)?\s*(?:учёб[уаы]|учеб[уаы]|занятие|помидор\w*|помодоро|"
                      r"режим\s+фокус\w*|фокус)(?:\s+на)?\s*(?P<what>.+)?$", re.I)


def until(choice, now: dt.datetime | None = None) -> float:
    """Минуты или «evening» / «morning» / «forever» → время окончания (timestamp) или FOREVER."""
    now = now or dt.datetime.now()
    if choice == "forever":
        return FOREVER
    if choice == "evening":
        end = now.replace(hour=EVENING, minute=0, second=0, microsecond=0)
        if end <= now:                      # уже вечер — тогда до утра
            return until("morning", now)
        return end.timestamp()
    if choice == "morning":
        end = now.replace(hour=MORNING, minute=0, second=0, microsecond=0)
        if end <= now:
            end += dt.timedelta(days=1)
        return end.timestamp()
    return (now + dt.timedelta(minutes=float(choice))).timestamp()


def parse(what: str | None):
    """«на час», «30 минут», «до вечера», «до утра», «на полтора часа», '' → выбор для until()."""
    from . import commands as C
    w = (what or "").lower().strip(" .!?")
    w = re.sub(r"^(?:на|в\s+течение)\s+", "", w)
    if not w:
        return 60                                   # просто «не беспокой» — на час
    if re.search(r"вечер", w):
        return "evening"
    if re.search(r"утр|завтра", w):
        return "morning"
    if re.search(r"пока не|всегда|навсегда|до отмены", w):
        return "forever"
    w = w.replace("полчаса", "30 минут").replace("полтора часа", "90 минут")
    if re.fullmatch(r"час\w*", w):
        return 60
    secs = C.parse_seconds(w)
    if secs:
        return max(1, round(secs / 60))
    nums = C.numbers(w)
    if nums:
        return nums[0] * (60 if re.search(r"час", w) else 1)
    return None


def active(dnd_until: float | None, now: float | None = None) -> bool:
    if not dnd_until:
        return False
    if dnd_until == FOREVER:
        return True
    return (now if now is not None else dt.datetime.now().timestamp()) < dnd_until


def describe(dnd_until: float) -> str:
    """«до 18:00», «до завтра 8:00», «пока не выключишь»."""
    if dnd_until == FOREVER:
        return "пока не выключишь"
    end = dt.datetime.fromtimestamp(dnd_until)
    day = "" if end.date() == dt.date.today() else "завтра "
    return f"до {day}{end:%H:%M}"
