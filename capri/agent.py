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
RE_OPEN = re.compile(r"^(?:открой|запусти|включи|öffne|starte|open|launch|start)\s+(.+?)[.!]?$", re.I)
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
RE_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")

UNIT_TO_MIN = {
    "сек": 1 / 60, "с": 1 / 60, "sek": 1 / 60, "sec": 1 / 60, "s": 1 / 60,
    "мин": 1, "м": 1, "min": 1, "m": 1,
    "час": 60, "ч": 60, "stunde": 60, "std": 60, "h": 60, "hour": 60,
}


class Agent:
    def __init__(self, config: dict, llm: OllamaClient | None = None,
                 claude: Callable[[str, dict], str] = claude_code.ask, system=None):
        self.cfg = config
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
        text = text.strip()
        if not text:
            return Reply("Ну? Я слушаю.", route="rules")
        reply = self._rules(text)
        if reply:
            return reply
        if m := RE_CLAUDE.match(text):
            return self._ask_claude(m.group(1), on_status)
        return self._local(text, on_status, on_token, should_stop)

    # быстрые правила --------------------------------------------------------
    def _rules(self, text: str) -> Reply | None:
        # системные команды (музыка, громкость, клавиши, проверка ПК…) — выполняем прямо здесь, в рабочем потоке
        if cmd := C.match(text):
            result = self.system.run(cmd)
            return Reply(C.reply(cmd, C.lang_of(text), result), route="error" if result.get("error") else "rules")
        if m := RE_SEARCH.match(text):
            q = m.group(1).strip()
            return Reply(f"Ищу «{q}».", [A.make_web_search(q, self.cfg["search_url"])], "rules")
        if m := RE_TIMER.search(text):
            minutes = float(m.group(1).replace(",", ".")) * UNIT_TO_MIN[m.group(2).lower()]
            if act := A.make_timer(minutes, (m.group(3) or "").strip()):
                return Reply(f"Окей, {act.description.lower()}.", [act], "rules")
        if m := RE_NOTE.match(text):
            return Reply("Записываю.", [A.make_note(m.group(1).strip())], "rules")
        if m := RE_OPEN.match(text):
            target = m.group(1).strip()
            if act := A.make_open_app(target, self.cfg["apps"]):
                return Reply(f"{act.description}.", [act], "rules")
            if RE_DOMAIN.match(target) and (act := A.make_open_url(target)):
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

        content = (msg.get("content") or "").strip()
        if not content:
            content = " ".join(a.description + "." for a in acts) or "Хм, не поняла. Переформулируешь?"
        if notes:
            content += "\n" + "\n".join(notes)
        self._remember("assistant", content)
        return Reply(content, acts, "local")

    def _tool_to_action(self, name: str, args: dict) -> A.Action | None:
        try:
            if name == "open_app":
                return A.make_open_app(str(args.get("name", "")), self.cfg["apps"])
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
    def _ask_claude(self, task: str, on_status, remembered: bool = False) -> Reply:
        if not self.cfg["claude_code"].get("enabled", True):
            return Reply("Claude выключен в config.yaml.", route="error")
        on_status("CLAUDE")
        if not remembered:
            self._remember("user", task)
        try:
            answer = self.claude(task, self.cfg["claude_code"])
        except claude_code.ClaudeError as e:
            return Reply(str(e), route="error")
        self._remember("assistant", answer)
        return Reply(answer, route="claude")

    def _remember(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        del self.history[:-HISTORY_LIMIT]
