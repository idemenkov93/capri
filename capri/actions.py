"""Действия, которые Капри может выполнить. Каждое действие — из белого списка."""
from __future__ import annotations

import datetime as dt
import os
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
    for app_name, target in apps.items():
        if app_name.lower() == key:
            return Action("open_app", f"Открыть «{app_name}»", {"target": target})
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
