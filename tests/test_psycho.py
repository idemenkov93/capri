"""Характер «Киберпсих»: включается голосом, отвечает бесплатная модель (своя или Gemini/Claude)."""
import copy
from pathlib import Path

from capri import persona
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.llm import claude_code


class Sys:
    def run(self, cmd):
        return {}


class Local:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools=None, on_token=None, should_stop=None, **k):
        self.calls.append(messages)
        return {"content": "локально", "tool_calls": []}


def agent(local=None):
    cfg = copy.deepcopy(DEFAULTS)
    a = Agent(cfg, llm=local or Local(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")),
              gemini_ask=lambda *x, **k: "G")
    return a


def test_voice_command_switches_attitude():
    a = agent()
    for phrase, code in (("включи киберпсиха", "psycho"), ("режим киберпсих", "psycho"), ("режим дерзкая", "sassy"),
                         ("характер мягкая", "soft"), ("режим грубая", "rude")):
        r = a.handle(phrase)
        assert r.actions and r.actions[0].kind == "set_attitude" and r.actions[0].params["code"] == code, phrase


def test_psycho_uses_local_model_with_psycho_prompt():
    local = Local()
    a = agent(local)
    a.cfg["persona"] = {"attitude": "psycho", "slang": True}
    assert a.handle("привет").text == "локально"
    assert "КИБЕРПСИХ" in local.calls[0][0]["content"]


def test_prompts():
    p = persona.system_prompt("Игорь", "psycho")
    assert "МАТ РАЗРЕШЁН" in p and "выходишь из образа" in p
    assert "КИБЕРПСИХ" not in persona.system_prompt("Игорь", "rude")


def test_claude_gets_psycho_style():
    assert "Киберпсих" in claude_code.PSYCHO
