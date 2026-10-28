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
    text: str | None = None   # что напечатать / какую кнопку нажать / что сделать с окном


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
def _strip_name(text: str) -> str:
    """Убрать обращение («Привет, Капри, …») из исходной фразы, не трогая регистр остального."""
    return re.sub(r"^\s*(?:(?:привет|эй|окей|ок|hey|hi)\s*,?\s*)?(?:капри|capri|kapri)[\s,!.:-]*", "", text, flags=re.I)


def _clean(text: str) -> str:
    t = text.lower().replace("ё", "е").strip()
    t = re.sub(r"^(привет\s*,?\s*)?(капри|capri|kapri)[\s,!.:-]*", "", t)
    t = re.sub(r"^(?:(?:да|ну|так|ладно|окей|ок|слушай|короче|а)[\s,!.]+)+", "", t)   # «Да, убери первый экран»
    t = re.sub(r"^(пожалуйста|please|bitte)\s+", "", t)
    t = re.sub(r"[\s,!.?]*(пожалуйста|please|bitte)?[\s!.?]*$", "", t)
    return t.strip()


ORDINAL_WORDS = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8,
                 "first": 1, "second": 2, "third": 3, "fourth": 4}


def _ordinal(word: str) -> int | None:
    w = word.lower()
    if re.match(r"последн|last", w):
        return -1
    if m := re.match(r"(\d+)", w):
        return int(m.group(1))
    return next((v for k, v in ORDINAL_WORDS.items() if w.startswith(k)), None)


def _fix_commands(text: str) -> str:
    """Что Whisper слышит вместо частых команд: «гид-статус» → git status."""
    fixes = ((r"\bги[тд][\s-]*статус\b", "git status"), (r"\bги[тд][\s-]*пул+\b", "git pull"),
             (r"\bги[тд][\s-]*пуш\b", "git push"), (r"\bги[тд][\s-]*лог\b", "git log"), (r"\bипконфиг\b", "ipconfig"),
             (r"\bпинг\b", "ping"))
    for rx, rep in fixes:
        text = re.sub(rx, rep, text, flags=re.I)
    return text


RE_MUSIC_WORD = r"(?:музык\w*|music|музон\w*|песн\w*|треки?)"
RE_STOP_WORD = r"(?:выключ\w*|отключ\w*|останов\w*|стоп|убери|пауз\w*|хватит|не надо)"


def _media(t: str) -> Command | None:
    """Команды плееру: пауза, играй, следующий, что играет, перемотка."""
    # «стоп», «стой», «хватит» — остановить то, что играет (голос Капри замолкает сам, когда ты говоришь)
    if re.fullmatch(r"стоп|стой|хватит|стоп(?:-| )стоп|stop|останови(?:сь)?|stopp|halt", t):
        return Command("pause")
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

    words = t.split()
    # Whisper путает слова: «паута», «поуза», «дай мне пауза» — короткая фраза с «пауз…» = пауза
    if len(words) <= 4 and re.search(r"\b(пау[зтс]\w*|поуз\w*|pause)\b", t) and not re.search(r"таймер|минут|секунд|час", t):
        return Command("pause")
    # «следующий траг/тракт/трон/кем» — это «следующий трек», просто плохо расслышано
    if re.fullmatch(r"следующ\w*\s+(?!окно|вкладк|экран|монитор)\S+", t) or (len(words) <= 12 and re.search(r"следующ\w* (трек|песн\w*)", t)):
        return Command("next")
    if re.fullmatch(r"потанцуй|танцуй|станцуй|давай потанцуем|dance", t) or re.fullmatch(r"включи (мой |мои )?плейлист\w*", t):
        return Command("play")
    # «Let me play», «и плей и тук мезик», «у вас произведи YouTube мерзик», «воспроизвести»
    if len(words) <= 6 and re.search(r"\b(плей|плое|плэй|play|проигра\w*|воспроизв\w*|произвед\w*|поспроизв\w*)\b", t) \
            and not re.search(RE_STOP_WORD, t) and not re.search(r"^(что|какая|какой|найди|открой видео)", t):
        return Command("play")
    # «музыка», «музыку давай», «произведи музыку» (Whisper съел «вос-»), «а поставь-ка музыку»:
    # короткая фраза со словом «музыка» и без «выключи/стоп» — значит, включить
    if len(words) <= 4 and re.search(rf"\b{RE_MUSIC_WORD}$|^{RE_MUSIC_WORD}\b", t) and not re.search(RE_STOP_WORD, t) \
            and not re.search(r"^(что|чо|че|какая|какой|открой|найди|поищи|скачай|запиши|громк)", t):
        return Command("play")
    # «…, просто нажми play», «YouTube Music открыт, нажми плей» — кнопка плеера внутри фразы
    if len(words) <= 12:
        if re.search(r"(?:^|\s)(?:нажми|нажать|жми|press|hit|click)\s+(?:на\s+)?(?:play|плей|плэй|воспроизведение)\b", t):
            return Command("play")
        if re.search(r"(?:^|\s)(?:нажми|нажать|жми|press|hit|click)\s+(?:на\s+)?(?:pause|паузу|пауза)\b", t):
            return Command("pause")
    # «нажми play», «нажми на паузу» — это про плеер, а не про клавиши
    if re.fullmatch(r"(?:нажми|нажать|press|hit)\s+(?:на\s+)?(?:play|плей|плэй|воспроизведение)", t):
        return Command("play")
    if re.fullmatch(r"(?:нажми|нажать|press|hit)\s+(?:на\s+)?(?:pause|паузу|пауза)", t):
        return Command("pause")
    return None


RE_MOVE_MON = re.compile(
    r"(?:перетащ\w*|перенес\w*|перемест\w*|передвин\w*|подвин\w*|кинь|закинь|отправь|move|drag|verschieb\w*)\s+(?P<what>.*?)\s*"
    r"(?:на|в|to(?: the)?|auf(?: den)?)\s+(?P<where>\S+)\s+(?:экран\w*|монитор\w*|дисплей\w*|screen|monitor|display|bildschirm)", re.I)
RE_MOVE_MON_NOVERB = re.compile(r"^(?P<what>[\w .-]{2,30}?)\s+на\s+(?P<where>\S+)\s+(?:экран\w*|монитор\w*)$")
MON_WORDS = (("главн", "1"), ("основн", "1"), ("перв", "1"), ("втор", "2"), ("трет", "3"), ("1", "1"), ("2", "2"), ("3", "3"), ("друг", "next"), ("соседн", "next"),
             ("следующ", "next"), ("лев", "left"), ("прав", "right"), ("first", "1"), ("second", "2"), ("third", "3"),
             ("other", "next"), ("next", "next"), ("left", "left"), ("right", "right"), ("erst", "1"), ("zweit", "2"),
             ("ander", "next"), ("link", "left"), ("recht", "right"))


def match(text: str) -> Command | None:
    """Прямая системная команда или None (тогда фразу разбирают другие правила или модель)."""
    t = _clean(text)
    typing = re.match(r"^(?:напечатай|впечатай|набери|введи|type|tippe|schreib\w* text)\s+", t)
    if not t or (len(t) > 80 and not typing):
        return None

    # --- музыка и видео; «… в ютуб мюзик», «… на ютубе», «… в спотифай» — какой плеер
    tm = re.search(r"\s+(?:в|во|на|in|on|auf)?\s*(ютуб[\s-]*м[юь]?\w*зик\w*|youtube[\s-]*(?:m\w*sic|м\w*зик\w*)|ютьюб[\s-]*мюзик\w*|спотифа\w*|spotify|"
                   r"ютуб\w*|youtube|хром\w*|chrome|браузер\w*|browser)\s*$", t)
    if tm:
        word = tm.group(1)
        target = ("music" if re.search(r"м[юь]?\w*зик|m\w*sic", word) else "spotify" if re.search(r"спотиф|spotify", word) else "browser")
        if (c := _media(t[: tm.start()].strip())) is not None:
            c.text = target
            return c
    if (c := _media(t)) is not None:
        if c.op in ("play", "pause", "next", "previous") and c.text is None:
            # плеер назван в середине фразы: «YouTube Music открыт, нажми play»
            if re.search(r"(?:ютуб|youtube|ютьюб)[\s-]*(?:m\w*sic|м\w{0,3}з\w*к\w*)", t):
                c.text = "music"
            elif re.search(r"спотиф|spotify", t):
                c.text = "spotify"
        return c

    # --- печать текста (текст берём из исходной фразы — с заглавными буквами и знаками)
    if re.match(r"^(?:напечатай|впечатай|набери|введи текст|введи|type|tippe|schreib\w* text)\s+", t):
        raw = _strip_name(text)
        m = re.match(r"^\s*(?:напечатай|впечатай|набери|введи текст|введи|type|tippe|schreib\w* text)\s*[:,]?\s*(.+?)\s*$", raw, re.I | re.S)
        if m:
            body = m.group(1).strip().strip("«»\"“”")
            body = re.sub(r"\s*(?:и нажми энтер|и отправь|and press enter)$", "\n", body, flags=re.I)
            return Command("type", text=body)

    # --- прокрутка
    if m := re.fullmatch(r"(?:прокрути|пролистай|листай|мотай|scroll|scrolle?)\s*(?:страницу\s+)?(вниз|вверх|наверх|down|up|runter|hoch)"
                         r"(?:\s+(?:на\s+)?(\S+)\s*(?:раз\w*|times|mal)?)?(?:\s+(немного|чуть-?чуть|сильно|много))?", t):
        up = m.group(1) in ("вверх", "наверх", "up", "hoch")
        how = " ".join(x for x in (m.group(2), m.group(3)) if x)
        n = numbers(how)[0] if numbers(how) else 2 if re.search(r"немного|чуть", how) else 10 if re.search(r"сильно|много", how) else 5
        return Command("scroll", value=n if up else -n)

    if m := re.search(r"(?:прокрути|пролистай|листай|крути|мотни)\s+(?:страницу\s+)?(чуть(?:-чуть)?\s+|немного\s+|сильно\s+)?(ниже|вниз|выше|вверх|наверх)", t):
        n = 2 if m.group(1) and re.match(r"чуть|немного", m.group(1)) else 10 if m.group(1) and m.group(1).startswith("сильно") else 5
        return Command("scroll", value=n if m.group(2) in ("выше", "вверх", "наверх") else -n)

    # --- клики мышью там, где сейчас курсор
    if re.fullmatch(r"(?:сделай\s+)?(?:двойной клик|кликни дважды|дабл ?клик|double ?click|doppelklick)", t):
        return Command("click", text="double")
    if re.fullmatch(r"(?:сделай\s+)?(?:правый клик|кликни правой(?: кнопкой)?(?: мыши)?|нажми правой кнопкой(?: мыши)?|right ?click|rechtsklick)", t):
        return Command("click", text="right")
    if re.fullmatch(r"(?:кликни|щелкни|клик|нажми левой кнопкой(?: мыши)?|кликни мышкой|click|klick)", t):
        return Command("click", text="left")
    if re.fullmatch(r"(?:мышь|мышку|курсор)\s+(?:в|на)\s+(?:центр|середину)(?:\s+окна|\s+экрана)?", t):
        return Command("mouse_center")

    # --- «замолчи», «shut up» — перестать говорить
    if re.fullmatch(r"замолчи|замолкни|заткнись|помолчи|тихо|молчи|хватит говорить|не говори|shut up|be quiet|"
                    r"stop talking|sei still|halt die klappe", t):
        return Command("quiet")
    # --- вкладки браузера
    if m := re.fullmatch(r"(?:открой|перейди|переключись|покажи|зайди)(?:\s+(?:на|в|во))?\s+(\S+)\s+(?:вкладк\w*|таб\w*|тап\w*|tab)", t):
        n = _ordinal(m.group(1))
        if n:
            return Command("keys", keys=["ctrl", "9" if n == -1 or n > 8 else str(n)])
    for rx, keys in ((r"(?:следующ\w*|другая|другую) вкладк\w*|вкладк\w* (?:вперед|дальше)|next tab", ["ctrl", "tab"]),
                     (r"предыдущ\w* вкладк\w*|вкладк\w* назад|previous tab", ["ctrl", "shift", "tab"]),
                     (r"закрой (?:эту |текущую )?вкладк\w*|close (?:the |this )?tab", ["ctrl", "w"]),
                     (r"(?:открой |создай )?нов\w* вкладк\w*|new tab", ["ctrl", "t"]),
                     (r"(?:верни|восстанови|открой) (?:закрыт\w* )?вкладк\w* (?:обратно|назад)|верни закрыт\w* вкладк\w*|reopen (?:the )?tab", ["ctrl", "shift", "t"])):
        if re.fullmatch(rx, t):
            return Command("keys", keys=keys)
    # --- полный экран: «YouTube на весь экран», «сделай во весь экран»
    if re.fullmatch(r"(?:(?:сделай|разверни|открой|включи)\s+)?(?:ютуб\w*\s+|youtube\s+|видео\s+)?(?:на|во)\s+весь\s+экран|"
                    r"(?:сделай\s+|включи\s+)?(?:полный|полноэкранный)\s+(?:экран|режим)|(?:выйди из|выключи|убери) полн\w* экран\w*|fullscreen|full screen", t):
        return Command("fullscreen")
    # --- какие мониторы показывать (как Win+P)
    S1 = r"(?:первый|основной|главный)"
    S2 = r"(?:второй|вспомогательный|дополнительный|внешний)"
    SCR = r"(?:экран|монитор|дисплей)"
    for rx, mode in ((rf"(?:оставь|включи|покажи) только {S2} {SCR}|(?:убери|выключи|отключи|погаси) {S1} {SCR}|только {S2} {SCR}", "external"),
                     (rf"(?:оставь|включи|покажи) только {S1} {SCR}|(?:убери|выключи|отключи|погаси) {S2} {SCR}|только {S1} {SCR}", "internal"),
                     (rf"(?:верни|включи|покажи) (?:оба|два|все) (?:экрана|монитора|экраны|мониторы)|(?:включи|верни) {S2} {SCR}|"
                      rf"(?:включи|верни) {S1} {SCR}|расширь (?:экран\w*|рабочий стол)", "extend"),
                     (r"(?:дублируй|повтори|клонируй) (?:экран\w*|монитор\w*)", "clone")):
        if re.fullmatch(rx, t):
            return Command("display", text=mode)
    # --- настройки Windows: ночной свет и т.п. (переключатель сам не нажимаем — открываем нужную страницу)
    if re.fullmatch(r"(?:включи |выключи |отключи |открой )?(?:ночн\w* (?:режим|свет)|ночник|night ?light)", t):
        return Command("nightlight", 0 if re.match(r"выключ|отключ", t) else 1)
    for rx, page in (                     (r"(?:открой )?(?:настройки )?(?:блютуз\w*|bluetooth)", "bluetooth"),
                     (r"(?:открой )?(?:настройки )?(?:вай ?фай|wi-?fi|вайфай)", "network-wifi"),
                     (r"(?:открой )?настройки (?:звука|звук)", "sound"),
                     (r"(?:открой )?настройки (?:экрана|дисплея|монитора)", "display"),
                     (r"(?:проверь |открой )?обновлени\w* (?:windows|виндоус\w*)|(?:открой )?центр обновлени\w*", "windowsupdate")):
        if re.fullmatch(rx, t):
            return Command("settings", text=page)
    # --- печать в нужную программу: «напиши в PowerShell git status», «открой блокнот и напечатай привет»
    raw = _strip_name(text).strip().rstrip(".!")
    if m := re.match(r"^(?:напиши|введи|напечатай|набери|впиши)\s+в\s+(?P<app>павершел\w*|пауэршел\w*|повершел\w*|повыш\w*|powershell|"
                     r"терминал\w*|консол\w*|командн\w* строк\w*|cmd|блокнот\w*|notepad)\s+(?P<text>.+)$", raw, re.I | re.S) or \
            re.match(r"^(?:открой|запусти)\s+(?P<app>павершел\w*|пауэршел\w*|повершел\w*|повыш\w*|powershell|терминал\w*|консол\w*|"
                     r"командн\w* строк\w*|cmd|блокнот\w*|notepad)\s+и\s+(?:введи|напиши|напечатай|набери)\s+(?P<text>.+)$", raw, re.I | re.S):
        app = m.group("app").lower()
        app = "notepad" if re.match(r"блокнот|notepad", app) else "cmd" if re.match(r"cmd|командн", app) else "powershell"
        body = _fix_commands(m.group("text").strip().strip("«»\"“”"))
        return Command("type_in", text=body + ("" if app == "notepad" else "\n"), keys=[app])
    # --- окна по имени программы: «разверни хром», «сверни телеграм», «закрой YouTube»
    if m := re.fullmatch(r"(сверни|разверни|закрой|верни|покажи)\s+(?!окно|все|вкладк|программу|эту|это|меню)(.+)", t):
        action = {"сверни": "minimize", "разверни": "maximize", "закрой": "close", "верни": "restore", "покажи": "restore"}[m.group(1)]
        name = re.sub(r"(?:ютуб|youtube|ютьюб)[\s-]*(?:m\w*sic|м\w{0,3}з\w*к\w*)", "youtube music", m.group(2))
        name = re.sub(r"^ютуб\w*$", "youtube", name)
        return Command("window", text=action, keys=[name])

    # --- окна
    for rx, action in ((r"сверни (?:это |текущее )?окно|minimi[sz]e(?: the)? window|fenster minimieren", "minimize"),
                       (r"разверни (?:это |текущее )?окно(?: на весь экран)?|maximi[sz]e(?: the)? window|fenster maximieren", "maximize"),
                       (r"закрой (?:это |текущее )?окно|закрой (?:эту )?программу|close(?: the| this)? window|fenster schließen", "close"),
                       (r"(?:передвинь|подвинь|прилепи|окно) (?:окно )?(?:влево|налево)|window left", "left"),
                       (r"(?:передвинь|подвинь|прилепи|окно) (?:окно )?(?:вправо|направо)|window right", "right")):
        if re.fullmatch(rx, t):
            return Command("window", text=action)
    if re.fullmatch(r"сверни все(?: окна)?|покажи рабочий стол|show desktop|alles minimieren", t):
        return Command("keys", keys=["win", "d"])
    # «перетащи хром на второй экран», «перенеси окно на другой монитор», «move chrome to the second screen»
    if m := RE_MOVE_MON.search(t) or RE_MOVE_MON_NOVERB.match(t):
        what = (m.group("what") or "").strip()
        what = None if what in ("", "окно", "это окно", "его", "её", "ее", "это", "window", "the window", "this window") else what
        where = m.group("where").lower()
        n = next((v for k, v in MON_WORDS if where.startswith(k)), None)
        if n:
            return Command("move_monitor", text=what, keys=[n])
    if re.fullmatch(r"(?:переключи|смени|следующее) окно|переключись на (?:другое|следующее) окно|switch window", t):
        return Command("keys", keys=["alt", "tab"])

    if m := re.match(r"^(?:нажми|нажать|зажми|press|hit|drück|drücke)\s+(.+)$", t):
        parsed = parse_keys(m.group(1))
        if parsed:
            return Command("keys", keys=parsed[0], repeat=parsed[1])
    # --- клик по кнопке по названию: «нажми кнопку Сохранить», «кликни по Настройки», «открой меню Файл»
    if m := re.match(r"^(?:нажми|нажать|кликни|щелкни|ткни|click|klick\w*|drück\w*)\s+(?:на\s+|по\s+|on\s+|auf\s+)?"
                     r"(?:кнопк\w+|пункт\w*|ссылк\w+|вкладк\w+|button|link|tab|knopf)?\s*(.+)$|^открой (?:меню|вкладку)\s+(.+)$", t):
        name = (m.group(1) or m.group(2)).strip().strip("«»\"“”")
        if name:
            return Command("click_name", text=name)

    # --- громкость
    if re.match(r"^(какая|какой|сколько) (сейчас |у меня )?([гк]ромкость|звук)|^what('s| is) the volume|^wie laut", t):
        return Command("volume_get")
    if re.fullmatch(r"(выключи|отключи|убери) звук|без звука|(сделай )?тишин\w*|mute|stumm( schalten)?|ton aus", t):
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
    if op == "type":
        return L("Напечатала.", "Typed it.", "Getippt.")
    if op == "scroll":
        return L("Листаю.", "Scrolling.", "Scrolle.")
    if op == "click":
        return L({"double": "Двойной клик.", "right": "Правый клик."}.get(cmd.text, "Клик."), "Clicked.", "Geklickt.")
    if op == "mouse_center":
        return L("Мышь в центре окна.", "Mouse centred.", "Maus in der Mitte.")
    if op == "window":
        w = result.get("title") or L("окно", "window", "Fenster")
        names = {"minimize": ("Свернула", "Minimised", "Minimiert"), "maximize": ("Развернула", "Maximised", "Maximiert"),
                 "close": ("Закрываю", "Closing", "Schließe"), "left": ("Подвинула влево", "Moved left", "Nach links"),
                 "right": ("Подвинула вправо", "Moved right", "Nach rechts")}[cmd.text]
        return L(f"{names[0]}: {w}.", f"{names[1]}: {w}.", f"{names[2]}: {w}.")
    if op == "fullscreen":
        return L("Во весь экран.", "Fullscreen.", "Vollbild.")
    if op == "display":
        if result.get("error"):
            return L(f"Не вышло: {result['error']}.", f"Couldn't: {result['error']}.", f"Ging nicht: {result['error']}.")
        return {"external": L("Оставила только второй экран. Вернуть — «верни оба экрана».", "Second screen only.", "Nur zweiter Bildschirm."),
                "internal": L("Оставила только основной экран. Вернуть — «верни оба экрана».", "Main screen only.", "Nur Hauptbildschirm."),
                "extend": L("Оба экрана снова работают.", "Both screens on.", "Beide Bildschirme an."),
                "clone": L("Экраны показывают одно и то же.", "Screens duplicated.", "Bildschirme dupliziert.")}[cmd.text or "extend"]
    if op == "nightlight":
        if result.get("opened"):
            return L("Windows не дал нажать сам — открыла «Ночной свет», щёлкни переключатель.", "Night light settings open — flip the switch.",
                     "Nachtmodus geöffnet — bitte umschalten.")
        return L("Ночной свет включён." if cmd.value else "Ночной свет выключен.", "Night light on." if cmd.value else "Night light off.",
                 "Nachtmodus an." if cmd.value else "Nachtmodus aus.")
    if op == "settings":
        return {"nightlight": L("Открыла ночной свет — щёлкни переключатель «Включить сейчас».", "Night light settings open.",
                                "Nachtmodus-Einstellungen geöffnet."),
                }.get(cmd.text or "", L("Открыла настройки.", "Settings open.", "Einstellungen geöffnet."))
    if op == "type_in":
        return L(f"Напечатала в {result.get('title') or 'окно'}.", "Typed it.", "Getippt.")
    if op == "move_monitor":
        w = result.get("title") or L("окно", "the window", "das Fenster")
        if result.get("same"):
            return L(f"{w} и так на мониторе {result.get('monitor')}.", f"{w} is already on screen {result.get('monitor')}.",
                     f"{w} ist schon auf Bildschirm {result.get('monitor')}.")
        side = (cmd.keys or [""])[0]
        where = {"left": L("левый экран", "the left screen", "den linken Bildschirm"),
                 "right": L("правый экран", "the right screen", "den rechten Bildschirm")}.get(
            side, L(f"монитор {result.get('monitor')}", f"screen {result.get('monitor')}", f"Bildschirm {result.get('monitor')}"))
        return L(f"Перетащила {w} на {where}.", f"Moved {w} to {where}.", f"{w} auf {where} verschoben.")
    if op == "click_name":
        if result.get("confirm"):
            return L(f"Нашла «{result['found']}». Нажать?", f"Found “{result['found']}”. Click it?", f"„{result['found']}“ gefunden. Klicken?")
        return L(f"Нажала «{result.get('found', cmd.text)}».", f"Clicked “{result.get('found', cmd.text)}”.", f"„{result.get('found', cmd.text)}“ geklickt.")
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
