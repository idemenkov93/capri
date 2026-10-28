"""Фразы из настоящего журнала Капри (как их записал Whisper), на которых она раньше ошибалась."""
import copy
from pathlib import Path

import pytest

from capri import commands as C
from capri import files, screen
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.google_api import RE_MAIL


@pytest.mark.parametrize("text,op,target", [
    ("Стоп!", "pause", None), ("Паута.", "pause", None), ("Дай мне пауза.", "pause", None),
    ("Пауза, YouTube!", "pause", None), ("Пауза YouTube визю", "pause", None),
    ("музыка.", "play", None), ("music.", "play", None), ("произведи музыку.", "play", None),
    ("а стане вея музыку", "play", None), ("Let me play.", "play", None), ("воспроизвести", "play", None),
    ("и плей и тук МЕЗИК", "play", None), ("У вас произведи YouTube Мерзик.", "play", "music"),
    ("капри, YouTube-мизик открыт, просто нажми Play.", "play", "music"), ("YouTube music play", "play", "music"),
    ("Потанцуй!", "play", None), ("включи мой плейлист", "play", None),
    ("Следующий траг.", "next", None), ("Следующий тракт.", "next", None), ("Следующий трон.", "next", None),
    ("Следующий трек Она шла спустыша Капри, следующий трек", "next", None),
    ("Сделай тишину!", "mute", None), ("Shut up", "quiet", None), ("замолчи", "quiet", None),
    ("Прокрути чуть ниже и тут ниже", "scroll", None),
    ("YouTube на весь экран.", "fullscreen", None), ("YouTube во весь экран.", "fullscreen", None),
    ("ночной свет", "nightlight", None), ("выключи ночной свет", "nightlight", None),
    ("оставь только второй экран", "display", "external"), ("Да, убери первый экран.", "display", "external"),
    ("верни оба экрана", "display", "extend"), ("Оставь второй экран.", "display", "external"),
    ("Проверим мой компьютер.", "pc_check", None), ("Проверите пока.", "pc_check", None), ("Проверь пока.", "pc_check", None),
])
def test_misheard_and_loose_phrases(text, op, target):
    cmd = C.match(text)
    assert cmd is not None and cmd.op == op, (text, cmd)
    if target is not None:
        assert cmd.text == target


@pytest.mark.parametrize("text,keys", [
    ("Открой первую вкладку.", ["ctrl", "1"]), ("Открой первую тап", ["ctrl", "1"]),
    ("перейди на третью вкладку", ["ctrl", "3"]), ("открой последнюю вкладку", ["ctrl", "9"]),
    ("следующая вкладка", ["ctrl", "tab"]), ("закрой вкладку", ["ctrl", "w"]), ("новая вкладка", ["ctrl", "t"]),
])
def test_browser_tabs(text, keys):
    cmd = C.match(text)
    assert (cmd.op, cmd.keys) == ("keys", keys)


@pytest.mark.parametrize("text,action,name", [
    ("Разверни хром.", "maximize", "хром"), ("Закрой YouTube", "close", "youtube"),
    ("Закрой YouTube-мьезик.", "close", "youtube music"), ("сверни телеграм", "minimize", "телеграм"),
])
def test_window_by_app_name(text, action, name):
    cmd = C.match(text)
    assert (cmd.op, cmd.text, cmd.keys) == ("window", action, [name])
    assert C.match("закрой окно").keys is None and C.match("закрой вкладку").op == "keys"


@pytest.mark.parametrize("text,what,where", [
    ("Перетащу хром на второй экран.", "хром", "2"), ("Google Chrome на правой экран.", "google chrome", "right"),
    ("перенеси YouTube Music на правый экран.", "youtube music", "right"), ("кинь хром на главный монитор", "хром", "1"),
])
def test_move_window_variants(text, what, where):
    cmd = C.match(text)
    assert (cmd.op, cmd.text, cmd.keys) == ("move_monitor", what, [where])


def test_type_into_app():
    c = C.match("Напиши в PowerShell гид-статус.")
    assert (c.op, c.text, c.keys) == ("type_in", "git status\n", ["powershell"])
    c = C.match("Открой PowerShell и введи Get-Process")
    assert (c.op, c.text, c.keys) == ("type_in", "Get-Process\n", ["powershell"])
    c = C.match("открой блокнот и напечатай Привет, мир")
    assert (c.op, c.text, c.keys) == ("type_in", "Привет, мир", ["notepad"])


def test_still_correct():
    """Новые правила не ломают старые команды."""
    assert C.match("следующий трек").op == "next" and C.match("предыдущая песня").op == "previous"
    assert C.match("что за музыка").op == "now_playing" and C.match("выключи музыку").op == "pause"
    assert C.match("открой понравившуюся музыку") is None and C.match("громкость 30").op == "volume_set"
    assert C.match("нажми кнопку сохранить").op == "click_name" and C.match("сверни все окна").keys == ["win", "d"]
    assert C.match("прокрути вниз").op == "scroll" and C.match("таймер на 5 минут") is None


def test_mail_and_screen_phrases():
    assert RE_MAIL.search("что у меня по e-mail") and RE_MAIL.search("Есть ли новые сообщения?")
    assert not RE_MAIL.search("есть новые сообщения в телеграме")
    for t in ("Смотри в мой Google. Я не вижу, куда мне нужно тыкнуть.", "Что за ошибка повышал?", "куда нажать?"):
        assert screen.is_screen_question(t), t


# ------------------------------------------------------------------ агент
class LLM:
    def __init__(self, *msgs):
        self.msgs, self.calls = list(msgs), []

    def chat(self, messages, tools=True, on_token=None, should_stop=None):
        self.calls.append(tools)
        return self.msgs.pop(0) if self.msgs else {"content": "ок"}


class Sys:
    def run(self, cmd):
        return {}


def agent(llm=None, claude=lambda t, c: "ответ"):
    return Agent(copy.deepcopy(DEFAULTS), llm=llm or LLM(), claude=claude, system=Sys(), apps=AppIndex(Path("/x")))


def test_bare_timer_asks_how_long():
    r = agent().handle("таймер.")
    assert "На сколько" in r.text and not r.actions


def test_model_cannot_invent_timer_or_spotify():
    timer = {"content": "", "tool_calls": [{"function": {"name": "set_timer", "arguments": {"minutes": 10}}}]}
    llm = LLM(timer, {"content": "git pull — подтянуть обновления."})
    r = agent(llm).handle("Напомним мне команду, как с репозитория подтянуть к себе обновление.")
    assert not r.actions and "git pull" in r.text and llm.calls[-1] is None     # второй раз — без инструментов
    spot = {"content": "Включаю.", "tool_calls": [{"function": {"name": "open_app", "arguments": {"name": "Spotify"}}}]}
    assert not agent(LLM(spot)).handle("расскажи анекдот").actions


def test_quiet_and_maps_and_help(tmp_path, monkeypatch):
    a = agent()
    assert a.handle("Shut up").actions[0].kind == "quiet"
    r = a.handle("Найди ближайший ко мне ресторан.")
    assert "google.com/maps/search" in r.actions[0].params["url"]
    monkeypatch.setattr(files, "known_folder", lambda key: tmp_path)
    r = a.handle("Напиши весь список своих команд в блокнот.")
    assert r.actions[0].kind == "open_file" and (tmp_path / "Capri — команды.txt").read_text(encoding="utf-8-sig").startswith("КАПРИ")
    from capri.agent import RE_HELP
    assert not RE_HELP.search("напиши команду git pull") and RE_HELP.search("что ты умеешь?")


def test_script_goes_to_claude_and_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "known_folder", lambda key: tmp_path)
    answer = "Вот скрипт: покажет 10 больших файлов.\n```powershell\nGet-ChildItem $env:USERPROFILE\\Downloads | Sort Length -Desc | Select -First 10\n```"
    r = agent(claude=lambda t, c: answer).handle("Напиши PowerShellScript, который показывает 10 самых больших файлов в загрузках.")
    assert r.route == "claude" and r.actions[0].kind == "open_file" and "```" not in r.text
    saved = list((tmp_path / "Capri скрипты").glob("*.ps1"))
    assert saved and "Get-ChildItem" in saved[0].read_text(encoding="utf-8-sig")


def test_claude_is_not_in_plan_mode(monkeypatch, tmp_path):
    from capri.llm import claude_code
    seen = {}

    class P:
        returncode, stdout, stderr = 0, '{"result": "ok"}', ""

    monkeypatch.setattr(claude_code, "find_claude", lambda c: "claude")
    monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd) or P())
    claude_code.ask("задача", {"permission_mode": "plan", "workdir": str(tmp_path)})
    assert "plan" not in seen["cmd"] and any(x.startswith("--disallowedTools=") for x in seen["cmd"])


def test_night_city_slang_toggle(monkeypatch, tmp_path):
    from capri.llm import claude_code
    from capri.persona import system_prompt
    assert "чумба" in system_prompt("Игорь", "sassy", True) and "Никаких «чумба»" in system_prompt("Игорь", "sassy", False)
    seen = {}

    class P:
        returncode, stdout, stderr = 0, '{"result": "ok"}', ""

    monkeypatch.setattr(claude_code, "find_claude", lambda c: "claude")
    monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **k: seen.update(p=cmd[2]) or P())
    claude_code.ask("x", {"workdir": str(tmp_path), "slang": True})
    assert "чумба" in seen["p"]
    claude_code.ask("x", {"workdir": str(tmp_path), "slang": False})
    assert "чумба" not in seen["p"]


def test_more_log_phrases():
    from capri import scenarios
    assert scenarios.match("Выключить ночной режим.", scenarios.DEFAULT) == ("ночной", False)
    cmd = C.match("проверь пока что почту")
    assert cmd is None or cmd.op != "pc_check"
    assert C.match("включи второй экран").text == "extend"


@pytest.mark.parametrize("text,mode,confirm", [
    ("Перезагрузи компьютер.", "restart", True), ("перезагрузи мой комп", "restart", True),
    ("выключи компьютер", "shutdown", True), ("спящий режим", "sleep", True), ("отмени перезагрузку", "abort", False),
])
def test_power_is_real_and_asks_first(text, mode, confirm):
    r = agent(LLM({"content": "я перезагружаю"})).handle(text)
    assert r.actions[0].kind == "power" and r.actions[0].params["mode"] == mode and r.actions[0].needs_confirm is confirm
    assert C.match("выключи звук").op == "mute"           # «выключи …» без компьютера — как раньше


def test_exact_search_never_breaks_the_old_one(monkeypatch):
    """Быстрый поиск кнопки сломался (другая версия uiautomation) — должен тихо уступить обычному поиску."""
    import sys
    import types

    from capri import control
    fake = types.ModuleType("uiautomation")
    fake.ControlFromHandle = lambda h: None
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    monkeypatch.setitem(sys.modules, "uiautomation.uiautomation", types.ModuleType("uiautomation.uiautomation"))
    assert control.find_exact(1, ["Next"]) is None
