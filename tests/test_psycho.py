"""Характер «Киберпсих»: голосом включается, болтает через Grok, без ключа — локальная модель."""
import copy
import json
from pathlib import Path

from capri import persona
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.llm import claude_code, grok


class Sys:
    def run(self, cmd):
        return {}


class Local:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools=None, on_token=None, should_stop=None, **k):
        self.calls.append(messages)
        return {"content": "локально", "tool_calls": []}


class FakeGrok:
    model = "grok-test"

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def chat(self, messages, tools="default", on_token=None, should_stop=None):
        self.calls.append((messages, tools))
        if self.fail:
            raise grok.GrokError("на счету xAI нет денег")
        return {"content": "Чё надо, кожаный?", "tool_calls": []}


def agent(local=None):
    cfg = copy.deepcopy(DEFAULTS)
    a = Agent(cfg, llm=local or Local(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")),
              gemini_ask=lambda *x, **k: "G")
    return a


def test_voice_command_switches_attitude(monkeypatch):
    monkeypatch.setattr(grok, "get_key", lambda cfg=None: None)
    a = agent()
    for phrase, code in (("включи киберпсиха", "psycho"), ("режим киберпсих", "psycho"), ("режим дерзкая", "sassy"),
                         ("характер мягкая", "soft"), ("режим грубая", "rude")):
        r = a.handle(phrase)
        assert r.actions and r.actions[0].kind == "set_attitude" and r.actions[0].params["code"] == code, phrase
    assert "grok" in a.handle("включи киберпсиха").text.lower()          # без ключа подсказывает


def test_psycho_chats_through_grok_with_tools():
    local, g = Local(), FakeGrok()
    a = agent(local)
    a.cfg["persona"] = {"attitude": "psycho", "slang": True}
    a._grok_client = g
    r = a.handle("привет")
    assert r.text == "Чё надо, кожаный?" and not local.calls
    msgs, tools = g.calls[0]
    assert "КИБЕРПСИХ" in msgs[0]["content"] and tools == "default"        # инструменты те же, что у локальной


def test_grok_failure_falls_back_to_local():
    local = Local()
    a = agent(local)
    a.cfg["persona"] = {"attitude": "psycho", "slang": True}
    a._grok_client = FakeGrok(fail=True)
    assert a.handle("привет").text == "локально" and local.calls


def test_other_attitudes_do_not_use_grok():
    local, g = Local(), FakeGrok()
    a = agent(local)
    a._grok_client = g
    a.handle("привет")
    assert local.calls and not g.calls


def test_prompts():
    p = persona.system_prompt("Игорь", "psycho")
    assert "МАТ РАЗРЕШЁН" in p and "выходишь из образа" in p
    assert "КИБЕРПСИХ" not in persona.system_prompt("Игорь", "rude")


def test_key_regex_and_model_pick():
    from capri.agent import RE_GROK_KEY
    assert RE_GROK_KEY.search("ключ grok xai-" + "a1" * 20).group("key").startswith("xai-")
    assert grok.pick_model(["grok-3-mini", "grok-4-fast-non-reasoning", "grok-4-1-fast-non-reasoning",
                            "grok-4-1-fast-reasoning", "grok-imagine-image"]) == "grok-4-1-fast-non-reasoning"


def test_stream_parsing(monkeypatch):
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "Открываю, "}}]}),
        'data: ' + json.dumps({"choices": [{"delta": {"content": "сука."}}]}),
        'data: ' + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "open_app", "arguments": '{"na'}}]}}]}),
        'data: ' + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'me": "Discord"}'}}]}}]}),
        "data: [DONE]",
    ]

    class Resp:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_lines(self):
            return iter(lines)

    seen = {}

    def fake_stream(method, url, json=None, **k):
        seen.update(json)
        return Resp()
    monkeypatch.setattr(grok.httpx, "stream", fake_stream)
    msg = grok.GrokClient("xai-x", "m").chat([{"role": "system", "content": "ты Капри /no_think"},
                                              {"role": "user", "content": "открой дискорд"}])
    assert msg["content"] == "Открываю, сука."
    assert msg["tool_calls"] == [{"function": {"name": "open_app", "arguments": '{"name": "Discord"}'}}]
    assert "/no_think" not in seen["messages"][0]["content"] and seen["tools"]


def test_claude_gets_psycho_style():
    assert "Киберпсих" in claude_code.PSYCHO
