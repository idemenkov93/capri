import pytest

from capri import commands as C
from capri.agent import Agent
from capri.config import DEFAULTS


@pytest.mark.parametrize("text,op,value", [
    ("пауза", "pause", None),
    ("Капри, поставь на паузу", "pause", None),
    ("продолжи", "play", None),
    ("следующий трек", "next", None),
    ("предыдущая песня", "previous", None),
    ("что сейчас играет?", "now_playing", None),
    ("поставь на 3:14", "seek_to", 194),
    ("поставь на три минуты четырнадцать секунд", "seek_to", 194),
    ("перемотай на 30 секунд вперёд", "seek_by", 30),
    ("перемотай назад на минуту", "seek_by", -60),
    ("отмотай на полминуты", "seek_by", -30),
    ("начни сначала", "seek_to", 0),
    ("громкость 40", "volume_set", 40),
    ("громкость на сорок процентов", "volume_set", 40),
    ("громкость на максимум", "volume_set", 100),
    ("сделай громче", "volume_change", 10),
    ("тише на 20", "volume_change", -20),
    ("выключи звук", "mute", None),
    ("включи звук", "unmute", None),
    ("какая громкость", "volume_get", None),
    ("яркость 70", "brightness_set", 70),
    ("темнее", "brightness_change", -15),
    ("проверь компьютер", "pc_check", None),
    ("что грузит компьютер", "pc_check", None),
    ("сделай скриншот", "screenshot", None),
    ("заблокируй компьютер", "lock", None),
    ("volume 30", "volume_set", 30),
    ("next track", "next", None),
    ("what's playing", "now_playing", None),
    ("mach lauter", None, None),
    ("lauter", "volume_change", 10),
    ("Lautstärke auf 25", "volume_set", 25),
    ("check my pc", "pc_check", None),
])
def test_match(text, op, value):
    cmd = C.match(text)
    if op is None:
        assert cmd is None
        return
    assert cmd and cmd.op == op
    if value is not None:
        assert cmd.value == value


@pytest.mark.parametrize("text", ["открой браузер", "как дела", "включи ютуб", "расскажи анекдот", "таймер на 5 минут"])
def test_not_a_system_command(text):
    assert C.match(text) is None


@pytest.mark.parametrize("text,keys,repeat", [
    ("нажми контрол шифт эскейп", ["ctrl", "shift", "esc"], 1),
    ("нажми вин таб", ["win", "tab"], 1),
    ("нажми альт эф четыре", ["alt", "f4"], 1),
    ("нажми ctrl+c", ["ctrl", "c"], 1),
    ("нажми контрол цэ", ["ctrl", "c"], 1),
    ("нажми пробел три раза", ["space"], 3),
    ("нажми стрелку вверх", ["up"], 1),
    ("press alt tab", ["alt", "tab"], 1),
])
def test_keys(text, keys, repeat):
    cmd = C.match(text)
    assert cmd.op == "keys" and cmd.keys == keys and cmd.repeat == repeat


def test_unknown_key_word_is_not_a_command():
    assert C.match("нажми на кнопку счастья") is None


def test_key_codes():
    assert C.key_codes(["ctrl", "shift", "esc"]) == [(0x11, False), (0x10, False), (0x1B, False)]
    assert C.key_codes(["win", "f5", "a"]) == [(0x5B, True), (0x74, False), (0x41, False)]


def test_numbers_and_seconds():
    assert C.numbers("двадцать пять") == [25]
    assert C.numbers("три четырнадцать") == [3, 14]
    assert C.parse_seconds("1:02:03") == 3723
    assert C.parse_seconds("тайминг 314") == 194
    assert C.parse_seconds("полторы минуты") == 90


def test_lang():
    assert C.lang_of("громче") == "ru"
    assert C.lang_of("lauter bitte") == "de"
    assert C.lang_of("louder") == "en"


def test_replies():
    assert C.reply(C.Command("volume_set", 40), "ru", {"volume": 40}) == "Громкость 40%."
    assert C.reply(C.Command("now_playing"), "ru", {"track": "Daft Punk — One More Time"}) == "Сейчас играет: Daft Punk — One More Time."
    assert C.reply(C.Command("now_playing"), "en", {}) == "Nothing is playing."
    assert C.reply(C.Command("seek_to", 194), "ru", {}) == "Поставила на 3:14."
    assert "Не вышло" in C.reply(C.Command("mute"), "ru", {"error": "нет звука"})
    s = C.reply(C.Command("pc_check"), "ru", {"cpu": 12, "ram_percent": 55, "issues": ["на диске C: мало места (5 ГБ)"], "top_cpu": "chrome.exe"})
    assert "Процессор 12%" in s and "мало места" in s and "chrome.exe" in s


class FakeSystem:
    def __init__(self):
        self.ran = []

    def run(self, cmd):
        self.ran.append(cmd)
        return {"volume": 40}


class NoLLM:
    calls = 0

    def chat(self, *a, **k):
        NoLLM.calls += 1
        return {"content": "?"}


def test_agent_runs_system_commands_instantly():
    sysctl = FakeSystem()
    agent = Agent(dict(DEFAULTS), llm=NoLLM(), claude=lambda t, c: "", system=sysctl)
    r = agent.handle("Капри, громкость 40")
    assert r.route == "rules" and r.text == "Громкость 40%."
    assert sysctl.ran[0].op == "volume_set"
    assert NoLLM.calls == 0
