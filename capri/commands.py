"""Мгновенные системные команды: музыка, громкость, яркость, клавиши, проверка ПК…

Разбор фраз (русский / Deutsch / English) живёт здесь и не зависит от Windows — его легко тестировать.
Выполнение — в capri/system.py. Идеи и часть словаря — из kystt29/capricorn (MIT).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Command:
    op: str              # pause | play | next | previous | now_playing | seek_to | seek_by | volume_* | mute | ...
    value: float | None = None
    keys: list[str] | None = None
    repeat: int = 1


def lang_of(text: str) -> str:
    if re.search(r"[֐-׿]", text):
        return "he"
    if re.search(r"[А-Яа-яЁё]", text):
        return "ru"
    if re.search(r"[äöüß]", text, re.I) or re.search(r"\b(bitte|lauter|leiser|mach|drück\w*|wie|was|ist|der|die|das)\b", text, re.I):
        return "de"
    return "en"


# ---------------------------------------------------------------- числа
NUM = {
    "ноль": 0, "нуль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
    "null": 0, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "zehn": 10, "zwanzig": 20, "dreißig": 30,
    "vierzig": 40, "fünfzig": 50, "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90, "hundert": 100,
}
TENS = {20, 30, 40, 50, 60, 70, 80, 90}


def numbers(text: str) -> list[int]:
    """«три четырнадцать» → [3, 14], «двадцать пять» → [25], «3 14» → [3, 14]."""
    out: list[int] = []
    tens = False
    for w in re.split(r"[^\w]+", text.lower().replace("ё", "е")):
        if not w:
            continue
        if w.isdigit():
            out.append(int(w))
            tens = False
            continue
        v = NUM.get(w)
        if v is None:
            tens = False
            continue
        if tens and 1 <= v <= 9:
            out[-1] += v
            tens = False
        else:
            out.append(v)
            tens = v in TENS
    return out


def level(text: str) -> int | None:
    t = text.lower()
    if re.search(r"максимум|на полную|\bmax", t):
        return 100
    if re.search(r"минимум|\bmin", t):
        return 0
    if re.search(r"половин|\bhalf|\bhälfte", t):
        return 50
    n = numbers(t)
    return min(100, n[0]) if n else None


def parse_seconds(text: str) -> int | None:
    """«3:14», «три минуты четырнадцать секунд», «полминуты», «минуту», «тайминг 314» → секунды."""
    t = text.lower().replace("ё", "е")
    m = re.search(r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?", t)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        return a * 3600 + b * 60 + int(c) if c else a * 60 + b
    if "полминут" in t or "half a minute" in t:
        return 30
    if "полторы минут" in t:
        return 90
    total, found, rest = 0, False, t
    for unit, mult in ((r"час\w*|hours?|stunden?", 3600), (r"мин\w*|minutes?|minuten?", 60),
                       (r"сек\w*|seconds?|sekunden?", 1)):
        um = re.search(rf"((?:\w+\s+){{0,2}})(?<!\w)(?:{unit})", rest)
        if not um:
            continue
        n = numbers(um.group(1))
        total += (n[-1] if n else 1) * mult
        found = True
        rest = rest.replace(um.group(0), " ", 1)
    if found:
        return total
    n = numbers(t)
    if len(n) == 2:
        return n[0] * 60 + n[1]
    if len(n) == 1:
        v = n[0]
        return v // 100 * 60 + v % 100 if v >= 100 and v % 100 < 60 else v
    return None


# ---------------------------------------------------------------- клавиши
VK = {
    "win": 0x5B, "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "printscreen": 0x2C, "capslock": 0x14,
}
EXTENDED = {"win", "delete", "insert", "home", "end", "pageup", "pagedown", "up", "down", "left", "right", "printscreen"}
KEY_WORDS = {
    **dict.fromkeys(["вин", "виндоус", "виндовс", "винда", "windows", "win", "super"], "win"),
    **dict.fromkeys(["контрол", "контрл", "контроль", "ctrl", "control", "strg"], "ctrl"),
    **dict.fromkeys(["шифт", "shift"], "shift"), **dict.fromkeys(["альт", "alt"], "alt"),
    **dict.fromkeys(["таб", "tab"], "tab"), **dict.fromkeys(["энтер", "ентер", "ввод", "enter", "return"], "enter"),
    **dict.fromkeys(["эскейп", "ескейп", "escape", "esc"], "esc"),
    **dict.fromkeys(["пробел", "space", "leertaste"], "space"),
    **dict.fromkeys(["бэкспейс", "бекспейс", "backspace"], "backspace"),
    **dict.fromkeys(["делит", "делете", "delete", "del", "entf"], "delete"),
    **dict.fromkeys(["инсерт", "insert"], "insert"), **dict.fromkeys(["хоум", "home"], "home"),
    **dict.fromkeys(["энд", "end"], "end"), "pageup": "pageup", "pagedown": "pagedown",
    **dict.fromkeys(["вверх", "наверх", "up"], "up"), **dict.fromkeys(["вниз", "down"], "down"),
    **dict.fromkeys(["влево", "налево", "left"], "left"), **dict.fromkeys(["вправо", "направо", "right"], "right"),
    "printscreen": "printscreen", "capslock": "capslock",
}
LETTERS = {
    "эй": "a", "би": "b", "си": "c", "цэ": "c", "це": "c", "ди": "d", "дэ": "d", "де": "d", "е": "e", "эф": "f",
    "джи": "g", "же": "g", "эйч": "h", "аш": "h", "ай": "i", "джей": "j", "кей": "k", "кэй": "k", "ка": "k",
    "эл": "l", "эль": "l", "эм": "m", "эн": "n", "оу": "o", "о": "o", "пи": "p", "пэ": "p", "кью": "q", "ку": "q",
    "ар": "r", "эр": "r", "эс": "s", "ти": "t", "тэ": "t", "ю": "u", "вэ": "v", "ви": "v", "дабл-ю": "w",
    "икс": "x", "вай": "y", "игрек": "y", "зэт": "z", "зет": "z", "зед": "z",
}
FILLER = {"клавишу", "клавиши", "кнопку", "кнопки", "на", "и", "плюс", "стрелку", "стрелка", "стрелочку",
          "the", "key", "keys", "and", "plus", "arrow", "die", "taste", "und"}


def parse_keys(text: str) -> tuple[list[str], int] | None:
    """«контрол шифт эскейп», «ctrl+shift+esc», «эф пять», «пробел три раза» → (['ctrl','shift','esc'], повторы)."""
    t = re.sub(r"[+,._]", " ", text.lower().replace("ё", "е")).strip()
    repeat = 1
    rm = re.search(r"\s+(\S+)\s+(раз|раза|times|mal)$", t)
    if rm:
        n = numbers(rm.group(1))
        repeat = max(1, min(20, n[0] if n else 1))
        t = t[: rm.start()]
    for a, b in (("пейдж ап", "pageup"), ("page up", "pageup"), ("пейдж даун", "pagedown"), ("page down", "pagedown"),
                 ("принт скрин", "printscreen"), ("print screen", "printscreen"), ("капс лок", "capslock"),
                 ("caps lock", "capslock"), ("дабл ю", "дабл-ю"), ("бэк спейс", "backspace"), ("back space", "backspace")):
        t = t.replace(a, b)
    words = t.split()
    keys: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in KEY_WORDS:
            keys.append(KEY_WORDS[w])
        elif re.fullmatch(r"[fф]([1-9]|1[0-2])", w):
            keys.append("f" + w[1:])
        elif w in ("эф", "f") and nxt and numbers(nxt) and 1 <= numbers(nxt)[0] <= 12:
            keys.append(f"f{numbers(nxt)[0]}")
            i += 1
        elif w in FILLER and not (w == "и" and i == len(words) - 1):
            pass
        elif w in LETTERS:
            keys.append(LETTERS[w])
        elif re.fullmatch(r"[a-z0-9]", w):
            keys.append(w)
        elif w in NUM and NUM[w] < 10:
            keys.append(str(NUM[w]))
        else:
            return None
        i += 1
    if not keys or len(keys) > 5:
        return None
    return keys, repeat


def key_codes(keys: list[str]) -> list[tuple[int, bool]]:
    """['ctrl','f5','a'] → [(код, extended?)]."""
    out = []
    for k in keys:
        if k in VK:
            out.append((VK[k], k in EXTENDED))
        elif re.fullmatch(r"f\d{1,2}", k):
            out.append((0x6F + int(k[1:]), False))
        else:
            out.append((ord(k.upper()), False))
    return out


# ---------------------------------------------------------------- разбор фразы
def _clean(text: str) -> str:
    t = text.lower().replace("ё", "е").strip()
    t = re.sub(r"^(привет\s*,?\s*)?(капри|capri|kapri)[\s,!.:-]*", "", t)
    t = re.sub(r"^(пожалуйста|please|bitte)\s+", "", t)
    t = re.sub(r"[\s,!.?]*(пожалуйста|please|bitte)?[\s!.?]*$", "", t)
    return t.strip()


def match(text: str) -> Command | None:
    """Прямая системная команда или None (тогда фразу разбирают другие правила или модель)."""
    t = _clean(text)
    if not t or len(t) > 80:
        return None

    # «нажми play», «нажми на паузу» — это про плеер, а не про клавиши
    if re.fullmatch(r"(?:нажми|нажать|press|hit)\s+(?:на\s+)?(?:play|плей|плэй|воспроизведение)(?:\s+(?:в|на|in)\s+.+)?", t):
        return Command("play")
    if re.fullmatch(r"(?:нажми|нажать|press|hit)\s+(?:на\s+)?(?:pause|паузу|пауза)(?:\s+(?:в|на|in)\s+.+)?", t):
        return Command("pause")
    if m := re.match(r"^(?:нажми|нажать|зажми|press|hit|drück|drücke)\s+(.+)$", t):
        parsed = parse_keys(m.group(1))
        return Command("keys", keys=parsed[0], repeat=parsed[1]) if parsed else None

    # --- музыка и видео
    if re.search(r"^(что|чо|че)\s+(сейчас\s+|щас\s+|у нас\s+)?(играет|звучит|за (песня|трек|музыка))|"
                 r"^(какая|какой) (это |сейчас )?(песня|трек)|^what('s| is)( this| now)? (playing|song)|^was läuft", t):
        return Command("now_playing")
    if re.fullmatch(r"пауза|на паузу|поставь (на )?паузу|постав\w* (музыку |видео )?на паузу|"
                    r"останови (музыку|видео|трек)|выключи (музыку|видео)|pause( (the )?(music|video))?|pausier\w*", t):
        return Command("pause")
    if re.fullmatch(r"продолжи|продолжай|играй|сними с паузы|сними паузу|включи дальше|продолжи (музыку|видео)|"
                    r"(воспроизвед\w*|поспроизвед\w*|запусти|включи|врубай|вруби|поставь)\s+(какую-?нибудь\s+|мою\s+|немного\s+)?(музык\w*|песн\w*|трек\w*)|"
                    r"воспроизведени\w* музык\w*|музыку|"
                    r"resume|play|play (some )?music|unpause|continue playing|weiter( abspielen)?|fortsetzen|musik (an|abspielen)", t):
        return Command("play")
    if re.fullmatch(r"следующ\w* (трек|песн\w*|видео|композици\w*)|пропусти( трек| песню)?|переключи (трек|песню)|"
                    r"next( track| song| video)?|skip( (this|the) (song|track))?|nächst\w* (titel|lied|song)", t):
        return Command("next")
    if re.fullmatch(r"предыдущ\w* (трек|песн\w*|видео)|прошл\w* (трек|песн\w*)|верни (трек|песню)|"
                    r"previous( track| song)?|last (track|song)|vorherig\w* (titel|lied)", t):
        return Command("previous")
    if re.match(r"^(включи|начни|поставь|запусти)\s+(сначала|с начала|заново)|^(start over|restart the (song|video))$|^von vorne$", t):
        return Command("seek_to", 0)
    if m := re.match(r"^(перемотай|промотай|отмотай|мотни|skip|jump|spul\w*)\s+(.+)$", t):
        if not re.search(r"\bна\s+\d{1,2}[:.]\d{2}", t):
            back = bool(re.search(r"назад|обратно|back|zurück", t)) or m.group(1) == "отмотай"
            secs = parse_seconds(re.sub(r"вперед|назад|обратно|forward|backwards?|zurück|\bvor\b", " ", m.group(2)))
            if secs:
                return Command("seek_by", -secs if back else secs)
    if m := re.match(r"^(?:поставь|перемотай|включи|начни|go to|jump to|skip to|spring zu)\s+(?:на|с|со|to|auf|zu)\s+(.+)$", t):
        if re.search(r"\d|минут|секунд|minute|second|час", m.group(1)) and not re.search(r"громкост|яркост", t):
            secs = parse_seconds(re.sub(r"^тайминг\s+", "", m.group(1)))
            if secs is not None:
                return Command("seek_to", secs)

    # --- громкость
    if re.match(r"^(какая|какой|сколько) (сейчас |у меня )?([гк]ромкость|звук)|^what('s| is) the volume|^wie laut", t):
        return Command("volume_get")
    if re.fullmatch(r"(выключи|отключи|убери) звук|без звука|mute|stumm( schalten)?|ton aus", t):
        return Command("mute")
    if re.fullmatch(r"(включи|верни) звук|unmute|ton an", t):
        return Command("unmute")
    if m := re.fullmatch(r"(?:(?:сделай|поставь|установи|set)\s+)?(?:[гк]ромкость|звук|volume|lautstärke)\s+"
                         r"(?:на\s+|to\s+|auf\s+)?(.+?)(?:\s*(?:процент\w*|%|percent|prozent))?", t):
        if (v := level(m.group(1))) is not None:
            return Command("volume_set", v)
    if m := re.fullmatch(r"(?:сделай\s+)?(громче|погромче|тише|потише)(?:\s+на\s+(.+?))?(?:\s*(?:процент\w*|%))?|"
                         r"(louder|quieter|turn it (up|down)|volume (up|down)|lauter|leiser)", t):
        down = bool(re.search(r"тише|quieter|down|leiser", t))
        n = level(m.group(2)) if m.group(2) else None
        return Command("volume_change", (-1 if down else 1) * (n or 10))

    # --- яркость
    if m := re.fullmatch(r"(?:(?:сделай|поставь|установи|set)\s+)?(?:яркость|brightness|helligkeit)\s+"
                         r"(?:на\s+|to\s+|auf\s+)?(.+?)(?:\s*(?:процент\w*|%|percent|prozent))?", t):
        if (v := level(m.group(1))) is not None:
            return Command("brightness_set", v)
    if re.fullmatch(r"(?:сделай\s+)?(?:экран\s+)?(ярче|поярче|темнее|потемнее)(?:\s+экран)?|brighter|darker|heller|dunkler", t):
        return Command("brightness_change", -15 if re.search(r"темнее|darker|dunkler", t) else 15)

    # --- компьютер
    if re.search(r"(проверь|проверить|проанализируй|продиагностируй|диагностик\w*|анализ)\s+(мой\s+|мне\s+)?"
                 r"(компьютер|комп|пк|систем\w*|ноутбук|ноут)|что (грузит|тормозит|нагружает) (компьютер|комп|пк|систему|ноут)|"
                 r"почему (тормозит|лагает|виснет) (компьютер|комп|пк|ноут)|^(check|diagnose|scan) (my |the )?(pc|computer|system)|"
                 r"^(prüf\w*|check) (den |meinen )?(pc|computer|rechner)", t):
        return Command("pc_check")
    if re.fullmatch(r"(сделай |сделать )?(скриншот|снимок экрана|скрин)( экрана)?|(take a )?screenshot|"
                    r"(mach )?(einen )?(screenshot|bildschirmfoto)", t):
        return Command("screenshot")
    if re.fullmatch(r"(заблокируй|заблокировать|блокируй) (компьютер|комп|пк|экран|ноут)|lock (the |my )?(computer|pc|screen)|"
                    r"sperr\w* (den )?(bildschirm|pc|computer)", t):
        return Command("lock")
    return None


# ---------------------------------------------------------------- ответы
def fmt_time(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def reply(cmd: Command, lang: str, result: dict) -> str:
    """Короткая фраза о результате на языке пользователя (ru / de / en; иврит → английский)."""
    def L(ru, en, de):
        return ru if lang == "ru" else de if lang == "de" else en

    op, v = cmd.op, cmd.value
    if result.get("error"):
        return L(f"Не вышло: {result['error']}", f"Didn't work: {result['error']}", f"Hat nicht geklappt: {result['error']}")
    if op == "keys":
        k = "+".join(cmd.keys or [])
        return L(f"Нажала {k}.", f"Pressed {k}.", f"{k} gedrückt.")
    if op in ("pause", "play", "next", "previous") and result.get("nothing"):
        return L("Сейчас ничего не играет.", "Nothing is playing.", "Gerade läuft nichts.")
    if op == "pause":
        return L("Пауза.", "Paused.", "Pausiert.")
    if op == "play":
        return L("Играет дальше.", "Playing.", "Läuft weiter.")
    if op in ("next", "previous"):
        what = result.get("track")
        base = L("Следующий", "Next one", "Nächster") if op == "next" else L("Предыдущий", "Going back", "Zurück")
        return f"{base}." if not what else f"{base}: {what}."
    if op == "seek_to":
        if result.get("unsupported"):
            return L("Этот плеер не даёт перематывать.", "This player doesn't allow seeking.", "Dieser Player kann nicht spulen.")
        return L("С начала.", "From the top.", "Von vorne.") if not v else L(f"Поставила на {fmt_time(v)}.", f"Jumped to {fmt_time(v)}.", f"Auf {fmt_time(v)}.")
    if op == "seek_by":
        if result.get("unsupported"):
            return L("Этот плеер не даёт перематывать.", "This player doesn't allow seeking.", "Dieser Player kann nicht spulen.")
        d = fmt_time(abs(v or 0))
        return L(f"Назад на {d}.", f"Back {d}.", f"{d} zurück.") if (v or 0) < 0 else L(f"Вперёд на {d}.", f"Forward {d}.", f"{d} vor.")
    if op == "now_playing":
        what = result.get("track")
        if not what:
            return L("Сейчас ничего не играет.", "Nothing is playing.", "Gerade läuft nichts.")
        return L(f"Сейчас играет: {what}.", f"Now playing: {what}.", f"Gerade läuft: {what}.")
    if op in ("volume_get", "volume_set", "volume_change"):
        vol = result.get("volume")
        muted = L(", звук выключен", ", muted", ", stumm") if result.get("muted") else ""
        return L(f"Громкость {vol}%{muted}.", f"Volume {vol}%{muted}.", f"Lautstärke {vol}%{muted}.")
    if op == "mute":
        return L("Звук выключен.", "Muted.", "Stumm.")
    if op == "unmute":
        return L("Звук включён.", "Sound is back.", "Ton ist wieder an.")
    if op in ("brightness_set", "brightness_change"):
        b = result.get("brightness")
        return L(f"Яркость {b}%.", f"Brightness {b}%.", f"Helligkeit {b}%.")
    if op == "screenshot":
        return L(f"Скриншот сохранила: {result.get('path')}", f"Screenshot saved: {result.get('path')}", f"Screenshot gespeichert: {result.get('path')}")
    if op == "lock":
        return L("Блокирую.", "Locking.", "Sperre.")
    if op == "pc_check":
        return pc_summary(result, lang)
    return L("Готово.", "Done.", "Erledigt.")


def pc_summary(r: dict, lang: str) -> str:
    """Две-три фразы для озвучки; полный отчёт открывается в блокноте."""
    issues = r.get("issues") or []
    cpu, ram = r.get("cpu", 0), r.get("ram_percent", 0)
    hog = r.get("top_cpu")
    if lang == "ru":
        parts = [f"Процессор {cpu:.0f}%, память {ram:.0f}%."]
        parts.append(("Нашла проблемы: " + "; ".join(issues) + ".") if issues else "Серьёзных проблем нет.")
        if hog:
            parts.append(f"Больше всего грузит {hog}.")
        parts.append("Полный отчёт открыла в блокноте.")
    elif lang == "de":
        parts = [f"CPU {cpu:.0f} %, RAM {ram:.0f} %."]
        parts.append(f"{len(issues)} Probleme gefunden, Details im Bericht." if issues else "Keine ernsten Probleme.")
        if hog:
            parts.append(f"Am meisten CPU braucht {hog}.")
    else:
        parts = [f"CPU {cpu:.0f}%, memory {ram:.0f}%."]
        parts.append(f"Found {len(issues)} problem(s) — details in the report." if issues else "No serious problems.")
        if hog:
            parts.append(f"{hog} is using the most CPU.")
    return " ".join(parts)
