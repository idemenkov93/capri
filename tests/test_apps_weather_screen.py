import copy
from pathlib import Path

import pytest

from capri import screen, weather
from capri.agent import Agent
from capri.applist import AppIndex, score
from capri.config import DEFAULTS


class LLM:
    calls = 0

    def chat(self, *a, **k):
        LLM.calls += 1
        return {"content": "LLM"}


def apps(*names):
    ix = AppIndex(Path("/nonexistent/apps.json"))
    ix.apps = [{"name": n, "id": "id:" + n} for n in names]
    return ix


def agent(ix=None, claude=lambda t, c: "CLAUDE"):
    return Agent(copy.deepcopy(DEFAULTS), llm=LLM(), claude=claude, apps=ix or apps())


@pytest.mark.parametrize("spoken,name", [
    ("ютуб мюзик", "YouTube Music"), ("дискорд", "Discord"), ("пакет трейсер", "Cisco Packet Tracer"),
    ("телеграм", "Telegram Desktop"), ("спотифай", "Spotify - Music and Podcasts"), ("вс код", "Visual Studio Code"),
    ("павершелл", "Windows PowerShell"), ("диспетчер задач", "Task Manager"),
])
def test_spoken_names_match(spoken, name):
    ix = apps("YouTube Music", "Discord", "Cisco Packet Tracer", "Telegram Desktop", "Spotify - Music and Podcasts",
              "Visual Studio Code", "Windows PowerShell", "Task Manager", "Steam")
    assert ix.find(spoken)["name"] == name


def test_app_first_then_site():
    a = agent(apps("YouTube Music", "Telegram Desktop"))
    assert a.handle("открой ютуб мюзик").actions[0].kind == "start_app"      # есть приложение
    assert a.handle("открой ютуб").actions[0].params["url"] == "https://www.youtube.com"  # YouTube Music ≠ YouTube
    assert a.handle("открой телеграм").actions[0].kind == "start_app"
    b = agent(apps())
    assert b.handle("открой ютуб мюзик").actions[0].params["url"] == "https://music.youtube.com"  # нет — браузер


def test_custom_sites_from_config():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["sites"] = {"школа|zukunftsmotor": "zukunftsmotor.de"}
    a = Agent(cfg, llm=LLM(), claude=lambda t, c: "", apps=apps())
    assert a.handle("открой школа").actions[0].params["url"] == "https://zukunftsmotor.de"


def test_unrelated_names_dont_match():
    assert score("фотошоп", "Photos") < 0.82
    assert apps("Steam", "Discord").find("regedit") is None


def test_weather_answer_without_browser():
    class Resp:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    class Client:
        def get(self, url, params):
            if "geocoding" in url:
                return Resp({"results": [{"name": "Мюнхен", "latitude": 48.1, "longitude": 11.6}]})
            return Resp({"current": {"temperature_2m": 7.4, "apparent_temperature": 4.1, "weather_code": 3},
                         "daily": {"temperature_2m_max": [9.6, 11, 8], "temperature_2m_min": [3.2, 4, 2],
                                   "precipitation_probability_max": [20, 70, 10], "weather_code": [3, 61, 0]}})
    s = weather.forecast("какая погода", "München", "ru", client=Client())
    assert s.startswith("В городе Мюнхен сейчас 7°") and "20%" in s
    assert "Зонт" in weather.forecast("погода завтра", "München", "ru", client=Client())


def test_screen_question_goes_to_claude(monkeypatch):
    seen = {}
    monkeypatch.setattr(screen, "capture", lambda workdir: (["screens/window-1.png", "screens/screen-1.png"], "Windows PowerShell"))

    def claude(task, cfg):
        seen["task"] = task
        return "Это ошибка доступа."
    a = agent(claude=claude)
    r = a.handle("Капри, посмотри что у меня за ошибка в павершел сейчас")
    assert r.route == "claude" and r.text == "Это ошибка доступа."
    assert "screens/window-1.png" in seen["task"] and "Windows PowerShell" in seen["task"]
    assert a.history[-2]["content"].startswith("посмотри")   # в истории — твой вопрос, а не служебный промпт


@pytest.mark.parametrize("text", ["напиши скрипт на PowerShell для бэкапа", "почему не работает DHCP", "объясни подробно, что такое VLAN"])
def test_complex_goes_to_claude(text):
    LLM.calls = 0
    r = agent().handle(text)
    assert r.route == "claude" and LLM.calls == 0


def test_simple_goes_to_ollama():
    LLM.calls = 0
    r = agent().handle("как дела?")
    assert r.route == "local" and LLM.calls == 1


def test_liked_music_and_open_in_phrasing():
    a = agent(apps("YouTube Music"))
    for t in ["Открой понравившейся музыка.", "Открой в YouTube-мюзик, понравившаяся музыка."]:
        assert a.handle(t).actions[0].params["url"] == "https://music.youtube.com/playlist?list=LM"
    assert a.handle("Открой YouTube-мюзик.").actions[0].kind == "start_app"


@pytest.mark.parametrize("spoken,name", [("просмотр событий", "Event Viewer"), ("реестр", "Registry Editor"),
                                         ("службы", "Services"), ("ведьмак", "The Witcher 3 Wild Hunt - Complete Edition")])
def test_admin_tools_in_russian(spoken, name):
    ix = apps("Event Viewer", "Registry Editor", "Services", "The Witcher 3 Wild Hunt - Complete Edition", "Settings")
    assert ix.find(spoken)["name"] == name
