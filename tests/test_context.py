"""Контекст: какой плеер ставить на паузу, ссылки на странице, «посмотри и нажми», перенос окон между мониторами."""
import copy
from pathlib import Path

import pytest

from capri import commands as C
from capri import control, screen
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS


class NoLLM:
    def chat(self, *a, **k):
        raise AssertionError("должно решаться правилами, без модели")


def agent(system=None, claude=lambda t, c: ""):
    return Agent(copy.deepcopy(DEFAULTS), llm=NoLLM(), claude=claude, system=system, apps=AppIndex(Path("/x")))


# ------------------------------------------------------------------ музыка: какой плеер
@pytest.mark.parametrize("text,op,target", [
    ("нажми плей ютуб мюзик", "play", "music"), ("пауза в ютуб мюзик", "pause", "music"),
    ("нажми на паузу в ютубе", "pause", "browser"), ("нажми паузу", "pause", None),
    ("включи музыку в спотифай", "play", "spotify"), ("следующий трек в ютуб мюзик", "next", "music"),
])
def test_media_target(text, op, target):
    cmd = C.match(text)
    assert (cmd.op, cmd.text) == (op, target)


# ------------------------------------------------------------------ мониторы
@pytest.mark.parametrize("text,what,where", [
    ("перетащи хром на второй экран", "хром", "2"), ("перенеси хром на первый экран", "хром", "1"),
    ("перетащи окно на другой монитор", None, "next"), ("перемести телеграм на левый монитор", "телеграм", "left"),
    ("move chrome to the second screen", "chrome", "2"), ("кинь его на 2 монитор", None, "2"),
])
def test_move_to_monitor_phrases(text, what, where):
    cmd = C.match(text)
    assert (cmd.op, cmd.text, cmd.keys) == ("move_monitor", what, [where])


def test_move_monitor_reply():
    cmd = C.Command("move_monitor", text="хром", keys=["2"])
    assert "монитор 2" in C.reply(cmd, "ru", {"title": "Google Chrome", "monitor": 2})
    assert "и так" in C.reply(cmd, "ru", {"title": "Google Chrome", "monitor": 2, "same": True})


# ------------------------------------------------------------------ названия ссылок
@pytest.mark.parametrize("want,name,ok", [
    ("подземелье чикен карри 54 выпуск", "Подземелья Чикен Карри #54 Свиток Апокалипсиса", True),
    ("подземелье чикен карри 54 выпуск", "Подземелья Чикен Карри #53 Ночь в музее", False),
    ("мама в 16", "Мама в 16 | 3 сезон, 5 выпуск", True),
])
def test_link_names(want, name, ok):
    assert (control.name_score(want, name) >= 0.8) == ok


def test_ordinals():
    assert [control.ordinal(w) for w in ("вторую", "третье", "последнюю", "2-ю", "первый", "second")] == [2, 3, -1, 2, 1, 2]


def test_nth_link_goes_before_click_by_name(monkeypatch):
    seen = []
    a = agent(system=None)
    monkeypatch.setattr(a, "_open_nth", lambda n: seen.append(n) or __import__("capri.agent").agent.Reply("ok", route="rules"))
    for t in ("открой вторую ссылку в хром", "открой первую ссылку", "включи третье видео", "нажми на последний результат"):
        a.handle(t)
    assert seen == ["вторую", "первую", "третье", "последний"]


def test_unknown_video_goes_to_youtube(monkeypatch):
    monkeypatch.setattr(control, "context_window", lambda: ("", ""))
    r = agent().handle("открой подземелье чикен карри 54 выпуск")
    assert r.actions and r.actions[0].kind == "web_search" and "youtube" in r.actions[0].params["url"]


# ------------------------------------------------------------------ «посмотри и нажми» через Claude
def test_parse_point():
    assert screen.parse_point('Вот: {"x": 812, "y": 430}') == (812, 430)
    assert screen.parse_point('{"none": true}') is None
    assert screen.parse_point("не вижу") is None
    assert screen.to_screen((800, 450), 0.5, -1920, 0) == (-320, 900)


def test_vision_fallback_when_button_not_in_window(monkeypatch, tmp_path):
    class Sys:
        def run(self, cmd):
            return {"error": "не нашла в окне кнопку «скип»"}

    clicks = []
    monkeypatch.setattr(screen, "screenshot_for_locate", lambda wd: ("screens/x.png", 0.5, 0, 0, 1600, 450))
    monkeypatch.setattr(control, "click", lambda button="left", double=False, at=None: clicks.append(at))
    asked = []
    a = agent(system=Sys(), claude=lambda t, c: asked.append(t) or '{"x": 100, "y": 50}')
    r = a.handle("нажми кнопку скип")
    assert asked and "скип" in asked[0] and clicks == [(200, 100)] and r.route == "claude"


def test_vision_risky_asks_first(monkeypatch):
    class Sys:
        def run(self, cmd):
            return {"error": "не нашла в окне кнопку «удалить»"}

    monkeypatch.setattr(screen, "screenshot_for_locate", lambda wd: ("screens/x.png", 1.0, 0, 0, 1600, 900))
    monkeypatch.setattr(control, "click", lambda **k: pytest.fail("рискованное — только после «Да»"))
    r = agent(system=Sys(), claude=lambda t, c: '{"x": 10, "y": 20}').handle("нажми кнопку удалить")
    assert r.actions[0].kind == "click_at" and r.actions[0].needs_confirm and r.actions[0].params == {"x": 10, "y": 20}
