"""Мозг Капри: быстрые команды → локальная модель → Claude для сложного."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import actions as A
from . import commands as C
from .llm import claude_code
from .llm.ollama_client import OllamaClient, OllamaError
from .persona import system_prompt

HISTORY_LIMIT = 20
RE_WANTS_CODE = re.compile(r"скрипт|код\b|кода\b|программ|батник|\.bat\b|функци|script|code", re.I)
RE_CODE_BLOCK = re.compile(r"```([\w+-]*)[ \t]*\n(.*?)```", re.S)
CODE_EXT = {"powershell": ".ps1", "ps1": ".ps1", "pwsh": ".ps1", "ps": ".ps1", "python": ".py", "py": ".py",
            "bash": ".sh", "sh": ".sh", "shell": ".sh", "bat": ".bat", "batch": ".bat", "cmd": ".bat",
            "sql": ".sql", "yaml": ".yaml", "yml": ".yaml", "json": ".json", "javascript": ".js", "js": ".js"}


@dataclass
class Reply:
    text: str
    actions: list[A.Action] = field(default_factory=list)
    route: str = "local"   # rules | local | claude | error
    ask: bool = False      # Капри задала вопрос — после ответа слушать без «Капри»


# --- быстрые правила: отрабатывают мгновенно, без ИИ -------------------------
# Русский / Deutsch / English. Всё, что не подошло, уходит модели — она понимает любой язык.
RE_OPEN = re.compile(r"^(?:(?:от)?крой(?:те)?|откройка|открою|запусти(?:те)?|включи(?:те)?|öffne|starte|open|launch|start|play)\s+(.+?)[.!]?$", re.I)
# «таймер», «поставь таймер» без времени — спросить, на сколько (а не придумывать 25 минут)
# «что ты умеешь», «напиши список своих команд»
RE_HELP = re.compile(r"что ты (?:умеешь|можешь)|какие (?:у тебя )?(?:есть )?команды|(?:список|перечень|все)\s+(?:\w+\s+){0,2}команд|"
                     r"(?:твоих|своих|твои|свои)\s+команд|what can you do|your commands|was kannst du", re.I)
# «найди ближайший ресторан», «где рядом аптека»
RE_NEARBY = re.compile(r"^(?:найди|покажи|подскажи|где)\s+.*\b(?:ближайш\w*|рядом|поблизости|недалеко|возле меня|около меня)\b.*$", re.I)
# «напоминай мне отдыхать каждые 2 часа», «не напоминай про отдых»
RE_BREAKS = re.compile(r"(?:напомина\w*|напомни)\b.{0,30}\b(?:отдох\w*|отдыха\w*|перерыв\w*|размя\w*|разминк\w*)|"
                       r"(?:не|хватит|выключи|отключи|убери)\s+напомина\w*.{0,20}(?:отдых|перерыв)", re.I)
RE_TIMER_BARE = re.compile(r"^(?:поставь\s+|заведи\s+|включи\s+|сделай\s+)?(?:таймер|будильник|напоминалк\w*|timer)[.!?]?$", re.I)
# слова о времени: без них «напомни мне команду…» — это вопрос, а не таймер
RE_TIME_WORDS = re.compile(r"\d|минут|секунд|час|таймер|будильник|через|завтра|утром|вечером|timer|minute|hour|second", re.I)
RE_SEARCH = re.compile(
    r"^(?:загугли|погугли|найди в (?:интернете|гугле|сети)|google|googel|suche nach|search for)\s+(.+)$", re.I
)
RE_TIMER = re.compile(
    r"(?:таймер\s+на|timer\s+(?:auf|für|for))\s+(\d+(?:[.,]\d+)?)\s*"
    r"(сек|с\b|мин|м\b|час|ч\b|sek|sec|s\b|min|m\b|stunde|std|h\b|hour)\w*(?:\s+(.+))?",
    re.I,
)
RE_NOTE = re.compile(r"^(?:запиши|заметка|запомни|notiz|merke dir|note|remember)[:,]?\s+(.+)$", re.I)
RE_CLAUDE = re.compile(r"^(?:/claude|клод|claude)[,:]?\s+(.+)$", re.I | re.S)
RE_YOUTUBE = re.compile(
    r"^(?:найди|поищи|включи|поставь|открой|запусти)\s+(?:на|в)\s+(?:ютубе|ютьюбе|youtube)\s+(.+)$|"
    r"^(?:ютуб|youtube)\s+(.+)$|^(?:search|play)\s+(.+?)\s+on youtube$|^(?:such\w*|spiel\w*)\s+(.+?)\s+auf youtube$", re.I)
# «Привет, Капри, открой ютуб.» → «открой ютуб» (голос приносит имя и точки)
RE_WAKE_PREFIX = re.compile(r"^\s*(?:(?:привет|эй|окей|ок|хей|hey|hi|hallo|okay)\s*,?\s*)?(?:капри|кэпри|capri|kapri)\b[\s,!.:-]*", re.I)
# сложное — сразу Claude: код, разбор ошибок, планы, подробные объяснения
RE_COMPLEX = re.compile(
    r"\b(напиши|написать|сделай|составь|сгенерируй|создай)\b.{0,40}?(\w*скрипт\w*|\w*script\w*|код\w*|программ\w*|игр[уыа]\w*|"
    r"бот\w*|сайт\w*|функци\w*|на (python|питоне|пайтоне|powershell|павершеле|bash|sql|js|javascript|c#|java))\b|"
    r"\b(напиши|написать|сделай|составь|сгенерируй)\s+(мне\s+)?(скрипт|код|программ\w*|функци\w*|регулярк\w*|запрос|план|резюме|письмо|"
    r"bewerbung\w*|anschreiben)|\b(объясни|расскажи)\s+(подробно|детально|по шагам)|\bразбер\w*|\bсравни\b|"
    r"\bпочему\s+(не\s+работает|ошибк\w*|падает|не\s+запускается)|\bкак\s+(настроить|исправить|починить|автоматизировать)|"
    r"\bпомоги\s+(с|мне с)\s+(код\w*|ошибк\w*|скрипт\w*|задач\w*)|\b(powershell|bash|python|sql|ansible|terraform|docker)\b.*\b(скрипт|команд\w*|ошибк\w*)|"
    r"\b(write|create)\s+(a\s+)?(script|function|program)|\bschreib\w*\s+(ein\s+)?(skript|programm)", re.I)
# «открой вторую ссылку», «включи третье видео», «открой первый результат в хроме»
RE_NTH = re.compile(r"^(?:открой|включи|запусти|кликни|нажми)\s+(?:на\s+)?(?P<n>\S+?)\s+(?:ссылк\w*|видео|ролик\w*|результат\w*|link|video|result)"
                    r"(?:\s+(?:в|на)\s+\S+)?$|^(?:open|play)\s+the\s+(?P<n2>\S+)\s+(?:link|video|result)$", re.I)
RE_VIDEO_WORDS = re.compile(r"\b(выпуск\w*|сери[яюи]|сезон\w*|видео|ролик\w*|клип\w*|трейлер\w*|обзор\w*|подкаст\w*|стрим\w*)\b|#\d+", re.I)
RE_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")
# ответы на её вопросы
RE_YES = re.compile(r"^(?:да|ага|угу|давай|конечно|ок|окей|ok|okay|включай|включи|ставь|поставь|го|yes|yeah|yep|ja|klar)\b", re.I)
RE_NO = re.compile(r"^(?:нет|неа|не надо|не нужно|не включай|не ставь|отмена|отмени|забей|no|nope|nein)\b", re.I)
PENDING_TTL = 90          # секунд: столько ждём ответ на свой вопрос
# куда искать, если сказал прямо: «… в ютуб мюзик» — YouTube Music, «… на ютубе» — обычный YouTube
RE_IN_YTM = re.compile(r"\s+(?:в|на|из)\s+(?:ютуб\w*|youtube|ютьюб\w*)[\s-]*(?:music|мюзик\w*|мьюзик\w*|музык\w*)$", re.I)
RE_IN_YT = re.compile(r"\s+(?:в|на)\s+(?:ютуб\w*|youtube|ютьюб\w*)$|^(?:\S+\s+)?(?:на|в)\s+(?:ютуб\w*|youtube)\s", re.I)
# «открой YouTube Music на главном экране», «запусти дискорд на втором мониторе» — открыть и сразу поставить
RE_OPEN_ON_SCREEN = re.compile(r"^(?:открой|откройте|запусти|включи)\s+(?P<app>.+?)\s+на\s+(?:(?P<one>главн\w*|основн\w*|перв\w*|1)|"
                               r"(?P<two>втор\w*|вспомогательн\w*|дополнительн\w*|друг\w*|2))\s+(?:экран\w*|монитор\w*|дисплe?\w*)$", re.I)
# «включи первый трек», «воспроизведи 3 песню», «трек номер 2», просто «трек» (Whisper съел начало) — в YouTube Music
RE_PLAY_NTH = re.compile(r"^(?:(?:включи|воспроизведи|запусти|поставь|играй|play)\s+)?(?:(?P<n>\S+)\s+(?:трек\w*|песн\w*|композици\w*)|"
                         r"(?:трек\w*|песн\w*|композици\w*)(?:\s+(?:номер\s+|№\s*)?(?P<n2>\S+))?)$", re.I)
# «воспроизведи Риплей Микс», «поставь плейлист For coding» — открыть на странице и сразу включить
RE_PLAY_NAMED = re.compile(r"^(?:(?:воспроизведи|воспроизвести|play)\s+(?:плейлист\s+|микс\s+|альбом\s+)?|"
                           r"(?:поставь|запусти|включи)\s+(?:плейлист|микс|альбом)\s+)(?P<name>.+)$", re.I)
RE_GENERIC_PLAY = re.compile(r"(?:мою\s+|мои\s+|какую-?нибудь\s+|немного\s+|любую\s+)?(?:музык\w*|песн\w*|трек\w*|плейлист\w*|"
                             r"(?:youtube|ютуб\w*)(?:[\s-]*(?:music|мюзик|мьюзик|музык\w*))?|play|плей|music|видео)", re.I)
# игры
RE_GAME_STATS = re.compile(r"\bкак я (?:сыграл\w*|играю|отыграл\w*|играл)\b|\b(?:моя|мою) (?:игров\w* )?статистик\w*|"
                           r"\bстатистик\w* (?:матча|игры|катки|за матч|в кс|в cs)|\bкакой у меня (?:кд|к ?д|счёт|счет)\b|\bhow did i play\b", re.I)
RE_GAME_COACH = re.compile(r"\b(?:разбери|оцени|проанализируй|проанализировать|разобрать)\s+(?:мою\s+|мой\s+|эту\s+|этот\s+)?"
                           r"(?:игру|катку|матч|каточку)|\bчто мне (?:улучшить|подтянуть|исправить) в (?:игре|кс|cs|катке)", re.I)
RE_GPU_TEMP = re.compile(r"температур\w* (?:видеокарт\w*|видюх\w*|gpu)|(?:видеокарт\w*|видюх\w*) (?:греется|горячая|сколько градусов)|"
                         r"как там видеокарт\w*|сколько градусов (?:на|у) (?:видеокарт\w*|видюх\w*)", re.I)
# питание: перезагрузить / выключить / усыпить — только по-настоящему и только после «Да»
_PC = r"(?:мой\s+|этот\s+)?(?:компьютер\w*|комп|пк|систем[уа]|windows|винд[уа])"
RE_POWER = (
    (re.compile(rf"(?:перезагрузи|перезагрузить|перезапусти|ребутни|рестартни)\s+{_PC}|restart (?:the |my )?(?:pc|computer)", re.I), "restart",
     "Перезагрузить компьютер", "Перезагружаю через 10 секунд. Передумал — скажи «отмени перезагрузку»."),
    (re.compile(rf"(?:выключи|выключить|выруби|отключи)\s+{_PC}|заверши работу(?: {_PC})?|shut ?down (?:the |my )?(?:pc|computer)", re.I), "shutdown",
     "Выключить компьютер", "Выключаю через 10 секунд. Передумал — скажи «отмени выключение»."),
    (re.compile(rf"(?:усыпи|усыпить)\s+{_PC}|(?:отправь|переведи)\s+{_PC}\s+в\s+сон|(?:включи\s+)?спящий режим|sleep mode", re.I), "sleep",
     "Перевести компьютер в сон", "Сплю. Пошевели мышкой — проснусь."),
    (re.compile(r"отмени\s+(?:перезагрузку|выключение)|не\s+(?:перезагружай|выключай)\s+(?:компьютер|комп|пк)", re.I), "abort",
     "", "Отменила."),
)

UNIT_TO_MIN = {
    "сек": 1 / 60, "с": 1 / 60, "sek": 1 / 60, "sec": 1 / 60, "s": 1 / 60,
    "мин": 1, "м": 1, "min": 1, "m": 1,
    "час": 60, "ч": 60, "stunde": 60, "std": 60, "h": 60, "hour": 60,
}


def _human(minutes: int) -> str:
    if minutes % 60 == 0:
        h = minutes // 60
        return "час" if h == 1 else f"{h} часа" if h in (2, 3, 4) else f"{h} часов"
    return f"{minutes} минут"


class Agent:
    def __init__(self, config: dict, llm: OllamaClient | None = None,
                 claude: Callable[[str, dict], str] = claude_code.ask, system=None, apps=None):
        self.cfg = config
        if apps is None:
            from .applist import AppIndex
            from .config import expand
            apps = AppIndex(expand("data/apps.json"))
        self.apps = apps
        self.google = None   # создаётся при первой просьбе про почту/календарь/задачи
        if system is None:
            from .config import expand
            from .system import SystemControl
            system = SystemControl(expand("data"))
        self.system = system
        if hasattr(system, "apps"):
            system.apps = apps            # чтобы узнавать YouTube Music по его ID
        o = config["ollama"]
        self.llm = llm or OllamaClient(o["url"], o["model"], o["timeout"], o.get("think"), o.get("options"))
        self.claude = claude
        self.history: list[dict] = []
        self.pending: dict | None = None   # Капри спросила и ждёт ответ: {"kind": "timer" | "confirm", "at": …}

    # публичный вход -------------------------------------------------------
    def handle(self, text: str, on_status: Callable[[str], None] = lambda s: None,
               on_token: Callable[[str, str], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> Reply:
        text = RE_WAKE_PREFIX.sub("", text.strip()).strip()
        pending, self.pending = self.pending, None
        if pending and text and time.monotonic() - pending["at"] < PENDING_TTL:
            if (ans := self._answer(pending, text)) is not None:
                return ans
        if not text:
            import random
            slang = self.cfg.get("persona", {}).get("slang", True)
            lines = ("Ну? Я слушаю.", "На связи, чумба.", "Говори, чум.", "Слушаю. Что за гиг?", "Я тут. Выкладывай.") if slang \
                else ("Ну? Я слушаю.", "Слушаю.", "Я тут. Говори.")
            return Reply(random.choice(lines), route="rules")
        reply = self._rules(text)
        if reply and reply.route == "vision":
            return self._vision_click(reply.text, on_status)
        if reply:
            return reply
        if m := RE_CLAUDE.match(text):
            return self._ask_claude(m.group(1), on_status)
        from . import screen, weather
        if weather.is_weather_question(text) and len(text) < 120:
            return self._weather(text)
        if screen.is_screen_question(text):
            return self._screen(text, on_status)
        if RE_COMPLEX.search(text) or len(text) > 300 or text.count("\n") >= 3:
            return self._ask_claude(text, on_status)
        return self._local(text, on_status, on_token, should_stop)

    def _google(self, text: str) -> Reply | None:
        """Почта / календарь / задачи. None — фраза не про Google.

        Полный режим (Google Cloud, docs/GOOGLE.md) — если есть data/google_token.json.
        Иначе простой режим (google_simple.py): календарь по iCal-ссылке, почта по паролю приложения, свой список дел."""
        from . import google_api as G
        from . import google_simple as S
        from .config import expand
        t = text.rstrip(".!? ")
        kind = ("connect_mail" if G.RE_CONNECT_MAIL.search(t) else "connect_cal" if G.RE_CONNECT_CAL.search(t) else
                "connect" if G.RE_CONNECT.search(t) else "task_done" if G.RE_TASK_DONE.match(t) else
                "task_add" if G.RE_ADD_TASK.match(t) else "tasks" if G.RE_LIST_TASKS.search(t) else
                "event_add" if G.RE_ADD_EVENT.match(t) else "mail" if G.RE_MAIL.search(t) else
                "agenda" if G.RE_AGENDA.search(t) else None)
        if not kind:
            return None
        if self.google is None:
            self.google = G.Google(expand("data"))
        full = self.google.token_file.exists()
        todo = S.Todo(expand("data/todo.md"))

        def ask(what: str, text: str) -> Reply:
            return Reply(text, [A.Action(what, text, {}, needs_confirm=False)], "rules")
        try:
            if kind == "connect_mail":
                return ask("connect_mail", "Подключаю почту: открыла страницу паролей приложений Google — следуй окошку.")
            if kind == "connect_cal":
                return ask("connect_calendar", "Подключаю календарь: открыла настройки Google Календаря — следуй окошку.")
            if kind == "connect":
                if self.google.client_file.exists() or self.google._find_client_file():
                    return Reply(self.google.connect(), route="rules")      # полный режим уже настроен
                return ask("connect_simple", "Подключаю по-простому: календарь и почту, без Google Cloud. Следуй окошкам.")
            if kind == "mail":
                if full:
                    return Reply(self.google.unread(), route="rules")
                addr, pw = S.get_secret("gmail_address"), S.get_secret("gmail_app_password")
                if not (addr and pw):
                    return ask("connect_mail", "Почта ещё не подключена — давай подключим, это 2 минуты.")
                return Reply(S.unread_imap(addr, pw), route="rules")
            if kind == "agenda":
                if full:
                    return Reply(self.google.agenda(t), route="rules")
                url = S.get_secret("ical_url")
                if not url:
                    return ask("connect_calendar", "Календарь ещё не подключён — давай подключим, это одна ссылка.")
                return Reply(self._agenda_simple(t, url), route="rules")
            if kind == "tasks":
                if full:
                    return Reply(self.google.tasks(), route="rules")
                items = todo.open_items()
                return Reply(("В списке дел: " + "; ".join(items) + ".") if items else "Список дел пуст. Подозрительно продуктивно.", route="rules")
            if kind == "task_add":
                m = G.RE_ADD_TASK.match(t)
                title = (m.group("title") or m.group("title_en")).strip(" :«»\"")
                title = title[0].upper() + title[1:]
                if full:
                    return Reply(self.google.add_task(title), route="rules")
                todo.add(title)
                return Reply(f"Добавила в список дел: «{title}».", route="rules")
            if kind == "task_done":
                m = G.RE_TASK_DONE.match(t)
                w = (m.group("w") or m.group("w2")).strip()
                done = todo.done(w)
                return Reply(f"Вычеркнула: «{done}». Красава." if done else f"Не нашла в списке дел «{w}».", route="rules")
            m = G.RE_ADD_EVENT.match(t)
            ev = G.Google.parse_event(m.group("rest") or m.group("rest_en"))
            if isinstance(ev, str):
                return Reply(f"Не поняла: {ev}.", route="error")
            if full:
                # в календарь пишем только после «Да» — вдруг я не так расслышала дату
                act = A.Action("gcal_add", f"Добавить в календарь {G.describe_event(ev)}", {"event": ev}, needs_confirm=True)
                return Reply(f"Добавить в календарь {G.describe_event(ev)}?", [act], "rules")
            act = A.Action("open_url", "Открыть событие в Google Календаре", {"url": S.calendar_add_url(ev)}, needs_confirm=False)
            return Reply(f"Открыла в календаре {G.describe_event(ev)} — проверь и нажми «Сохранить».", [act], "rules")
        except G.GoogleError as e:
            return Reply(str(e), route="error")
        except Exception as e:  # noqa: BLE001 — сеть, права, неверный пароль
            return Reply(f"Google ответил ошибкой: {str(e)[:200]}", route="error")

    def _agenda_simple(self, text: str, url: str) -> str:
        import datetime as dt

        from . import google_simple as S
        from .when import fmt_day, parse_day
        now = dt.datetime.now().astimezone()
        day = parse_day(text, now.date()) or now.date()
        items = S.fetch_agenda(url, day, now.tzinfo)
        when = "Сегодня" if day == now.date() else "Завтра" if day == now.date() + dt.timedelta(days=1) else fmt_day(day)
        if not items:
            return f"{when} в календаре пусто. Свобода."
        return f"{when}: " + "; ".join(f"{a} — {b}" for a, b in items) + "."

    # ------------------------------------------------------------------ то, что на экране
    def _open_nth(self, word: str) -> Reply:
        from . import control
        n = control.ordinal(word)
        if not n:
            return Reply(f"Не поняла, какую по счёту: «{word}».", route="error")
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                links = control.page_links()
                if not links:              # Chrome включает «доступность» по первому запросу — пробуем ещё раз
                    time.sleep(0.6)
                    links = control.page_links()
                if not links:
                    return Reply("Не вижу ссылок в этом окне. Открой страницу в браузере и повтори.", route="error")
                name, ctrl = links[n - 1] if n > 0 and n <= len(links) else links[-1] if n == -1 else (None, None)
                if not ctrl:
                    return Reply(f"На странице только {len(links)} ссылок.", route="error")
                control.click_element(ctrl)
        except ImportError:
            return Reply("Не установлен пакет uiautomation — перезапусти run_capri.bat.", route="error")
        except Exception as e:  # noqa: BLE001
            return Reply(f"Не вышло: {e}", route="error")
        return Reply(f"Открываю: «{name[:80]}».", route="rules")

    def ask(self, kind: str, **data) -> None:
        """Запомнить, что Капри спросила, — следующая фраза будет ответом."""
        self.pending = {"kind": kind, "at": time.monotonic(), **data}

    def _answer(self, p: dict, text: str) -> Reply | None:
        """Ответ на её вопрос. None — это не ответ, а новая команда."""
        t = text.lower().strip(" .!?,")
        if RE_NO.match(t):
            return Reply(p.get("no", "Окей, не надо."), route="rules")
        if p["kind"] == "timer":
            if not re.search(r"сек|мин|час|[:.]\d|second|minute|hour|stund|полчас|полминут", t) and len(C.numbers(t)) == 1:
                secs = C.numbers(t)[0] * 60             # «десять», «на пять» — значит, минут
            else:
                secs = C.parse_seconds(re.sub(r"полчаса", "30 минут", t))
            if secs:
                act = A.make_timer(secs / 60, p.get("label", ""))
                if act:
                    return Reply(f"Окей, {act.description.lower()}.", [act], "rules")
            return None
        if p["kind"] == "confirm" and RE_YES.match(t):
            return p["do"]()
        return None

    def _ytm_play(self, text: str) -> Reply | None:
        """YouTube Music: «включи первый трек», «воспроизведи Риплей Микс». None — не про это или YouTube Music не открыт."""
        from . import control
        bare = text.strip(" .!?")
        if RE_IN_YT.search(bare) and not RE_IN_YTM.search(bare):
            return None                               # «включи … на ютубе» — это обычный YouTube
        said_ytm = bool(RE_IN_YTM.search(bare))
        bare = RE_IN_YTM.sub("", bare)
        # «открой плейлист On repeat» — открыть (без ▶); слово «плейлист» говорит, что это YouTube Music
        m_openpl = re.match(r"^(?:открой|откройте|покажи|перейди\s+в)\s+(?:плейлист\w*|микс|альбом)\s+(?P<name>.+)$", bare, re.I) \
            or (said_ytm and re.match(r"^(?:открой|откройте|покажи)\s+(?P<name>.+)$", bare, re.I))
        if m_openpl:
            return self._ytm_open(m_openpl.group("name").strip(" .,"), play=False)
        m_nth = RE_PLAY_NTH.match(bare)
        m_named = RE_PLAY_NAMED.match(bare)
        if said_ytm and not m_named:                  # «включи / найди Лампочки в ютуб мюзик»
            m_named = re.match(r"^(?:включи|поставь|запусти|найди|поищи)\s+(?P<name>.+)$", bare, re.I)
        if m_nth and re.match(r"(?:следующ|предыдущ|прошл|этот|эту|это|любой|любую|какой|какую|мой|мою)", m_nth.group("n") or "", re.I):
            m_nth = None
        # «нажми, до утра мы гуляем» — трек с открытой страницы (только то, что видно, без поиска)
        m_press = None if (m_nth or m_named) else re.match(r"^(?:нажми|кликни|ткни)\s*,?\s+(?:на\s+)?(?P<name>.{3,})$", bare, re.I)
        if m_press and re.match(r"(?:кнопк|play|плей|плэй|паузу|пауза|энтер|enter|ctrl|контрол|esc)", m_press.group("name"), re.I):
            m_press = None
        if not m_nth and not m_press and not (m_named and not RE_GENERIC_PLAY.fullmatch(m_named.group("name").strip())):
            return None
        try:
            h = control.ytm_window()
        except Exception:  # noqa: BLE001
            h = 0
        if not h:
            return None
        if not said_ytm and not m_nth:
            # YouTube Music открыт, но ты сейчас в другом окне (например, YouTube в браузере) — не перехватываем.
            # «Третий трек» — только про YouTube Music (на YouTube говорят «третье видео»), его не проверяем
            try:
                fg_title, _exe = control.context_window()
            except Exception:  # noqa: BLE001
                fg_title = ""
            if "youtube music" not in fg_title.lower():
                return None
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                control.focus(h)
                if m_nth:
                    word = m_nth.group("n") or m_nth.group("n2") or "1"
                    n = control.ordinal(word)
                    if not n:
                        return None
                    tracks = control.ytm_tracks(h) or (time.sleep(0.6) or control.ytm_tracks(h))
                    if not tracks:
                        return Reply("Не вижу треков в YouTube Music — открой плейлист и повтори.", route="error")
                    if n > len(tracks):
                        return Reply(f"Тут только {len(tracks)} треков.", route="error")
                    name, ctrl = tracks[n - 1] if n > 0 else tracks[-1]
                    control.click_element(ctrl)
                    return Reply(f"Включаю «{name[:80]}».", route="rules")
                if m_press:
                    want = m_press.group("name").strip(" .,")
                    best = max(((control.name_score(want, nm), nm) for nm, _c in control.ytm_tracks(h)), default=(0.0, ""))
                    if best[0] >= 0.75 and control.ytm_click(h, best[1]):
                        return Reply(f"Включаю «{best[1][:80]}».", route="rules")
                    return None
                target = m_named.group("name").strip()
                r = self._open_on_screen(target, autoplay=True, ytm_hwnd=h)
                return r if r is not None and "Ищу на YouTube" not in r.text else None
        except ImportError:
            return None
        except Exception as e:  # noqa: BLE001
            return Reply(f"Не вышло: {e}", route="error")

    def _ytm_open(self, name: str, play: bool) -> Reply | None:
        """Плейлист из левого меню YouTube Music (или ссылка на странице). Не нашли — поиск в YouTube Music с вопросом."""
        from . import control
        h = control.ytm_window()
        if not h:
            return None
        name = re.sub(r"^(?:плейлист\w*|микс|альбом)\s+", "", name, flags=re.I).strip()
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                control.focus(h)
                cands = control.ytm_sidebar(h) + control.page_links(hwnd=h, min_len=3)
                score, found, ctrl = max(((control.title_score(name, nm), nm, c) for nm, c in cands),
                                         key=lambda x: x[0], default=(0.0, "", None))
                if ctrl is not None and score >= 0.8:
                    control.click_element(ctrl)
                    if play:
                        for _ in range(6):
                            time.sleep(0.8)
                            if control.ytm_play_page(h):
                                return Reply(f"Включаю «{found[:80]}».", route="rules")
                    return Reply(f"Открываю «{found[:80]}».", route="rules")
                return self._ytm_search_ask(name)
        except ImportError:
            return None
        except Exception as e:  # noqa: BLE001
            return Reply(f"Не вышло: {e}", route="error")

    def _ytm_search_ask(self, target: str) -> Reply:
        """На экране такого нет — ищем в YouTube Music и спрашиваем, то ли это."""
        from . import control
        h = control.ytm_window()
        results = control.ytm_search(h, target) if h else []
        if not results:
            return Reply(f"В YouTube Music не нашла «{target}».", route="error")
        name = results[0][0]

        def play():
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                ok = control.ytm_click(control.ytm_window(), name)
            return Reply(f"Включаю «{name[:80]}»." if ok else f"Не смогла нажать «{name[:80]}» — нажми сам.",
                         route="rules" if ok else "error")
        self.ask("confirm", do=play, no="Окей, не включаю.")
        return Reply(f"Нашла «{name[:80]}». Включить?", route="rules", ask=True)

    def _open_on_screen(self, target: str, autoplay: bool = False, ytm_hwnd: int = 0) -> Reply | None:
        """«Открой подземелье чикен карри 54 выпуск»: ищем такую ссылку на открытой странице и кликаем;
        не нашли, но это похоже на видео — ищем на YouTube. None — пусть решает модель."""
        from . import control
        try:
            title, exe = control.context_window()
        except Exception:  # noqa: BLE001
            title, exe = "", ""
        if ytm_hwnd:                                  # точно про YouTube Music — смотрим его окно, где бы оно ни было
            title, exe = "YouTube Music", "chrome.exe"
        in_browser = exe in control.BROWSERS
        if in_browser:
            try:
                import uiautomation as auto
                with auto.UIAutomationInitializerInThread():
                    kw = {"hwnd": ytm_hwnd} if ytm_hwnd else {}
                    links = control.page_links(min_len=4, **kw) or (time.sleep(0.6) or control.page_links(min_len=4, **kw))
                    score, found, ctrl = max(((control.name_score(target, nm), nm, c) for nm, c in links),
                                             key=lambda x: x[0], default=(0.0, "", None))
                    ytm = "youtube music" in title.lower()
                    if ctrl is not None and score >= 0.8:
                        # трек — в списке справа (клик его и включает); плейлисты — слева, в меню
                        is_track = ytm and control.in_content_area(control.ytm_window(), ctrl)
                        control.click_element(ctrl)
                        if ytm and is_track:
                            return Reply(f"Включаю «{found[:80]}».", route="rules")   # клик по треку его и включает
                        if autoplay and ytm:
                            # плейлист или микс в YouTube Music: страница открылась — жмём ▶ в шапке
                            for _ in range(6):
                                time.sleep(0.8)
                                if control.ytm_play_page(control.ytm_window() or 0):
                                    return Reply(f"Включаю «{found[:80]}».", route="rules")
                        return Reply(f"Открываю: «{found[:80]}».", route="rules")
                    if autoplay and ytm:
                        return self._ytm_open(target, play=True)    # левое меню, потом поиск YouTube Music
            except Exception:  # noqa: BLE001
                pass
        if in_browser and "youtube music" in title.lower() and not ytm_hwnd:
            return self._ytm_open(target, play=autoplay)       # в YouTube Music — его меню и его поиск, не YouTube
        if RE_VIDEO_WORDS.search(target) or "youtube" in title.lower():
            return Reply(f"Ищу на YouTube «{target}».", [A.make_youtube_search(target)], "rules")
        return None

    def _vision_click(self, what: str, on_status) -> Reply:
        """Кнопки нет в «дереве» окна (игры, видео, свои интерфейсы) — Claude смотрит на скриншот и говорит, куда нажать."""
        from . import control, screen
        from .config import expand
        if not self.cfg["claude_code"].get("enabled", True):
            return Reply(f"Не нашла в окне «{what}». (Посмотреть на экран могу через Claude — он выключен в config.yaml.)", route="error")
        on_status("CLAUDE")
        try:
            path, scale, x0, y0, w, h = screen.screenshot_for_locate(expand(self.cfg["claude_code"].get("workdir", "workspace")))
            answer = self.claude(screen.locate_prompt(what, path, w, h), self.cfg["claude_code"])
        except claude_code.ClaudeError as e:
            return Reply(str(e), route="error")
        except Exception as e:  # noqa: BLE001
            return Reply(f"Не смогла посмотреть на экран: {e}", route="error")
        pt = screen.parse_point(answer)
        if not pt:
            return Reply(f"Посмотрела — не вижу на экране «{what}».", route="claude")
        if control.RISKY.search(what):
            x, y = screen.to_screen(pt, scale, x0, y0)
            act = A.Action("click_at", f"Нажать «{what}»", {"x": x, "y": y}, needs_confirm=True)
            return Reply(f"Нашла «{what}». Нажать?", [act], "claude")
        control.click(at=screen.to_screen(pt, scale, x0, y0))
        return Reply(f"Нажала «{what}» (посмотрела на экран).", route="claude")

    def _weather(self, text: str) -> Reply:
        from . import weather
        lang = C.lang_of(text)
        try:
            return Reply(weather.forecast(text, self.cfg.get("city", "München"), lang), route="rules")
        except Exception as e:  # noqa: BLE001 — нет интернета и т.п.
            return Reply(f"Погоду узнать не вышло: {e}", route="error")

    def _screen(self, text: str, on_status) -> Reply:
        """Снимок экрана → Claude смотрит и отвечает (локальная модель картинки не видит)."""
        from . import screen
        from .config import expand
        on_status("CLAUDE")
        try:
            paths, title = screen.capture(expand(self.cfg["claude_code"].get("workdir", "workspace")))
        except Exception as e:  # noqa: BLE001
            return Reply(f"Не смогла сделать снимок экрана: {e}", route="error")
        return self._ask_claude(screen.prompt(text, paths, title), on_status, label=text)

    # быстрые правила --------------------------------------------------------
    def _rules(self, text: str) -> Reply | None:
        bare_cmd = text.strip(" .!?")
        for rx, mode, ask, said in RE_POWER:
            if rx.fullmatch(bare_cmd):
                act = A.Action("power", ask or "Отменить", {"mode": mode}, needs_confirm=mode != "abort")
                return Reply(said, [act], "rules")
        # «открой вторую ссылку / третье видео» — до системных команд, иначе «нажми …» уйдёт в поиск кнопки
        if m := RE_NTH.match(text.rstrip(".!? ")):
            return self._open_nth(m.group("n") or m.group("n2"))
        # системные команды (музыка, громкость, клавиши, проверка ПК…) — выполняем прямо здесь, в рабочем потоке
        from . import scenarios
        sc_data = scenarios.load()
        if hit := scenarios.match(text, sc_data):
            return self._scenario(hit[0], hit[1], sc_data)
        if m := RE_OPEN_ON_SCREEN.match(text.strip(" .!?")):
            app, screen_no = m.group("app").strip(), 1 if m.group("one") else 2
            if self.open_action(app) is not None:
                from . import scenarios
                runner = scenarios.Runner(self.open_action, self.system, lambda act: A.execute(act, self.cfg))
                done, failed, *_ = runner.run([{"open": app, "screen": screen_no, "place": "max"}])
                if failed:
                    return Reply(f"Не вышло: {failed[0]}.", route="error")
                note = (" (" + "; ".join(runner.notes) + ")") if runner.notes else ""
                return Reply(f"Открыла {done[0] if done else app} на {'основном' if screen_no == 1 else 'втором'} экране{note}.",
                             route="rules")
        if (r := self._ytm_play(text)) is not None:
            return r
        if RE_BREAKS.search(text):
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(час|ч\b|минут|мин)", text, re.I)
            off = re.search(r"не напомина|хватит напомин|выключи напомин|отключи напомин|убери напомин", text, re.I)
            minutes = 0 if off else round(float(m.group(1).replace(",", ".")) * (60 if m.group(2).startswith("ч") else 1)) if m else 120
            act = A.Action("break_reminder", "Напоминание об отдыхе", {"minutes": minutes}, needs_confirm=False)
            msg = "Окей, напоминать не буду." if not minutes else f"Буду напоминать отдохнуть каждые {_human(minutes)}."
            return Reply(msg, [act], "rules")
        if cmd := C.match(text):
            if cmd.op == "quiet":
                return Reply("Молчу.", [A.Action("quiet", "Замолчать", needs_confirm=False)], "rules")
            result = self.system.run(cmd)
            if cmd.op == "click_name" and str(result.get("error", "")).startswith("не нашла"):
                return Reply(cmd.text, route="vision")       # кнопки нет в дереве окна — посмотрим глазами (Claude)
            if cmd.op == "click_name" and result.get("confirm"):
                act = A.Action("click_confirmed", f"Нажать «{result['found']}»", {"name": cmd.text}, needs_confirm=True)
                return Reply(C.reply(cmd, C.lang_of(text), result), [act], "rules")
            if cmd.op == "play" and result.get("nothing"):
                # ничего не играет — открываем YouTube Music (приложение, если есть, иначе сайт)
                app = self.apps.find("youtube music", threshold=0.95)
                act = A.make_start_app(app) if app else A.make_open_site("ютуб мюзик")
                return Reply("Сейчас ничего не играет — открываю YouTube Music, жми ▶.", [act], "rules")
            return Reply(C.reply(cmd, C.lang_of(text), result), route="error" if result.get("error") else "rules")
        if (g := self._google(text)) is not None:
            return g
        if RE_HELP.search(text):
            return self._help(desktop=bool(re.search(r"рабоч\w* стол", text, re.I)))
        if m := RE_NEARBY.search(text):
            q = m.group(0).strip(" .?!")
            q = re.sub(r"^(?:найди|покажи|подскажи|где)\s+(?:мне\s+)?", "", q, flags=re.I)
            url = "https://www.google.com/maps/search/" + __import__("urllib.parse").parse.quote(q)
            return Reply(f"Открываю карту: «{q}».", [A.Action("open_url", f"Карта: {q}", {"url": url}, needs_confirm=False)], "rules")
        if m := RE_SEARCH.match(text):
            q = m.group(1).strip()
            return Reply(f"Ищу «{q}».", [A.make_web_search(q, self.cfg["search_url"])], "rules")
        if RE_TIMER_BARE.match(text.strip()):
            self.ask("timer")
            return Reply("На сколько?", route="rules", ask=True)
        if m := RE_TIMER.search(text):
            minutes = float(m.group(1).replace(",", ".")) * UNIT_TO_MIN[m.group(2).lower()]
            if act := A.make_timer(minutes, (m.group(3) or "").strip()):
                return Reply(f"Окей, {act.description.lower()}.", [act], "rules")
        if m := RE_NOTE.match(text):
            return Reply("Записываю.", [A.make_note(m.group(1).strip())], "rules")
        from . import files
        f = files.match(text)
        if isinstance(f, str):
            return Reply(f"Не вышло: {f}.", route="error")
        if f:
            return Reply(f"{f.description}.", [f], "rules")
        bare = text.rstrip(".!? ")
        if f := files.match_open(bare):
            return Reply(f"{f.description}.", [f], "rules")
        if m := RE_YOUTUBE.match(bare):
            q = next(g for g in m.groups() if g).strip()
            return Reply(f"Ищу на YouTube «{q}».", [A.make_youtube_search(q)], "rules")
        if m := RE_OPEN.match(bare):
            target = re.sub(r"^(?:в|на|in|auf)\s+", "", m.group(1).strip())
            # Whisper: «YouTube-музык», «ютуб мерзик», «YouTube мьезик» → YouTube Music
            target = re.sub(r"(?:ютуб|youtube|ютьюб)[\s-]*(?:m\w*sic|м\w{0,3}з\w*к\w*)", "youtube music", target, flags=re.I)
            # «открой в YouTube Music понравившуюся музыку» / «…, понравившаяся музыка» — сначала то, что внутри
            if "," in target and (inner := A.make_open_site(target.split(",", 1)[1], self.cfg.get("sites"))):
                return Reply(f"{inner.description}.", [inner], "rules")
            if act := self.open_action(target):
                return Reply(f"{act.description}.", [act], "rules")
            autoplay = bool(re.match(r"(?:включи|запусти|play|start)\b", bare, re.I))   # «открой» — только открыть
            if (r := self._open_on_screen(target, autoplay=autoplay)) is not None:
                return r
        return None  # не узнали команду — пусть думает модель

    def open_action(self, target: str) -> A.Action | None:
        """Что значит «открой <target>»: программа из config → программа из «Пуска» (точно) → сайт → программа «похоже» → домен."""
        target = re.sub(r"(?:ютуб|youtube|ютьюб)[\s-]*(?:m\w*sic|м\w{0,3}з\w*к\w*)", "youtube music", target, flags=re.I)
        # ты сам попросил открыть — без лишнего «Вы уверены?»
        if act := A.make_open_app(target, self.cfg["apps"]):
            act.needs_confirm = False
            return act
        app = self.apps.find(target, threshold=0.95)
        site = A.make_open_site(target, self.cfg.get("sites"))
        if not app and not site:
            app = self.apps.find(target)
        if app:
            return A.make_start_app(app)
        if site:
            return site
        if RE_DOMAIN.match(target) and (act := A.make_open_url(target)):
            act.needs_confirm = False
            return act
        return None

    # сценарии ---------------------------------------------------------------
    def _scenario(self, name: str, on: bool, data: dict) -> Reply:
        from . import scenarios
        sc = data.get(name) or {}
        steps = sc.get("steps" if on else "off_steps") or []
        if not on and not steps:
            return Reply(f"Сценарий «{name}» нечего выключать.", route="rules")
        runner = scenarios.Runner(self.open_action, self.system, lambda act: A.execute(act, self.cfg))
        done, failed, reminder, say = runner.run(steps)
        parts = [say or (f"Сценарий «{name}» готов." if on else f"Сценарий «{name}» выключен.")]
        if runner.notes:
            parts.append("(" + "; ".join(runner.notes).capitalize() + ".)")
        if failed:
            parts.append("Не вышло: " + "; ".join(failed) + ".")
        acts = []
        if reminder is not None:
            acts.append(A.Action("break_reminder", "Напоминание об отдыхе", {"minutes": reminder}, needs_confirm=False))
        from .log import log
        log.info("сценарий %s (%s): готово %s, ошибки %s", name, "вкл" if on else "выкл", done, failed)
        return Reply(" ".join(parts), acts, "rules")

    # локальная модель -------------------------------------------------------
    def _local(self, text: str, on_status, on_token=None, should_stop=None) -> Reply:
        on_status("THINKING")
        self._remember("user", text)
        attitude = self.cfg.get("persona", {}).get("attitude", "sassy")
        slang = self.cfg.get("persona", {}).get("slang", True)
        messages = [{"role": "system", "content": system_prompt(self.cfg["user_name"], attitude, slang)}, *self.history]
        try:
            msg = self.llm.chat(messages, on_token=on_token, should_stop=should_stop)
        except OllamaError as e:
            self.history.pop()
            return Reply(str(e), route="error")

        acts: list[A.Action] = []
        notes: list[str] = []
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            name, args = fn.get("name"), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name == "ask_claude":
                return self._ask_claude(args.get("task") or text, on_status, remembered=True)
            if not self._tool_makes_sense(name, args, text):
                continue                       # модель «додумала» действие, о котором не просили
            act = self._tool_to_action(name, args)
            if act:
                acts.append(act)
            else:
                notes.append(f"(не могу: {name} {args})")
        if not acts and not (msg.get("content") or "").strip() and msg.get("tool_calls"):
            # все действия отброшены, а текста нет — просим ответить словами, без инструментов
            try:
                msg = self.llm.chat(messages, tools=None, on_token=on_token, should_stop=should_stop)
            except OllamaError as e:
                self.history.pop()
                return Reply(str(e), route="error")

        # модели иногда пишут вызов инструмента текстом: «(вызывает инструмент open_app с параметром …)» — убираем
        content = re.sub(r"\((?:вызывает|вызываю|calls?|calling)[^)]*\)\s*", "", (msg.get("content") or "")).strip()
        if not content:
            content = " ".join(a.description + "." for a in acts) or "Хм, не поняла. Переформулируешь?"
        if notes:
            content += "\n" + "\n".join(notes)
        self._remember("assistant", content)
        # спросила что-то в ответ — послушаем, что ты скажешь (история разговора у модели есть)
        last = re.split(r"(?<=[.!?…])\s+", content.strip())[-1] if content.strip() else ""
        return Reply(content, acts, "local", ask=not acts and len(content) < 400 and last.endswith("?"))

    @staticmethod
    def _tool_makes_sense(name: str, args: dict, text: str) -> bool:
        """Отсекаем выдумки маленькой модели: таймер без слов о времени, Spotify, когда о нём не говорили."""
        if name == "set_timer":
            return bool(RE_TIME_WORDS.search(text))
        if name in ("open_app", "open_url"):
            what = str(args.get("name") or args.get("url") or "").lower()
            if "spotify" in what and not re.search(r"спотиф|spotify", text, re.I):
                return False
        if name == "web_search" and re.search(r"почт|мейл|e-?mail|письм", text, re.I):
            return False                       # «что у меня по почте» — не повод гуглить
        return True

    def _tool_to_action(self, name: str, args: dict) -> A.Action | None:
        try:
            if name == "open_app":
                n = str(args.get("name", ""))
                if act := A.make_open_app(n, self.cfg["apps"]):
                    return act
                if app := self.apps.find(n):
                    act = A.make_start_app(app)
                    act.needs_confirm = True   # придумала модель, а не ты — спросим
                    return act
                return A.make_open_site(n, self.cfg.get("sites"))
            if name == "open_url":
                return A.make_open_url(str(args.get("url", "")))
            if name == "web_search":
                return A.make_web_search(str(args.get("query", "")), self.cfg["search_url"])
            if name == "set_timer":
                return A.make_timer(float(args.get("minutes", 0)), str(args.get("label", "")))
            if name == "save_note":
                return A.make_note(str(args.get("text", "")))
        except (TypeError, ValueError):
            return None
        return None

    # Claude ---------------------------------------------------------------------
    def _ask_claude(self, task: str, on_status, remembered: bool = False, label: str | None = None) -> Reply:
        if not self.cfg["claude_code"].get("enabled", True):
            return Reply("Claude выключен в config.yaml.", route="error")
        on_status("CLAUDE")
        if not remembered:
            self._remember("user", label or task)
        try:
            answer = self.claude(task, self.cfg["claude_code"])
        except claude_code.ClaudeError as e:
            return Reply(str(e), route="error")
        self._remember("assistant", answer)
        if RE_WANTS_CODE.search(label or task):
            if saved := self._save_code(answer):
                return saved
        return Reply(answer, route="claude")

    def _help(self, desktop: bool = False) -> Reply:
        """Список команд — в файл и в Блокнот (голосом такое не прочитаешь)."""
        from . import files
        from .help import COMMANDS
        path = files.known_folder("desktop" if desktop else "documents") / "Capri — команды.txt"
        try:
            path.write_text(COMMANDS, encoding="utf-8-sig")
        except OSError as e:
            return Reply(f"Не смогла записать список: {e}", route="error")
        act = A.Action("open_file", "Открыть список команд", {"path": str(path)}, needs_confirm=False)
        return Reply("Умею много: музыка, окна, мониторы, мышь и клавиатура, файлы, Google, погода, экран, скрипты. "
                     f"Полный список открыла в Блокноте — он лежит {'на рабочем столе' if desktop else 'в «Документах»'}.", [act], "rules")

    def _save_code(self, answer: str) -> Reply | None:
        """«Напиши скрипт…» — код из ответа кладём в файл и открываем в Блокноте; озвучивается только пояснение."""
        import datetime as dt

        from . import files
        blocks = RE_CODE_BLOCK.findall(answer)
        if not blocks:
            return None
        lang, code = max(blocks, key=lambda b: len(b[1]))
        ext = CODE_EXT.get(lang.lower().strip(), ".ps1" if re.search(r"\b(Get-|Set-|\$env:|Write-Host)", code) else ".txt")
        folder = files.known_folder("documents") / "Capri скрипты"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"скрипт-{dt.datetime.now():%Y%m%d-%H%M%S}{ext}"
            path.write_text(code.strip() + "\n", encoding="utf-8-sig" if ext in (".ps1", ".bat", ".cmd") else "utf-8")
        except OSError:
            return None
        text = RE_CODE_BLOCK.sub("", answer).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        note = f"Сохранила в «Документы\\Capri скрипты\\{path.name}» и открыла в Блокноте."
        act = A.Action("open_file", f"Открыть {path.name}", {"path": str(path)}, needs_confirm=False)
        return Reply(f"{text}\n\n{note}" if text else note, [act], "claude")

    def _remember(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        del self.history[:-HISTORY_LIMIT]
