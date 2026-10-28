"""Быстрые ответы без нейросети: время и дата, простой счёт, «напомни через …», будильник, сведения о ПК.

Маленькая локальная модель не знает, который час, и часто ошибается в арифметике — такие вопросы
лучше решать правилами: мгновенно и без выдумок.
"""
from __future__ import annotations

import datetime as dt
import re

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

RE_TIME = re.compile(r"^(?:сколько\s+(?:сейчас\s+)?времени|который\s+(?:сейчас\s+)?час|какое\s+(?:сейчас\s+)?время|"
                     r"сколько\s+на\s+часах|what\s+time\s+is\s+it|wie\s+spät\s+ist\s+es)\b", re.I)
RE_DATE = re.compile(r"^(?:какое\s+(?:сегодня\s+)?число|какая\s+(?:сегодня\s+)?дата|какое\s+сегодня\s+число|"
                     r"какой\s+сегодня\s+день(?!\s+недели)|what(?:'s| is)\s+the\s+date|welches\s+datum)\b", re.I)
RE_WEEKDAY = re.compile(r"^(?:какой\s+(?:сегодня\s+)?день\s+недели|какой\s+сегодня\s+день\s+недели|what\s+day\s+is\s+it)\b", re.I)


def time_answer(text: str, now: dt.datetime | None = None) -> str | None:
    now = now or dt.datetime.now()
    t = text.strip(" .!?").lower().replace("ё", "е")
    if RE_TIME.search(t):
        return f"Сейчас {now:%H:%M}."
    if RE_WEEKDAY.search(t):
        return f"Сегодня {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}."
    if RE_DATE.search(t):
        return f"Сегодня {now.day} {MONTHS[now.month - 1]} {now.year}, {WEEKDAYS[now.weekday()]}."
    return None


# ------------------------------------------------------------------ счёт
OPS = (("в квадрате", "**2"), ("в кубе", "**3"), ("умножить на", "*"), ("умножь на", "*"), ("помножить на", "*"),
       ("разделить на", "/"), ("подели на", "/"), ("делить на", "/"), ("плюс", "+"), ("минус", "-"),
       ("процентов от", "*0.01*"), ("процента от", "*0.01*"), ("процент от", "*0.01*"),
       (" x ", "*"), (" х ", "*"), ("×", "*"), ("÷", "/"), (":", "/"))
RE_CALC = re.compile(r"^(?:посчитай|сосчитай|вычисли|сколько\s+будет|calculate|what\s+is|was\s+ist)\s+(.+)$", re.I)


def calc(text: str) -> str | None:
    """«посчитай 25 умножить на 4» → «25 × 4 = 100». Только числа и + − × ÷, без eval чужого кода."""
    m = RE_CALC.match(text.strip(" .!?"))
    if not m:
        return None
    from .commands import numbers
    expr = " " + m.group(1).lower().replace("ё", "е").replace(",", ".") + " "
    for word, op in OPS:
        expr = expr.replace(word, f" {op} ")
    # слова-числа → цифры («двадцать пять» → 25)
    parts = re.split(r"(\*\*\d|\*0\.01\*|[-+*/()])", expr)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if re.fullmatch(r"\*\*\d|\*0\.01\*|[-+*/()]", p) or re.fullmatch(r"\d+(?:\.\d+)?", p):
            out.append(p)
            continue
        n = numbers(p)
        if len(n) != 1:
            return None
        out.append(str(n[0]))
    safe = " ".join(out)
    if not re.fullmatch(r"[\d.\s+\-*/()]+", safe) or len(safe) > 80 or "**" in safe.replace("**2", "").replace("**3", ""):
        return None
    try:
        value = eval(safe, {"__builtins__": {}}, {})          # noqa: S307 — только цифры и + − × ÷ (проверено выше)
    except (SyntaxError, ZeroDivisionError, TypeError, ValueError, OverflowError):
        return "На ноль делить не буду." if "/ 0" in safe else None
    if isinstance(value, float):
        value = round(value, 6)
        if value.is_integer():
            value = int(value)
    shown = safe.replace(" **2", "²").replace(" **3", "³").replace(" *0.01* ", "% от ").replace("*", "×").replace("/", "÷")
    return f"{shown} = {value}".replace(".", ",") if isinstance(value, float) else f"{shown} = {value}"


# ------------------------------------------------------------------ напоминания и будильник
RE_REMIND = re.compile(r"^(?:напомни|напомнишь|разбуди)(?:\s+мне|\s+меня)?\s+через\s+(.+?)(?:\s*,\s*|\s+(?:что(?:бы)?|о том,? что|про)\s+|\s+)?"
                       r"(?P<what>(?:(?:позвонить|купить|сделать|выключить|проверить|забрать|написать|отправить|вынуть|"
                       r"полить|покормить|выпить|принять|сходить|встать|начать|закончить|посмотреть|ответить)\b.*)?)$", re.I)
RE_ALARM = re.compile(r"^(?:поставь\s+|заведи\s+|установи\s+)?(?:будильник|разбуди(?:\s+меня)?)\s+(?:на|в)\s+(.+)$", re.I)


def reminder(text: str, now: dt.datetime | None = None) -> tuple[float, str] | None:
    """→ (через сколько минут, что сказать) или None."""
    from .commands import parse_seconds
    from .when import parse_time
    now = now or dt.datetime.now()
    t = text.strip(" .!?")
    if m := RE_REMIND.match(t):
        secs = parse_seconds(m.group(1))
        if secs:
            what = (m.group("what") or "").strip()
            label = f"Напоминаю: {what}." if what else ("Подъём! Время вставать." if t.lower().startswith("разбуди") else "Время вышло!")
            return secs / 60, label
    if m := RE_ALARM.match(t):
        hm = parse_time("в " + m.group(1)) or parse_time(m.group(1))
        if hm:
            when = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            if when <= now:
                when += dt.timedelta(days=1)
            return (when - now).total_seconds() / 60, f"Подъём! Уже {hm[0]:02d}:{hm[1]:02d}."
    return None


# ------------------------------------------------------------------ сведения о ПК
RE_GPU = re.compile(r"какая\s+(?:у\s+меня\s+)?(?:стоит\s+)?видеокарт\w*|что\s+за\s+видеокарт\w*|модель\s+видеокарт\w*", re.I)
RE_CPU = re.compile(r"какой\s+(?:у\s+меня\s+)?(?:стоит\s+)?процессор|что\s+за\s+процессор", re.I)
RE_DISK = re.compile(r"(?:сколько|много\s+ли)\s+(?:свободного\s+)?места\s+(?:на\s+)?(?:диск\w*|компьютер\w*|ссд|ssd)?|"
                     r"свободн\w*\s+мест\w*\s+на\s+диск\w*", re.I)
RE_RAM = re.compile(r"сколько\s+(?:свободной\s+|у\s+меня\s+)?(?:оперативк\w*|оперативной\s+памяти|памяти|озу|ram)"
                    r"|(?:оперативк\w*|память)\s+(?:занята|свободна)", re.I)


def pc_info(text: str) -> str | None:
    t = text.strip(" .!?")
    try:
        import psutil
    except ImportError:
        psutil = None
    if RE_GPU.search(t):
        from . import gpu
        name = gpu.name()
        if not name:
            return "Не вижу видеокарту NVIDIA. Полная проверка — «проверь компьютер»."
        g = gpu.read() or {}
        temp = f", сейчас {g['temp']}°" if g.get("temp") else ""
        return f"Видеокарта: {name}{temp}."
    if RE_CPU.search(t):
        import os
        import platform
        name = platform.processor() or "процессор"
        load = f", загрузка {psutil.cpu_percent(interval=0.3):.0f}%" if psutil else ""
        return f"Процессор: {name}, {os.cpu_count()} потоков{load}."
    if RE_RAM.search(t) and psutil:
        vm = psutil.virtual_memory()
        return f"Оперативка: занято {vm.used / 2**30:.1f} из {vm.total / 2**30:.0f} ГБ, свободно {vm.available / 2**30:.1f} ГБ."
    if RE_DISK.search(t) and psutil:
        parts = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in p.opts or not p.fstype:
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            parts.append(f"{p.mountpoint.rstrip(chr(92))} свободно {u.free / 2**30:.0f} из {u.total / 2**30:.0f} ГБ")
        return ("Диски: " + "; ".join(parts) + ".") if parts else None
    return None
