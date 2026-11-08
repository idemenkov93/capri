"""Фразы → админ-аптечка, проверка сайтов, PowerShell-подсказчик. Отвечает agent.handle в рабочем потоке:
коротко — в ответ (голосом), подробно — окно у курсора (действие popup) с кнопкой «📜 Команда»."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import actions as A

HOST = r"(?P<host>[\w.-]+\.[a-zа-я]{2,}|\d{1,3}(?:\.\d{1,3}){3}|localhost)"

READ = [  # (regex, функция admin.*, аргументы из групп)
    (r"^(?:какой\s+|скажи\s+|покажи\s+)?(?:у\s+меня\s+)?(?:мой\s+)?(?:ip|айпи|ай-пи)(?:[\s-]*адрес)?(?:\s+у\s+меня)?$", "my_ip"),
    (r"^(?:проверь|протестируй)\s+(?:интернет|сеть|подключение)|^(?:есть\s+ли|работает\s+ли)\s+интернет|"
     r"^(?:почему\s+)?(?:не\s+работает|пропал|отвалился)\s+интернет|^интернет\s+не\s+работает|^что\s+с\s+интернетом", "check_internet"),
    (rf"^(?:пинг|пингани|пропингуй|ping)\s+(?:до\s+)?{HOST}$", "ping_host"),
    (rf"^(?:трассировк\w*|tracert|traceroute|маршрут)\s+(?:до\s+)?{HOST}$", "traceroute"),
    (rf"^(?:проверь\s+|открыт\s+ли\s+)?порт\s+(?P<port>\d{{1,5}})\s+(?:на|у)\s+{HOST}(?:\s+открыт)?$", "port_open"),
    (r"^(?:какой\s+|скажи\s+|покажи\s+)?пароль\s+(?:от\s+)?(?:моего\s+)?(?:вай[\s-]?фа\w*|wi[\s-]?fi|wlan)$", "wifi_password"),
    (r"^(?:какие\s+|покажи\s+)?устройства\s+в\s+(?:моей\s+)?сети|^кто\s+(?:ещё\s+|еще\s+)?в\s+(?:моей\s+)?сети|"
     r"^(?:просканируй|сканируй)\s+сеть", "lan_devices"),
    (r"^что\s+(?:грузит|нагружает|тормозит)\s+(?:мой\s+)?(?:компьютер|пк|комп|процессор|систему)|"
     r"^почему\s+(?:тормозит|лагает|тупит)\s+(?:компьютер|пк|комп)|^кто\s+(?:ест|жр[её]т)\s+(?:память|процессор|оперативку)", "top_processes"),
    (r"^(?:проверь\s+)?(?:здоровье|состояние)\s+(?:моих\s+)?(?:дисков|диска|ssd|hdd)|^(?:проверь\s+)?smart(?:\s+дисков)?", "disk_health"),
    (r"^сколько\s+(?:уже\s+)?(?:работает|не\s+выключался|не\s+перезагружался)\s+(?:мой\s+)?(?:компьютер|пк|комп)|^аптайм|^uptime", "uptime"),
    (r"^(?:сводка|характеристики)\s+(?:железа|компьютера|пк)|^какое\s+у\s+меня\s+железо|^(?:что|какое)\s+у\s+меня\s+в\s+компьютере", "hardware"),
    (r"^(?:какие\s+)?ошибки\s+(?:в\s+журнале\s+)?(?:за\s+)?сегодня|^(?:какие\s+)?ошибки\s+(?:в\s+)?(?:журнал\w*|windows|виндовс)|"
     r"^журнал\s+ошибок", "errors_today"),
    (r"^почему\s+(?:мой\s+)?(?:компьютер\s+|пк\s+|комп\s+)?(?:перезагрузился|выключился|ребутнулся)", "why_reboot"),
    (r"^(?:какие\s+|последние\s+)?обновления\s+(?:windows|виндовс)|^когда\s+(?:обновлялась|обновлялся)\s+(?:windows|виндовс)", "updates"),
    (r"^(?:статус|состояние)\s+службы\s+(?P<name>\S+)|^служба\s+(?P<name2>\S+)\s+работает", "service_status"),
    (r"^(?:статус\s+)?(?:защитник\w*|антивирус\w*|defender)(?:\s+windows)?(?:\s+работает)?$|^(?:проверь|работает\s+ли)\s+(?:защитник|антивирус)", "defender"),
    (r"^(?:статус\s+|проверь\s+)?(?:брандмауэр\w*|файрвол\w*|firewall)(?:\s+включ[её]н)?$", "firewall"),
    (r"^кто\s+(?:сейчас\s+)?(?:залогинен|вош[её]л|в\s+системе)", "who_logged_in"),
    (r"^(?:неудачные|неправильные)\s+входы|^кто\s+пытался\s+войти|^(?:попытки|ошибки)\s+входа", "failed_logons"),
]

DANGER = [  # (regex, код, вопрос «Да?»)
    (r"^(?:очисти|почисти|удали)\s+(?:временные\s+файлы|темп|temp)", "clean_temp", "Удалить временные файлы старше суток из %TEMP%"),
    (r"^(?:сбрось|сброс)\s+(?:кэш\s+)?dns|^(?:сбрось|перезапусти)\s+сеть$", "flush_dns", "Сбросить кэш DNS и обновить адрес (ipconfig /flushdns /renew)"),
    (r"^(?:полный\s+сброс\s+сети|сбрось\s+сеть\s+полностью|сбрось\s+winsock)", "reset_network",
     "Полный сброс сети (winsock, TCP/IP) — нужны права администратора и перезагрузка"),
    (r"^(?:очисти|почисти|сбрось)\s+(?:очередь\s+печати|печать)|^принтер\s+завис", "print_queue",
     "Очистить очередь печати и перезапустить службу печати (права администратора)"),
]

RE_SERVICE_RESTART = re.compile(r"^перезапусти\s+службу\s+(?P<name>[\w .-]{2,60})$", re.I)
RE_SNAPIN = re.compile(r"^(?:открой|запусти|покажи)\s+(?P<what>.+)$", re.I)
RE_SITE = re.compile(r"^(?:проверь|работает\s+ли|открывается\s+ли|жив\s+ли|упал\s+ли)\s+(?:сайт|сервер)?\s*(?P<url>\S+?)\??$", re.I)
RE_WATCH = re.compile(r"^(?:следи|наблюдай|присматривай)\s+за\s+(?:сайтом|сервером)?\s*(?P<url>\S+)$", re.I)
RE_UNWATCH = re.compile(r"^(?:перестань|хватит|не\s+надо)\s+следить\s+за\s+(?:сайтом|сервером)?\s*(?P<url>\S+)$", re.I)
RE_WATCH_LIST = re.compile(r"^(?:за\s+какими\s+сайтами\s+следишь|какие\s+сайты\s+(?:отслеживаешь|ты\s+следишь))", re.I)
RE_PS = re.compile(r"^(?:как\s+(?:в|на|через)\s+(?:powershell|павершел\w*|пауэршел\w*|повершел\w*)|"
                   r"(?:команда|комманда)\s+(?:powershell|павершел\w*|пауэршел\w*)(?:\s+чтобы|\s+для)?|"
                   r"(?:powershell|павершел\w*|пауэршел\w*)\s*[:—-])\s*(?P<task>.+)$", re.I)


def route(agent, text: str):
    from .agent import Reply
    t = text.strip(" .!?")
    low = t.lower()
    from . import admin
    for rx, name in READ:
        if m := re.search(rx, low, re.I):
            g = {k: v for k, v in m.groupdict().items() if v}
            args = []
            if name in ("ping_host", "traceroute"):
                args = [g["host"]]
            elif name == "port_open":
                args = [g["host"], int(g["port"])]
            elif name == "service_status":
                args = [g.get("name") or g.get("name2")]
            return _result(Reply, lambda: getattr(admin, name)(*args))
    for rx, code, ask in DANGER:
        if re.search(rx, low, re.I):
            return Reply("Спрошу перед тем, как менять.", [A.Action("admin_do", ask, {"what": code}, needs_confirm=True)], "rules")
    if m := RE_SERVICE_RESTART.match(t):
        name = m.group("name").strip()
        return Reply("Спрошу перед перезапуском.", [A.Action("admin_do", f"Перезапустить службу «{name}» (права администратора)",
                                                            {"what": "restart_service", "name": name}, needs_confirm=True)], "rules")
    if (m := RE_SNAPIN.match(t)) and (cmd := admin.snapin(m.group("what"))):
        return Reply("Открываю.", [A.Action("open_app", f"Открыть {cmd}", {"target": cmd}, needs_confirm=False)], "rules")
    return _sites(agent, t, Reply) or _powershell(agent, t, Reply)


def _result(Reply, fn):
    from . import admin
    try:
        r = fn()
    except admin.AdminError as e:
        return Reply(f"Не получилось: {e}", route="error")
    except Exception as e:  # noqa: BLE001
        return Reply(f"Не получилось: {type(e).__name__}: {str(e)[:150]}", route="error")
    return popup_reply(Reply, r)


def popup_reply(Reply, r):
    acts = []
    if r.details or r.command:
        acts = [A.Action("popup", r.title, {"title": r.title, "text": r.details or r.say, "command": r.command},
                         needs_confirm=False)]
    return Reply(r.say, acts, "rules")


RE_BACKUP_NOW = re.compile(r"^(?:сделай|запусти|создай|делай)\s+(?:бэкап|бекап|резервн\w+\s+копи\w+)(?:\s+сейчас)?$", re.I)
RE_BACKUP_SETUP = re.compile(r"^(?:настрой\w*|настройки|покажи)\s+(?:бэкап|бекап)\w*$|^(?:бэкап|бекап)$|^куда\s+(?:бэкап|бекап)\w*", re.I)


def _sites(agent, t: str, Reply):
    from . import sites
    if RE_BACKUP_NOW.match(t):
        return Reply("Делаю бэкап.", [A.Action("backup", "Бэкап", {"do": "now"}, needs_confirm=False)], "rules")
    if RE_BACKUP_SETUP.match(t):
        return Reply("Открываю настройки бэкапа.", [A.Action("backup", "Бэкап", {"do": "setup"}, needs_confirm=False)], "rules")
    if RE_WATCH_LIST.match(t):
        return Reply("Смотрю список.", [A.Action("site_watch", "Сайты", {"list": True}, needs_confirm=False)], "rules")
    for rx, on in ((RE_UNWATCH, False), (RE_WATCH, True)):
        if (m := rx.match(t)) and (url := sites.normalize(m.group("url"))):
            say = (f"Слежу за {sites.host_of(url)}: проверяю каждые 5 минут, скажу, если упадёт." if on
                   else f"Больше не слежу за {sites.host_of(url)}.")
            return Reply(say, [A.Action("site_watch", "Сайты", {"url": url, "on": on}, needs_confirm=False)], "rules")
    if (m := RE_SITE.match(t)) and (url := sites.normalize(m.group("url"))):
        c = sites.check(url)
        return Reply(sites.say(c), [A.Action("popup", "Сайт", {"title": "Проверка сайта", "text": c.line(),
                                                               "command": f"Invoke-WebRequest {url} -Method Head"},
                                             needs_confirm=False)], "rules" if c.ok else "error")
    return None


PS_PROMPT = """Ты помогаешь начинающему системному администратору. Задача: «{task}».
Дай одну команду (или короткий скрипт) Windows PowerShell 5.1, которая это делает. Ничего не запускай.
Ответь строго JSON без markdown: {{"command": "...", "explain": "2–4 коротких предложения по-русски: что делает команда и каждый важный параметр", "admin": true/false (нужны ли права администратора), "danger": "" или чем опасна}}"""


def _powershell(agent, t: str, Reply):
    m = RE_PS.match(t)
    if not m:
        return None
    task = m.group("task").strip()
    try:
        raw = agent.ask_text(PS_PROMPT.format(task=task))
    except Exception as e:  # noqa: BLE001
        return Reply(f"Не получилось спросить помощника: {e}", route="error")
    data = parse_ps(raw)
    if not data.get("command"):
        return Reply(raw.strip()[:800], route="local")
    save_cheatsheet(task, data)
    warn = (" Нужны права администратора." if data.get("admin") else "") + (f" ⚠ {data['danger']}" if data.get("danger") else "")
    text = f"Команда уже в буфере — вставь в PowerShell сам.{warn}"
    return Reply(text, [A.Action("ps_helper", "PowerShell", {"task": task, **data}, needs_confirm=False)], "rules")


def parse_ps(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return {"command": str(d.get("command", "")).strip(), "explain": str(d.get("explain", "")).strip(),
                        "admin": bool(d.get("admin")), "danger": str(d.get("danger") or "").strip()}
        except ValueError:
            pass
    code = re.search(r"```(?:powershell|ps1?)?\s*\n(.*?)```", raw, re.S)
    return {"command": code.group(1).strip() if code else "", "explain": re.sub(r"```.*?```", "", raw, flags=re.S).strip()[:400],
            "admin": False, "danger": ""}


def cheatsheet_path() -> Path:
    from . import files
    return files.known_folder("documents") / "Capri" / "PowerShell.md"


def save_cheatsheet(task: str, data: dict, path: Path | None = None) -> Path:
    """Личная шпаргалка: каждая команда, которую спрашивал, — в Документы\\Capri\\PowerShell.md."""
    import datetime as dt
    path = path or cheatsheet_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# Моя шпаргалка PowerShell (Капри)\n", encoding="utf-8")
        if f"```powershell\n{data['command']}\n```" in path.read_text(encoding="utf-8"):
            return path
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {task[:1].upper() + task[1:]}  _{dt.date.today():%d.%m.%Y}_\n\n```powershell\n{data['command']}\n```\n\n"
                     f"{data.get('explain', '')}\n" + ("\n> Нужны права администратора.\n" if data.get("admin") else ""))
    except OSError:
        pass
    return path
