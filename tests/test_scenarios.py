"""Сценарии («игровой режим», «ночной режим», «режим работы») и напоминания об отдыхе."""
import copy
from pathlib import Path

import pytest
import yaml

from capri import commands as C
from capri import scenarios
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS


@pytest.mark.parametrize("text,hit", [
    ("Капри, игровой режим", ("игровой", True)), ("включи игровой режим", ("игровой", True)),
    ("запусти режим работы", ("работа", True)), ("рабочий режим.", ("работа", True)),
    ("ночной режим", ("ночной", True)), ("выключи ночной режим", ("ночной", False)),
    ("выключи режим работы", ("работа", False)), ("хватит работать", ("работа", False)),
    ("открой стим", None), ("какой у тебя режим", None),
])
def test_phrases(text, hit):
    assert scenarios.match(text, scenarios.DEFAULT) == hit


def test_default_file_is_created_and_editable(tmp_path, monkeypatch):
    monkeypatch.setattr(scenarios, "path", lambda: tmp_path / "scenarios.yaml")
    data = scenarios.load()
    assert set(data) == {"игровой", "ночной", "работа"} and (tmp_path / "scenarios.yaml").exists()
    own = {"кино": {"phrases": ["режим кино"], "steps": [{"volume": 60}]}}
    (tmp_path / "scenarios.yaml").write_text(yaml.safe_dump(own, allow_unicode=True), encoding="utf-8")
    assert scenarios.match("включи режим кино") == ("кино", True)


class Sys:
    def __init__(self):
        self.cmds = []

    def run(self, cmd):
        self.cmds.append((cmd.op, cmd.value))
        return {}


def test_runner_does_steps_in_order_and_survives_errors(monkeypatch):
    from capri import control
    placed, launched = [], []
    windows = {}
    monkeypatch.setattr(control, "find_app_window", lambda n, strict=False: windows.get(n, 0))

    def wait(n, timeout=20, ignore=None):
        windows[n] = hash(n) & 0xFFFF
        return windows[n]
    monkeypatch.setattr(control, "wait_window", wait)
    monkeypatch.setattr(control, "place_window", lambda h, screen, place: placed.append((h, screen, place)) or "окно")
    monkeypatch.setattr(control, "window_title", lambda h: f"win{h}")
    monkeypatch.setattr(control, "night_light", lambda on: True)
    monkeypatch.setattr(scenarios.time, "sleep", lambda s: None)
    monkeypatch.setattr(scenarios, "browser_exe", lambda: None)
    tabs = []
    monkeypatch.setattr(scenarios.webbrowser, "open_new_tab", tabs.append)
    sysc = Sys()
    known = {"discord": "D", "telegram": "T"}
    r = scenarios.Runner(lambda n: known.get(n), sysc, launched.append)
    done, failed, reminder, say = r.run([
        {"open": "discord", "screen": 2, "place": "left"}, {"open": "telegram", "screen": 2, "place": "right"},
        {"open": "нет-такой"}, {"nightlight": "on"}, {"volume": 20},
        {"browser": ["https://www.youtube.com", "https://www.netflix.com"]}, {"break_reminder": 180}, {"say": "Готово"}])
    assert launched == ["D", "T"] and [(p[1], p[2]) for p in placed] == [(2, "left"), (2, "right")]
    assert sysc.cmds == [("volume_set", 20.0)] and tabs == ["https://www.youtube.com", "https://www.netflix.com"]
    assert reminder == 180 and say == "Готово" and len(failed) == 1 and "нет-такой" in failed[0]
    assert "ночной свет вкл" in done


def test_agent_runs_scenario_and_sets_reminder(monkeypatch):
    seen = []
    monkeypatch.setattr(scenarios, "load", lambda: scenarios.DEFAULT)
    monkeypatch.setattr(scenarios.Runner, "run", lambda self, steps: seen.append(steps) or (["ok"], [], 180, "Режим работы."))
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))
    r = a.handle("Капри, режим работы")
    assert r.text.startswith("Режим работы") and r.actions[0].kind == "break_reminder" and r.actions[0].params == {"minutes": 180}
    assert seen[0][0] == {"open": "youtube music", "screen": 2, "place": "max"}


@pytest.mark.parametrize("text,minutes", [
    ("напоминай мне отдыхать каждые 2 часа", 120), ("напомни сделать перерыв через 45 минут", 45),
    ("не напоминай про отдых", 0), ("хватит напоминать про перерыв", 0),
])
def test_break_phrases(text, minutes):
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))
    r = a.handle(text)
    assert r.actions[0].kind == "break_reminder" and r.actions[0].params["minutes"] == minutes


def test_app_reminds_only_while_you_work(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import capri.app as appmod
    from capri import control
    from capri.applist import AppIndex as AI
    monkeypatch.setattr(appmod.CapriApp, "_init_voice", lambda self: None)
    monkeypatch.setattr(appmod.CapriApp, "start_dancing", lambda self: None)
    monkeypatch.setattr(AI, "refresh", lambda self: 0)
    app = appmod.CapriApp()
    app.warmup.wait(10_000)
    said = []
    monkeypatch.setattr(app, "timer_fired", lambda text, prefix="": said.append(text))
    app.set_break_reminder(180)
    idle = {"s": 0}
    monkeypatch.setattr(control, "idle_seconds", lambda: idle["s"])
    app._work_since -= 179 * 60
    app._break_check()
    assert not said
    app._work_since -= 2 * 60
    app._break_check()
    assert said                                       # три часа без перерыва — напомнила
    said.clear()
    app._work_since -= 200 * 60
    idle["s"] = 15 * 60                               # отошёл на 15 минут — это перерыв, отсчёт заново
    app._break_check()
    assert not said
    app.set_break_reminder(0)
    assert not app.prefs.get("break_every")


def test_night_light_phrase():
    assert (C.match("включи ночной свет").op, C.match("включи ночной свет").value) == ("nightlight", 1)
    assert C.match("выключи ночной свет").value == 0


def _fake_screens(monkeypatch, active, connected):
    from capri import control
    state = {"active": active}
    switched = []
    monkeypatch.setattr(control, "monitors", lambda: [(0, 0, 1920, 1080, True)] * state["active"])
    monkeypatch.setattr(control, "connected_displays", lambda: connected)

    def switch(mode, wait=6.0):
        switched.append(mode)
        state["active"] = 2 if mode == "extend" else 1
        return state["active"]
    monkeypatch.setattr(control, "switch_display", switch)
    for name in ("find_app_window", "wait_window"):
        monkeypatch.setattr(control, name, lambda n, *a, **k: 7)
    monkeypatch.setattr(control, "place_window", lambda h, screen, place: "окно")
    monkeypatch.setattr(control, "window_title", lambda h: "окно")
    return switched


def test_second_screen_is_switched_on_when_needed(monkeypatch):
    switched = _fake_screens(monkeypatch, active=1, connected=2)
    r = scenarios.Runner(lambda n: "A", Sys(), lambda a: None)
    r.run([{"open": "discord", "screen": 2, "place": "left"}])
    assert switched == ["extend"] and r.notes == ["включила второй экран"]


def test_only_one_screen_at_all(monkeypatch):
    switched = _fake_screens(monkeypatch, active=1, connected=1)
    r = scenarios.Runner(lambda n: "A", Sys(), lambda a: None)
    r.run([{"open": "discord", "screen": 2, "place": "left"}])
    assert switched == [] and "второго экрана нет" in r.notes[0]
    done, failed, *_ = r.run([{"display": "external"}])
    assert switched == [] and "не подключён" in failed[0]


def test_night_mode_keeps_only_second_screen(monkeypatch):
    switched = _fake_screens(monkeypatch, active=2, connected=2)
    from capri import control
    monkeypatch.setattr(control, "night_light", lambda on: True)
    monkeypatch.setattr(scenarios, "browser_exe", lambda: None)
    monkeypatch.setattr(scenarios.webbrowser, "open_new_tab", lambda u: None)
    r = scenarios.Runner(lambda n: "A", Sys(), lambda a: None)
    r.run(scenarios.DEFAULT["ночной"]["steps"])
    r.run(scenarios.DEFAULT["ночной"]["off_steps"])
    assert switched == ["external", "extend"]


def test_old_example_file_is_upgraded(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(scenarios, "path", lambda: tmp_path / "scenarios.yaml")
    old = subprocess.run(["git", "show", "891624c:capri/scenarios.py"], capture_output=True, text=True).stdout
    if not old:
        pytest.skip("нет истории git")
    ns = {}
    exec(old.split("DEFAULT = yaml")[0], ns)
    (tmp_path / "scenarios.yaml").write_text(ns["DEFAULT_TEXT"], encoding="utf-8")
    assert scenarios.load()["ночной"]["steps"][0] == {"display": "external"}
    (tmp_path / "scenarios.yaml").write_text("моё:\n  phrases: [мой режим]\n  steps: []\n", encoding="utf-8")
    assert list(scenarios.load()) == ["моё"]          # свой файл не трогаем


@pytest.mark.parametrize("text,mode", [
    ("выключи основной экран", "external"), ("отключи главный монитор", "external"),
    ("выключи второй экран", "internal"), ("погаси вспомогательный монитор", "internal"),
    ("включи второй экран", "extend"), ("верни оба экрана", "extend"),
])
def test_display_phrases(text, mode):
    c = C.match(text)
    assert (c.op, c.text) == ("display", mode)
