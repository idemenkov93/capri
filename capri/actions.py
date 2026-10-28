"""Действия, которые Капри может выполнить. Каждое действие — из белого списка."""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlparse

from .config import expand


@dataclass
class Action:
    kind: str                      # open_app | open_url | web_search | timer | note
    description: str               # что увидит пользователь в подтверждении
    params: dict = field(default_factory=dict)
    needs_confirm: bool = True


def make_open_app(name: str, apps: dict) -> Action | None:
    key = name.strip().lower()
    for app_names, target in apps.items():
        # в config можно указать синонимы через |, например "браузер|browser"
        aliases = [a.strip().lower() for a in app_names.split("|")]
        if key in aliases:
            return Action("open_app", f"Открыть «{aliases[0]}»", {"target": target})
    return None


def make_open_url(url: str) -> Action | None:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    # только обычные сайты: никаких file://, javascript: и т.п.
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return Action("open_url", f"Открыть сайт {parsed.netloc}", {"url": url})


# Известные сайты: «открой ютуб» работает без настройки. Свои — в config.yaml → apps.
SITES = {
    "ютуб|ютьюб|youtube|ю туб": "https://www.youtube.com",
    "гугл|google|гугол": "https://www.google.com",
    "гитхаб|github|гит хаб": "https://github.com",
    "почту|почта|gmail|джимейл": "https://mail.google.com",
    "переводчик|гугл переводчик|translate|übersetzer": "https://translate.google.com",
    "карты|гугл карты|maps|karten": "https://maps.google.com",
    "вк|вконтакте|vk": "https://vk.com",
    "телеграм|телеграмм|telegram": "https://web.telegram.org",
    "твич|twitch": "https://www.twitch.tv",
    "нетфликс|netflix": "https://www.netflix.com",
    "спотифай|spotify": "https://open.spotify.com",
    "чатгпт|chatgpt|чат гпт": "https://chatgpt.com",
    "клод|claude": "https://claude.ai",
    "википедию|википедия|wikipedia": "https://ru.wikipedia.org",
    "амазон|amazon": "https://www.amazon.de",
    "linkedin|линкедин": "https://www.linkedin.com",
}


SITES = {
    "ютуб мюзик|youtube music|ютьюб мюзик|youtube мюзик": "https://music.youtube.com",
    # «открой понравившуюся музыку» — плейлист лайков в YouTube Music
    "понравившаяся музыка|понравившуюся музыку|понравившиеся|понравившейся музыка|любимая музыка|любимую музыку|"
    "мои лайки|лайкнутые|liked music|мою музыку": "https://music.youtube.com/playlist?list=LM",
    **SITES,
}


def make_open_site(name: str, extra: dict | None = None) -> Action | None:
    """Сайт по имени: сначала твои из config.yaml → sites, потом встроенные."""
    import difflib
    key = " ".join(re.sub(r"[-–,.!?]+", " ", name.lower()).split())
    for names, url in {**(extra or {}), **SITES}.items():
        # точное совпадение или почти (Whisper пишет «понравившейся музыка», «YouTube-мюзик»)
        if any(key == n or difflib.SequenceMatcher(None, key, n).ratio() >= 0.88 for n in names.split("|")):
            url = url if "://" in url else "https://" + url
            return Action("open_url", f"Открыть {url.split('//')[1].rstrip('/')}", {"url": url}, needs_confirm=False)
    return None


def make_start_app(app: dict) -> Action:
    """Программа из меню «Пуск» (найдена по названию)."""
    return Action("start_app", f"Открыть {app['name']}", {"id": app["id"], "name": app["name"]}, needs_confirm=False)


def make_youtube_search(query: str) -> Action:
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    return Action("web_search", f"Найти на YouTube: «{query}»", {"url": url}, needs_confirm=False)


def make_web_search(query: str, search_url: str) -> Action:
    url = search_url.format(query=quote_plus(query))
    return Action("web_search", f"Поискать в интернете: «{query}»", {"url": url}, needs_confirm=False)


def make_timer(minutes: float, label: str = "") -> Action | None:
    if not 0 < minutes <= 24 * 60:
        return None
    span = f"{round(minutes * 60)} сек" if minutes < 1 else f"{round(minutes, 1):g} мин"
    text = f"Таймер на {span}" + (f" — {label}" if label else "")
    return Action("timer", text, {"minutes": minutes, "label": label}, needs_confirm=False)


def make_note(text: str) -> Action:
    return Action("note", f"Записать заметку: «{text[:60]}»", {"text": text}, needs_confirm=False)


def _launch(target: str) -> None:
    """Запуск программы или ссылки без shell=True (никакой подстановки команд)."""
    if "://" in target:
        webbrowser.open(target)
    elif sys.platform == "win32":
        os.startfile(target)  # noqa: S606 — target только из белого списка config.yaml
    else:
        subprocess.Popen([target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def execute(action: Action, config: dict) -> str:
    """Выполнить действие (кроме timer — его запускает UI). Возвращает текст для чата."""
    if action.kind == "open_app":
        _launch(action.params["target"])
        return "Готово, открыла."
    if action.kind in ("mkdir", "mkfile", "trash", "open_folder"):
        from . import files
        return files.execute(action)
    if action.kind == "click_at":
        from . import control
        control.click(at=(action.params["x"], action.params["y"]))
        return "Нажала."
    if action.kind == "click_confirmed":
        from .commands import Command
        from .system import SystemControl
        r = SystemControl(expand("data"))._click_name(Command("click_name", text=action.params["name"]), confirmed=True)
        return f"Не вышло: {r['error']}" if r.get("error") else f"Нажала «{r.get('found')}»."
    if action.kind == "gcal_add":
        from .google_api import Google
        return Google(expand("data")).add_event(action.params["event"])
    if action.kind == "empty_bin":
        import ctypes
        import sys
        if sys.platform != "win32":
            return "Это только для Windows."
        # SHERB_NOCONFIRMATION | NOPROGRESSUI | NOSOUND — спросили уже мы сами
        r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
        return "Корзина пуста." if r in (0, -2147418113) else f"Не вышло (код {r})."
    if action.kind == "power":
        import subprocess
        import sys
        mode = action.params["mode"]
        if sys.platform != "win32":
            return "Это только для Windows."
        cmd = {"restart": ["shutdown", "/r", "/t", "10"], "shutdown": ["shutdown", "/s", "/t", "10"],
               "abort": ["shutdown", "/a"],
               "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]}[mode]
        subprocess.Popen(cmd, creationflags=0x08000000)
        return {"restart": "Перезагрузка через 10 с.", "shutdown": "Выключение через 10 с.", "abort": "Отменила.",
                "sleep": "Сон."}[mode]
    if action.kind == "open_file":
        import subprocess
        import sys
        path = action.params["path"]
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", path])   # Блокнот, а не запуск: .ps1/.py по двойному клику могут выполниться
        return f"Открыла {path}"
    if action.kind == "start_app":
        from .applist import launch
        launch(action.params["id"])
        return f"Запускаю {action.params['name']}."
    if action.kind in ("open_url", "web_search"):
        webbrowser.open(action.params["url"])
        return "Открыла в браузере."
    if action.kind == "note":
        path = expand(config["notes_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- {stamp} — {action.params['text']}\n")
        return f"Записала в {path}."
    raise ValueError(f"Неизвестное действие: {action.kind}")
