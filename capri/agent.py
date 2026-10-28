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
# игры
RE_GAME_STATS = re.compile(r"\bкак я (?:сыграл\w*|играю|отыграл\w*|играл)\b|\b(?:моя|мою) (?:игров\w* )?статистик\w*|"
                           r"\bстатистик\w* (?:матча|игры|катки|за матч|в кс|в cs)|\bкакой у меня (?:кд|к ?д|счёт|счет)\b|\bhow did i play\b", re.I)
RE_GAME_COACH = re.compile(r"\b(?:разбери|оцени|проанализируй|проанализировать|разобрать)\s+(?:мою\s+|мой\s+|эту\s+|этот\s+)?"
                           r"(?:игру|катку|матч|каточку)|\bчто мне (?:улучшить|подтянуть|исправить) в (?:игре|кс|cs|катке)", re.I)
RE_GPU_TEMP = re.compile(r"температур\w* (?:видеокарт\w*|видюх\w*|gpu)|(?:видеокарт\w*|видюх\w*) (?:греется|горячая|сколько градусов)|"
                         r"как там видеокарт\w*|сколько градусов (?:на|у) (?:видеокарт\w*|видюх\w*)", re.I)
RE_OFF = r"(?:выключи|отключи|убери|не надо|останови|хватит)"
GAME_TOGGLES = (   # (регулярка «включить», регулярка «выключить», ключ)
    (r"(?:включи|верни)\s+игров\w* помощник\w*", rf"{RE_OFF}\s+игров\w* помощник\w*|не мешай (?:мне )?в играх", "game_assist"),
    (r"(?:включи\s+|запусти\s+)?турнирн\w* режим", rf"{RE_OFF}\s+турнирн\w* режим", "cs2_tournament"),
    (r"(?:включи|верни)\s+таймер\w* бомб\w*", rf"{RE_OFF}\s+таймер\w* бомб\w*", "cs2_bomb_timer"),
    (r"(?:включи|верни)\s+подсказк\w* (?:по )?(?:экономик\w*|закуп\w*)",
     rf"{RE_OFF}\s+подсказк\w* (?:по )?(?:экономик\w*|закуп\w*)|не подсказывай (?:по )?(?:экономик\w*|закуп\w*)", "cs2_econ"),
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
        self.game = None      # GameMode из app.py: идёт ли игра, строгий ли режим, данные CS2

    # публичный вход -------------------------------------------------------
    def handle(self, text: str, on_status: Callable[[str], None] = lambda s: None,
               on_token: Callable[[str, str], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> Reply:
        text = RE_WAKE_PREFIX.sub("", text.strip()).strip()
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
        from . import screen, weather
        g = self.game if self.game is not None and self.game.active else None
        if g and g.busy_round() and not screen.is_screen_question(text):
            # посреди раунда CS2 не думаем: Claude и модель — после раунда (команды выше уже выполнились)
            return Reply("Отвечу после раунда.", [A.Action("game_defer", "Ответить после раунда", {"text": text},
                                                          needs_confirm=False)], "rules")
        if m := RE_CLAUDE.match(text):
            return self._ask_claude(m.group(1), on_status)
        if weather.is_weather_question(text) and len(text) < 120:
            return self._weather(text)
        if screen.is_screen_question(text):
            return self._screen(text, on_status)
        if RE_COMPLEX.search(text) or len(text) > 300 or text.count("\n") >= 3:
            return self._ask_claude(text, on_status)
        if g:
            # локальная модель выгружена из видеокарты ради игры — спрашиваем Claude (он в облаке)
            if self.cfg["claude_code"].get("enabled", True):
                return self._ask_claude(f"{g.context()} Вопрос: «{text}». Ответь коротко, 1–3 предложения, для озвучки, без markdown.",
                                        on_status, label=text)
            if g.strict:
                return Reply("Пока идёт игра, локальную модель не включаю — она займёт видеокарту. Спроси после матча "
                             "или включи Claude в config.yaml.", route="rules")
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

    def _open_on_screen(self, target: str) -> Reply | None:
        """«Открой подземелье чикен карри 54 выпуск»: ищем такую ссылку на открытой странице и кликаем;
        не нашли, но это похоже на видео — ищем на YouTube. None — пусть решает модель."""
        from . import control
        try:
            title, exe = control.context_window()
        except Exception:  # noqa: BLE001
            title, exe = "", ""
        in_browser = exe in control.BROWSERS
        if in_browser:
            try:
                import uiautomation as auto
                with auto.UIAutomationInitializerInThread():
                    links = control.page_links(min_len=4) or (time.sleep(0.6) or control.page_links(min_len=4))
                    score, found, ctrl = max(((control.name_score(target, nm), nm, c) for nm, c in links),
                                             key=lambda x: x[0], default=(0.0, "", None))
                    if ctrl is not None and score >= 0.8:
                        control.click_element(ctrl)
                        return Reply(f"Открываю: «{found[:80]}».", route="rules")
            except Exception:  # noqa: BLE001
                pass
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
            in_game = bool(self.game is not None and self.game.active)
            workdir = expand(self.cfg["claude_code"].get("workdir", "workspace"))
            paths, title = screen.capture(workdir, in_game=True) if in_game else screen.capture(workdir)
        except Exception as e:  # noqa: BLE001
            return Reply(f"Не смогла сделать снимок экрана: {e}", route="error")
        return self._ask_claude(screen.prompt(text, paths, title), on_status, label=text)

    # быстрые правила --------------------------------------------------------
    def _games(self, text: str) -> Reply | None:
        """Команды игрового помощника: статистика CS2, разбор матча, температура видеокарты, переключатели."""
        from . import cs2
        bare = text.strip(" .!?").lower()
        for on_rx, off_rx, key in GAME_TOGGLES:
            for rx, on in ((off_rx, False), (on_rx, True)):
                if re.fullmatch(rx, bare, re.I):
                    from .game import TOGGLE_TEXT
                    return Reply(TOGGLE_TEXT[key][on], [A.Action("game_set", "Игры", {"key": key, "on": on}, needs_confirm=False)], "rules")
        tracker = getattr(self.game, "tracker", None)
        if RE_GAME_STATS.search(text):
            return Reply(cs2.summary_text(tracker.current() if tracker else None), route="rules")
        if RE_GAME_COACH.search(text):
            data = tracker.current() if tracker else None
            if not data:
                return Reply(cs2.summary_text(None), route="rules")
            if self.game is not None and self.game.busy_round():
                return Reply("Отвечу после раунда.", [A.Action("game_defer", "Ответить после раунда", {"text": text},
                                                              needs_confirm=False)], "rules")
            return self._ask_claude(cs2.coach_prompt(text, data), lambda s: None, label=text)
        if RE_GPU_TEMP.search(text):
            from . import gpu
            g = gpu.read()
            if not g:
                return Reply("Не вижу датчиков видеокарты: нужна NVIDIA с драйвером.", route="rules")
            load = f", загрузка {g['load']}%" if g.get("load") is not None else ""
            hot = " Горячо — проверь обдув." if g["temp"] >= 85 else ""
            return Reply(f"Видеокарта: {g['temp']}°{load}.{hot}", route="rules")
        return None

    def _rules(self, text: str) -> Reply | None:
        if (gr := self._games(text)) is not None:
            return gr
        # «открой вторую ссылку / третье видео» — до системных команд, иначе «нажми …» уйдёт в поиск кнопки
        if m := RE_NTH.match(text.rstrip(".!? ")):
            return self._open_nth(m.group("n") or m.group("n2"))
        # системные команды (музыка, громкость, клавиши, проверка ПК…) — выполняем прямо здесь, в рабочем потоке
        from . import scenarios
        sc_data = scenarios.load()
        if hit := scenarios.match(text, sc_data):
            return self._scenario(hit[0], hit[1], sc_data)
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
            return Reply("На сколько? Скажи, например: «таймер на 10 минут».", route="rules")
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
            if (r := self._open_on_screen(target)) is not None:
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
        return Reply(content, acts, "local")

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
