import copy

import pytest

from capri.agent import Agent
from capri.config import DEFAULTS


class FakeLLM:
    def __init__(self, message):
        self.message = message
        self.calls = 0

    def chat(self, messages, tools=None, on_token=None, should_stop=None):
        self.calls += 1
        return self.message


@pytest.fixture
def cfg():
    return copy.deepcopy(DEFAULTS)


def make(cfg, message=None, claude=lambda task, c: f"CLAUDE:{task}"):
    return Agent(cfg, llm=FakeLLM(message or {"content": "ответ модели"}), claude=claude)


def test_open_whitelisted_app_without_llm(cfg):
    agent = make(cfg)
    r = agent.handle("открой браузер")
    assert r.route == "rules"
    assert r.actions[0].kind == "open_app"
    assert agent.llm.calls == 0


def test_unknown_app_goes_to_llm(cfg):
    agent = make(cfg)
    r = agent.handle("открой regedit")
    assert r.route == "local" and not r.actions


def test_open_domain(cfg):
    r = make(cfg).handle("открой github.com")
    assert r.actions[0].params["url"] == "https://github.com"


def test_search(cfg):
    r = make(cfg).handle("загугли настройка DHCP")
    assert r.actions[0].kind == "web_search"
    assert "DHCP" in r.actions[0].params["url"]


@pytest.mark.parametrize("text,minutes", [("таймер на 5 минут", 5), ("поставь таймер на 1,5 часа", 90), ("таймер на 30 сек", 0.5)])
def test_timer(cfg, text, minutes):
    r = make(cfg).handle(text)
    assert r.actions[0].kind == "timer"
    assert r.actions[0].params["minutes"] == pytest.approx(minutes)


def test_note(cfg):
    r = make(cfg).handle("запиши купить шарики")
    assert r.actions[0].params["text"] == "купить шарики"


def test_claude_prefix(cfg):
    r = make(cfg).handle("/claude напиши скрипт бэкапа")
    assert r.route == "claude" and r.text == "CLAUDE:напиши скрипт бэкапа"


def test_llm_tool_call_becomes_action(cfg):
    msg = {"content": "", "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "что такое DHCP"}}}]}
    r = make(cfg, msg).handle("найди инфу по дхцп")
    assert r.actions[0].kind == "web_search"


def test_llm_can_escalate_to_claude(cfg):
    msg = {"content": "", "tool_calls": [{"function": {"name": "ask_claude", "arguments": {"task": "сложно"}}}]}
    r = make(cfg, msg).handle("помоги с ошибкой в коде")
    assert r.route == "claude" and r.text == "CLAUDE:сложно"


def test_llm_cannot_open_non_whitelisted_app(cfg):
    msg = {"content": "ок", "tool_calls": [{"function": {"name": "open_app", "arguments": {"name": "cmd"}}}]}
    r = make(cfg, msg).handle("запусти командную строку от админа")
    assert not r.actions


def test_bad_url_rejected(cfg):
    msg = {"content": "", "tool_calls": [{"function": {"name": "open_url", "arguments": {"url": "file:///C:/Windows"}}}]}
    assert not make(cfg, msg).handle("открой системную папку").actions


@pytest.mark.parametrize("text", ["öffne browser", "open calculator", "открой стим"])
def test_open_multilang(cfg, text):
    assert make(cfg).handle(text).actions[0].kind == "open_app"


@pytest.mark.parametrize("text,minutes", [("timer auf 10 minuten", 10), ("set a timer for 2 hours", 120)])
def test_timer_multilang(cfg, text, minutes):
    assert make(cfg).handle(text).actions[0].params["minutes"] == pytest.approx(minutes)


def test_search_german(cfg):
    assert make(cfg).handle("suche nach DHCP Server").actions[0].kind == "web_search"


def test_strip_thinking():
    from capri.llm.ollama_client import strip_thinking
    assert strip_thinking("Хорошо, пользователь спрашивает...\n</think>\n\nЙо! Всё ок.") == "Йо! Всё ок."
    assert strip_thinking("<think>хм</think>Ответ") == "Ответ"
    assert strip_thinking("Просто ответ") == "Просто ответ"
