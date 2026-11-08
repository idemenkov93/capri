"""Фразы про субтитры, сигналы и уроки → действие «live» (выполняет LiveController в главном потоке)."""
from __future__ import annotations

import re

from .. import actions as A

LANG_RX = (("de", r"немецк|deutsch|german"), ("en", r"английск|english|англ"), ("ru", r"русск"), ("he", r"иврит"),
           ("auto", r"любо\w+\s+язык|авто"))

RE_START = re.compile(
    r"^(?:включи|запусти|начни|дай|покажи)?\s*(?:живые\s+)?(?:субтитры|титры|перевод\s+урока|перевод\s+разговора|"
    r"синхронный\s+перевод)(?P<rest>.*)$|"
    r"^(?:переводи|переводить|переведи)\s+(?:мне\s+)?(?:урок|разговор|собеседование|что\s+говор\w+|тимс|teams|звонок|"
    r"видео|созвон|учителя)(?P<rest2>.*)$", re.I)
RE_STOP = re.compile(r"^(?:выключи|останови|стоп|хватит|закрой|убери)\s+(?:субтитры|титры|перевод(?:\s+урока)?)$|"
                     r"^хватит\s+переводить$|^субтитры\s+(?:выкл|стоп)$", re.I)
RE_NOTES = re.compile(r"^(?:сделай|составь|напиши|создай)\s+конспект(?:\s+урока)?$|^конспект\s+урока$", re.I)
RE_ADD = re.compile(r"^(?:добавь\s+слово|сигнал\s+на\s+слово|слушай\s+слово|сигналь\s+на|реагируй\s+на\s+слово)\s+"
                    r"[«\"']?(?P<w>[^«»\"']{2,40})[»\"']?$", re.I)
RE_DEL = re.compile(r"^(?:убери|удали)\s+слово\s+[«\"']?(?P<w>[^«»\"']{2,40})[»\"']?$|"
                    r"^не\s+(?:сигналь|реагируй)\s+на\s+[«\"']?(?P<w2>[^«»\"']{2,40})[»\"']?$", re.I)
RE_LIST = re.compile(r"^(?:какие|на\s+какие)\s+слова\s+(?:ты\s+)?(?:слушаешь|сигналишь|ловишь)", re.I)
RE_SIGNALS = re.compile(r"^(?P<on>включи|выключи)\s+сигналы$", re.I)
RE_MIC = re.compile(r"^(?:переводи\s+и\s+меня|переводи\s+мой\s+микрофон)$|^(?P<off>не\s+переводи\s+(?:меня|мой\s+микрофон))$", re.I)
RE_LAST = re.compile(r"^(?:повтори|напомни|покажи)\s+(?:мне\s+)?прошлый\s+урок$|^что\s+было\s+на\s+прошлом\s+уроке$|"
                     r"^(?:о\s+ч[её]м|про\s+что)\s+был\s+(?:прошлый|последний)\s+урок$", re.I)
RE_SEARCH = re.compile(r"^(?:что\s+мы\s+проходили\s+(?:по|про)|когда\s+мы\s+проходили|найди\s+в\s+уроках|"
                       r"что\s+говорили\s+на\s+уроке\s+про)\s+(?P<t>.{2,60})$", re.I)


def lang_of(text: str) -> str | None:
    for code, rx in LANG_RX:
        if re.search(rx, text, re.I):
            return code
    return None


def route(text: str):
    from ..agent import Reply
    t = text.strip(" .!?")

    def act(say, **p):
        return Reply(say, [A.Action("live", "Субтитры", p, needs_confirm=False)], "rules")
    if RE_STOP.match(t):
        return act("Выключаю субтитры.", do="stop")
    if m := RE_START.match(t):
        rest = (m.group("rest") or m.group("rest2") or "").strip()
        if rest and not re.match(r"^(?:с|на|для|урока|разговора|пожалуйста|по-|в\s)", rest, re.I) and not lang_of(rest):
            return None                         # «субтитры к фильму скачай…» — не наше
        return act("Включаю субтитры.", do="start", lang=lang_of(rest))
    if RE_NOTES.match(t):
        return act("Делаю конспект.", do="notes")
    if m := RE_ADD.match(t):
        return act("Запомнила слово.", do="add_word", word=m.group("w").strip())
    if m := RE_DEL.match(t):
        return act("Убираю слово.", do="del_word", word=(m.group("w") or m.group("w2")).strip())
    if RE_LIST.match(t):
        return act("Смотрю.", do="list_words")
    if m := RE_SIGNALS.match(t):
        return act("Готово.", do="signals", on=m.group("on").lower().startswith("вк"))
    if m := RE_MIC.match(t):
        return act("Готово.", do="mic", on=not m.group("off"))
    if RE_LAST.match(t):
        return act("Открываю прошлый урок.", do="last")
    if m := RE_SEARCH.match(t):
        return act("Ищу в уроках.", do="search", term=m.group("t").strip(" ?.!"))
    return None
