"""Gemini: в играх вместо Claude (без окон и процессов), когда кончился лимит Claude, и подключение ключа из чата."""
import copy
from pathlib import Path

from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.llm import claude_code, gemini


class Sys:
    def run(self, cmd):
        return {}


class Game:
    active, strict = True, False

    def busy_round(self):
        return False

    def context(self):
        return "Пользователь сейчас играет в StarRupture."


def agent(claude, gem, game=None):
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=claude, system=Sys(), apps=AppIndex(Path("/x")), gemini_ask=gem)
    a.game = game
    return a


def test_games_use_gemini_first():
    asked = []
    a = agent(lambda t, c: asked.append("claude") or "C", lambda t, cfg, images=None, system="": asked.append("gemini") or "G",
              Game())
    assert a.handle("как построить базу быстрее?").text == "G" and asked == ["gemini"]


def test_claude_limit_switches_to_gemini_and_remembers():
    calls = []

    def claude(t, c):
        calls.append("claude")
        raise claude_code.ClaudeLimit("You've hit your limit")
    a = agent(claude, lambda t, cfg, images=None, system="": calls.append("gemini") or "Ответ Gemini")
    r = a.handle("объясни подробно, что такое DNS")
    assert r.text.startswith("Лимит Claude кончился — ответил Gemini.") and calls == ["claude", "gemini"]
    a.handle("объясни подробно, что такое VLAN")
    assert calls == ["claude", "gemini", "gemini"]          # полчаса Claude не дёргаем


def test_limit_without_gemini_says_how_to_connect(monkeypatch):
    monkeypatch.setattr(gemini, "get_key", lambda cfg=None: None)

    def claude(t, c):
        raise claude_code.ClaudeLimit("лимит")
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=claude, system=Sys(), apps=AppIndex(Path("/x")))
    assert "как подключить gemini" in a.handle("объясни подробно, что такое DNS").text


def test_limit_answer_text_is_detected():
    assert claude_code.RE_LIMIT_ANSWER.search("You've hit your limit · resets 9pm (Europe/Berlin)")
    assert not claude_code.RE_LIMIT_ANSWER.search("DNS — это система доменных имён.")


def test_key_from_chat(monkeypatch):
    saved = []
    monkeypatch.setattr(gemini, "check_key", lambda key, tier: "gemini-flash-latest")
    monkeypatch.setattr(gemini, "set_key", saved.append)
    a = agent(lambda t, c: "", None)
    r = a.handle("ключ gemini AIzaSyA1234567890abcdefghijklmnopqrstu")
    assert "Gemini подключён" in r.text and saved == ["AIzaSyA1234567890abcdefghijklmnopqrstu"]
    r = a.handle("как подключить gemini")
    assert "aistudio.google.com/apikey" in r.text and r.actions[0].kind == "open_url"


def test_chat_with_friends_in_game_is_ignored():
    asked = []
    a = agent(lambda t, c: asked.append(t) or "C", lambda t, cfg, images=None, system="": asked.append(t) or "G", Game())
    for chatter in ("Капец, уронка.", "Я не буду видеть фильмы, конечно.", "какая-то", "Ah", "если он будет столбой"):
        assert a.handle(chatter).route == "ignored", chatter
    assert not asked
    assert a.handle("что ты знаешь про эту игру?").text == "G"
    assert a.handle("громкость 30").route == "rules"             # команды — как обычно


def test_new_aq_keys_are_accepted(monkeypatch):
    saved = []
    monkeypatch.setattr(gemini, "check_key", lambda key, tier: "gemini-flash-latest")
    monkeypatch.setattr(gemini, "set_key", saved.append)
    a = agent(lambda t, c: "", None)
    r = a.handle("AQ.Ab8RN6IKTQEnnNn_XBx14MO9TEtT7L9UwYygotOXabcdEzA")
    assert "Gemini подключён" in r.text and saved[0].startswith("AQ.Ab8RN6")
