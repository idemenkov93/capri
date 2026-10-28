"""Google: почта (только чтение), календарь и задачи (Google Tasks).

Подключение один раз (подробно — docs/GOOGLE.md):
  1. Google Cloud Console → свой проект → включить Gmail API, Google Calendar API, Google Tasks API.
  2. OAuth client ID типа «Desktop app» → скачать JSON → положить как data/google_client.json.
  3. Сказать Капри «подключи гугл» — откроется браузер, разрешаешь доступ. Ключ доступа хранится в data/google_token.json
     (только у тебя на компьютере; отключить — удалить этот файл или отозвать доступ в настройках Google-аккаунта).

Капри НЕ отправляет письма и ничего не удаляет: почту только читает, в календарь и задачи — добавляет.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .when import fmt_day, parse_day, parse_time

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]

RE_CONNECT = re.compile(r"^(подключи|подключить|авторизуй\w*|войди в)\s+(гугл|google)|^connect google", re.I)
RE_CONNECT_MAIL = re.compile(r"^(подключи|подключить)\s+(почту|gmail|джимейл)|^connect (gmail|mail)", re.I)
RE_CONNECT_CAL = re.compile(r"^(подключи|подключить)\s+(календарь|гугл календарь)|^connect calendar", re.I)
RE_TASK_DONE = re.compile(r"^(?:отметь|вычеркни|закрой)\s+(?:задачу|дело)\s+(?P<w>.+?)(?:\s+(?:выполненной|как выполненную|сделанной))?$|"
                          r"^(?:я\s+)?(?:сделал|выполнил|сделала|выполнила)\s+(?:задачу\s+)?(?P<w2>.+)$", re.I)
# про почту: слово о письмах + вопрос («сколько / есть ли / новые / непрочитанные / проверь / что пришло»)
_MAIL_WORD = (r"(письм\w*|писем|почт\w*|мейл\w*|имейл\w*|емейл\w*|e-?mails?|mails?|gmail|джимейл\w*|входящи\w*|"
              r"сообщени\w*(?!.*(?:телеграм|telegram|whats\s*app|ватсап|дискорд|discord|вк\b|инст)))")
_MAIL_ASK = r"(что\s+(?:у меня\s+)?(?:по|на|в)|сколько|есть\s+ли|есть|новы\w*|непрочитанн\w*|не\s*прочитанн\w*|проверь|глянь|посмотри|что\s+(там\s+)?(нового|пришло)|пришл\w*|any|new|unread|check|neue|ungelesen\w*)"
RE_MAIL = re.compile(rf"{_MAIL_ASK}\b.*\b{_MAIL_WORD}|{_MAIL_WORD}\b.*\b{_MAIL_ASK}", re.I)
RE_AGENDA = re.compile(r"(что|какие)\s+(у меня\s+)?(в календаре|планы|по планам|встреч\w*|дела)\b|"
                       r"(что|какие)\s+у меня\s+(сегодня|завтра|послезавтра|в\s+\w+)\b|(мой|моё|мое)\s+расписание|"
                       r"what('s| is) on my calendar|my schedule|was steht (heute|morgen) an|termine", re.I)
RE_ADD_EVENT = re.compile(r"^(?:добавь|запиши|поставь|создай)\s+(?:в календарь|встречу|событие|напоминание в календар\w*)\s*(?:в календарь\s+)?(?P<rest>.+)$|"
                          r"^(?:add|put)\s+(?:to (?:my )?calendar|an? (?:event|meeting))\s+(?P<rest_en>.+)$", re.I)
RE_ADD_TASK = re.compile(r"^(?:добавь|запиши|создай)\s+(?:задачу|в (?:список дел|задачи|туду|to-?do)|в дела|дело)\s*:?\s*(?P<title>.+)$|"
                         r"^add (?:a )?(?:task|to-?do)\s*:?\s*(?P<title_en>.+)$", re.I)
RE_LIST_TASKS = re.compile(r"(какие|что)\s+(у меня\s+)?(задачи|дела в списке|в списке дел|в туду|в to-?do)|мои задачи|список дел|my (tasks|to-?dos)", re.I)


class GoogleError(RuntimeError):
    pass


class Google:
    def __init__(self, data_dir: Path):
        self.client_file = Path(data_dir) / "google_client.json"
        self.token_file = Path(data_dir) / "google_token.json"
        self._services: dict = {}

    # ------------------------------------------------------------ доступ
    GUIDE = "https://github.com/idemenkov93/capri/blob/main/docs/GOOGLE.md"

    def _find_client_file(self) -> bool:
        """Скачанный из Google Cloud ключ называется client_secret_<много цифр>.json и обычно лежит в «Загрузках».
        Находим его сами (data, Загрузки, Рабочий стол) и копируем как data/google_client.json."""
        import json
        import shutil
        homes = [self.client_file.parent, Path.home() / "Downloads", Path.home() / "Desktop"]
        try:
            from .files import known_folder
            homes += [known_folder("downloads"), known_folder("desktop")]
        except Exception:  # noqa: BLE001
            pass
        for folder in homes:
            for f in sorted(folder.glob("client_secret*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []:
                try:
                    if "installed" in json.loads(f.read_text(encoding="utf-8")):   # нужен именно «Desktop app»
                        self.client_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(f, self.client_file)
                        return True
                except (OSError, ValueError):
                    continue
        return False

    def ready(self) -> tuple[bool, str]:
        try:
            import googleapiclient  # noqa: F401
            import google_auth_oauthlib  # noqa: F401
        except ImportError:
            return False, "не установлены пакеты Google — перезапусти run_capri.bat"
        if not self.client_file.exists() and not self._find_client_file():
            return False, ("Сначала нужен ключ из Google Cloud (это 10 минут, один раз). Открыла инструкцию: "
                           "сделай шаги 1–3, скачанный файл client_secret_….json оставь в «Загрузках» — я сама его найду. "
                           "Потом снова скажи «подключи гугл».")
        return True, ""

    def open_guide(self) -> None:
        import webbrowser
        webbrowser.open(self.GUIDE)

    def _creds(self, interactive: bool):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self.token_file.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception:  # noqa: BLE001 — токен отозван/просрочен: нужен новый вход
                creds = None
        if not interactive:
            raise GoogleError("Google не подключён. Скажи «подключи гугл».")
        ok, why = self.ready()
        if not ok:
            raise GoogleError(why)
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_file), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=180,
                                      success_message="Готово! Капри подключена к Google. Это окно можно закрыть.")
        self.token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def connect(self) -> str:
        self._services.clear()
        ok, why = self.ready()
        if not ok:
            self.open_guide()
            raise GoogleError(why)
        self._creds(interactive=True)
        return "Подключила Google: почта (только чтение), календарь и задачи."

    def _svc(self, name: str, version: str):
        if name not in self._services:
            ok, why = self.ready()
            if not ok:
                raise GoogleError(why)
            from googleapiclient.discovery import build
            self._services[name] = build(name, version, credentials=self._creds(interactive=False), cache_discovery=False)
        return self._services[name]

    # ------------------------------------------------------------ почта
    def unread(self, limit: int = 3) -> str:
        g = self._svc("gmail", "v1")
        res = g.users().messages().list(userId="me", q="is:unread in:inbox category:primary", maxResults=limit).execute()
        total = res.get("resultSizeEstimate", 0)
        msgs = res.get("messages", [])
        if not msgs:
            return "Новых писем нет. Тишина."
        lines = []
        for m in msgs:
            full = g.users().messages().get(userId="me", id=m["id"], format="metadata",
                                            metadataHeaders=["From", "Subject"]).execute()
            h = {x["name"]: x["value"] for x in full.get("payload", {}).get("headers", [])}
            sender = re.sub(r"\s*<.*?>", "", h.get("From", "?")).strip('" ') or "?"
            lines.append(f"от {sender}: «{h.get('Subject', '(без темы)')}»")
        more = f" Всего непрочитанных во «Входящих» примерно {total}." if total > len(msgs) else ""
        return "Новые письма: " + "; ".join(lines) + "." + more

    # ------------------------------------------------------------ календарь
    def agenda(self, text: str, now: dt.datetime | None = None) -> str:
        now = now or dt.datetime.now().astimezone()
        day = parse_day(text, now.date()) or now.date()
        start = dt.datetime.combine(day, dt.time.min, now.tzinfo)
        end = start + dt.timedelta(days=1)
        items = self._svc("calendar", "v3").events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=10).execute().get("items", [])
        when = "Сегодня" if day == now.date() else "Завтра" if day == now.date() + dt.timedelta(days=1) else fmt_day(day)
        if not items:
            return f"{when} в календаре пусто. Свобода."
        parts = []
        for e in items:
            s = e.get("start", {})
            if "dateTime" in s:
                t = dt.datetime.fromisoformat(s["dateTime"]).astimezone(now.tzinfo).strftime("%H:%M")
                parts.append(f"{t} — {e.get('summary', 'без названия')}")
            else:
                parts.append(f"весь день — {e.get('summary', 'без названия')}")
        return f"{when}: " + "; ".join(parts) + "."

    @staticmethod
    def parse_event(rest: str, now: dt.datetime | None = None) -> dict | str:
        """«завтра в 15:00 собеседование в XCOM» → {'title', 'start', 'end', 'all_day'}; строка — что непонятно."""
        now = now or dt.datetime.now().astimezone()
        day = parse_day(rest, now.date())
        tm = parse_time(rest)
        if not day and not tm:
            return "когда? Скажи, например, «завтра в 15:00»"
        day = day or (now.date() if tm and dt.time(*tm) > now.time() else now.date() + dt.timedelta(days=1))
        title = rest
        for rx in (r"\b(сегодня|завтра|послезавтра|через\s+\d+\s+дн\w*)\b", r"\b(?:в|um|at)?\s*\d{1,2}[:.]\d{2}\b",
                   r"\b(?:в|um|at)\s+\d{1,2}(?:\s*(?:час\w*|uhr|am|pm))?(?:\s+(?:утра|дня|вечера|ночи))?\b", r"\b\d{1,2}\s+\w+бря\b|\b\d{1,2}\.\d{1,2}(\.\d{2,4})?\b",
                   r"\b(в|во)\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b"):
            title = re.sub(rx, " ", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" ,.:—-") or "Событие"
        title = title[0].upper() + title[1:]
        if tm:
            start = dt.datetime.combine(day, dt.time(*tm), now.tzinfo)
            return {"title": title, "start": start, "end": start + dt.timedelta(hours=1), "all_day": False}
        return {"title": title, "start": day, "end": day + dt.timedelta(days=1), "all_day": True}

    def add_event(self, ev: dict) -> str:
        if ev["all_day"]:
            body = {"summary": ev["title"], "start": {"date": ev["start"].isoformat()}, "end": {"date": ev["end"].isoformat()}}
        else:
            body = {"summary": ev["title"], "start": {"dateTime": ev["start"].isoformat()}, "end": {"dateTime": ev["end"].isoformat()}}
        self._svc("calendar", "v3").events().insert(calendarId="primary", body=body).execute()
        return f"Добавила в календарь: {describe_event(ev)}."

    # ------------------------------------------------------------ задачи
    def tasks(self, limit: int = 7) -> str:
        items = self._svc("tasks", "v1").tasks().list(tasklist="@default", showCompleted=False, maxResults=limit).execute().get("items", [])
        if not items:
            return "Список дел пуст. Подозрительно продуктивно."
        return "В списке дел: " + "; ".join(t.get("title", "?") for t in items) + "."

    def add_task(self, title: str) -> str:
        self._svc("tasks", "v1").tasks().insert(tasklist="@default", body={"title": title}).execute()
        return f"Добавила задачу: «{title}»."


def describe_event(ev: dict) -> str:
    if ev["all_day"]:
        return f"«{ev['title']}», {fmt_day(ev['start'])}, весь день"
    return f"«{ev['title']}», {fmt_day(ev['start'].date())} {ev['start']:%H:%M}–{ev['end']:%H:%M}"
