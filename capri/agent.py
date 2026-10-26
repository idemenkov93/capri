"""Мозг Капри: быстрые команды → локальная модель → Claude для сложного."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from . import actions as A
from . import commands as C
from .llm import claude_code
from .llm.ollama_client import OllamaClient, OllamaError
from .persona import system_prompt

HISTORY_LIMIT = 20


@dataclass
class Reply:
    text: str
    actions: list[A.Action] = field(default_factory=list)
    route: str = "local"   # rules | local | claude | error


# --- быстрые правила: отрабатывают мгновенно, без ИИ -------------------------
# Русский / Deutsch / English. Всё, что не подошло, уходит модели — она понимает любой язык.
RE_OPEN = re.compile(r"^(?:открой|запусти|включи|öffne|starte|open|launch|start|play)\s+(.+?)[.!]?$", re.I)
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
    r"\b(напиши|написать|сделай|составь|сгенерируй)\s+(мне\s+)?(скрипт|код|программ\w*|функци\w*|регулярк\w*|запрос|план|резюме|письмо|"
    r"bewerbung\w*|anschreiben)|\b(объясни|расскажи)\s+(подробно|детально|по шагам)|\bразбер\w*|\bсравни\b|"
    r"\bпочему\s+(не\s+работает|ошибк\w*|падает|не\s+запускается)|\bкак\s+(настроить|исправить|починить|автоматизировать)|"
    r"\bпомоги\s+(с|мне с)\s+(код\w*|ошибк\w*|скрипт\w*|задач\w*)|\b(powershell|bash|python|sql|ansible|terraform|docker)\b.*\b(скрипт|команд\w*|ошибк\w*)|"
    r"\b(write|create)\s+(a\s+)?(script|function|program)|\bschreib\w*\s+(ein\s+)?(skript|programm)", re.I)
RE_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")

UNIT_TO_MIN = {
    "сек": 1 / 60, "с": 1 / 60, "sek": 1 / 60, "sec": 1 / 60, "s": 1 / 60,
    "мин": 1, "м": 1, "min": 1, "m": 1,
    "час": 60, "ч": 60, "stunde": 60, "std": 60, "h": 60, "hour": 60,
}


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
        o = config["ollama"]
        self.llm = llm or OllamaClient(o["url"], o["model"], o["timeout"], o.get("think"), o.get("options"))
        self.claude = claude
        self.history: list[dict] = []

    # публичный вход -------------------------------------------------------
    def handle(self, text: str, on_status: Callable[[str], None] = lambda s: None,
               on_token: Callable[[str, str], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> Reply:
        text = RE_WAKE_PREFIX.sub("", text.strip()).strip()
        if not text:
            return Reply("Ну? Я слушаю.", route="rules")
        reply = self._rules(text)
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
        # системные команды (музыка, громкость, клавиши, проверка ПК…) — выполняем прямо здесь, в рабочем потоке
        if cmd := C.match(text):
            result = self.system.run(cmd)
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
        if m := RE_SEARCH.match(text):
            q = m.group(1).strip()
            return Reply(f"Ищу «{q}».", [A.make_web_search(q, self.cfg["search_url"])], "rules")
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
            # «открой в YouTube Music понравившуюся музыку» / «…, понравившаяся музыка» — сначала то, что внутри
            if "," in target and (inner := A.make_open_site(target.split(",", 1)[1], self.cfg.get("sites"))):
                return Reply(f"{inner.description}.", [inner], "rules")
            # ты сам попросил открыть — без лишнего «Вы уверены?»
            if act := A.make_open_app(target, self.cfg["apps"]):
                act.needs_confirm = False
                return Reply(f"{act.description}.", [act], "rules")
            # программа на компьютере с точным названием → сайт из списка → программа «похоже на то» → браузер
            app = self.apps.find(target, threshold=0.95)
            site = A.make_open_site(target, self.cfg.get("sites"))
            if not app and not site:
                app = self.apps.find(target)
            if app:
                act = A.make_start_app(app)
                return Reply(f"{act.description}.", [act], "rules")
            if site:
                return Reply(f"{site.description}.", [site], "rules")
            if RE_DOMAIN.match(target) and (act := A.make_open_url(target)):
                act.needs_confirm = False
                return Reply(f"{act.description}.", [act], "rules")
        return None  # не узнали команду — пусть думает модель

    # локальная модель -------------------------------------------------------
    def _local(self, text: str, on_status, on_token=None, should_stop=None) -> Reply:
        on_status("THINKING")
        self._remember("user", text)
        attitude = self.cfg.get("persona", {}).get("attitude", "sassy")
        messages = [{"role": "system", "content": system_prompt(self.cfg["user_name"], attitude)}, *self.history]
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
            act = self._tool_to_action(name, args)
            if act:
                acts.append(act)
            else:
                notes.append(f"(не могу: {name} {args})")

        # модели иногда пишут вызов инструмента текстом: «(вызывает инструмент open_app с параметром …)» — убираем
        content = re.sub(r"\((?:вызывает|вызываю|calls?|calling)[^)]*\)\s*", "", (msg.get("content") or "")).strip()
        if not content:
            content = " ".join(a.description + "." for a in acts) or "Хм, не поняла. Переформулируешь?"
        if notes:
            content += "\n" + "\n".join(notes)
        self._remember("assistant", content)
        return Reply(content, acts, "local")

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
        return Reply(answer, route="claude")

    def _remember(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        del self.history[:-HISTORY_LIMIT]
