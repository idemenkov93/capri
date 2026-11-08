"""🔕 Не беспокоить и учебный таймер."""
import copy
import datetime as dt
from pathlib import Path

from capri import dnd
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS


class Sys:
    def run(self, cmd):
        return {}


def agent():
    return Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))


def test_until():
    now = dt.datetime(2026, 11, 9, 14, 0)
    assert dt.datetime.fromtimestamp(dnd.until(30, now)) == dt.datetime(2026, 11, 9, 14, 30)
    assert dt.datetime.fromtimestamp(dnd.until("evening", now)) == dt.datetime(2026, 11, 9, 18, 0)
    late = dt.datetime(2026, 11, 9, 21, 0)
    assert dt.datetime.fromtimestamp(dnd.until("evening", late)) == dt.datetime(2026, 11, 10, 8, 0)   # вечер прошёл → до утра
    assert dt.datetime.fromtimestamp(dnd.until("morning", now)) == dt.datetime(2026, 11, 10, 8, 0)
    assert dnd.until("forever") == dnd.FOREVER and dnd.active(dnd.FOREVER) and not dnd.active(0)
    assert dnd.active(now.timestamp() + 60, now.timestamp()) and not dnd.active(now.timestamp() - 1, now.timestamp())


def test_voice_commands():
    a = agent()
    for phrase, choice in (("не беспокой меня час", 60), ("не беспокой до вечера", "evening"),
                           ("режим не беспокоить", 60), ("не мешай 30 минут", 30), ("не беспокой меня до утра", "morning")):
        r = a.handle(phrase)
        assert r.actions[0].kind == "dnd" and r.actions[0].params["choice"] == choice, phrase
    assert a.handle("можно беспокоить").actions[0].params["choice"] == 0
    r = a.handle("начни учёбу на 45 минут")
    assert r.actions[0].kind == "study" and r.actions[0].params["minutes"] == 45
    assert a.handle("помодоро").actions[0].params["minutes"] == 45
    assert a.handle("не мешай мне в играх").actions[0].kind == "game_set"     # другое — игровой помощник


def test_study_answer_goes_to_notes():
    import time
    a = agent()
    a.pending = {"kind": "study_note", "at": time.monotonic(), "minutes": "45", "ttl": 600}
    r = a.handle("разобрался с DHCP и GPO")
    assert r.actions[0].kind == "note" and "DHCP и GPO" in r.actions[0].params["text"] and "45" in r.actions[0].params["text"]
