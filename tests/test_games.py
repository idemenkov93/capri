"""Игровой помощник: как узнаём игру, CS2 Game State Integration, таймер бомбы, экономика, итог матча."""
import copy
import json
import os
import socket
import time
import urllib.request
from pathlib import Path

import pytest

from capri import cs2, game, gpu, grab
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS

KEEP = []   # Qt-объекты живут до конца тестов (сборка мусора посреди фоновых потоков роняет Windows)

STEAM = r"D:\SteamLibrary\steamapps\common"


@pytest.mark.parametrize("path,name,mode", [
    (STEAM + r"\Counter-Strike Global Offensive\game\bin\win64\cs2.exe", "Counter-Strike 2", "strict"),
    (STEAM + r"\Hades II\Ship\Hades2.exe", "Hades II", "soft"),
    (r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\FortniteClient-Win64-Shipping.exe", "Fortnite", "soft"),
    (r"C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe", "Valorant", "soft"),
    (r"E:\Games\Witcher 3\bin\x64\witcher3.exe", "Ведьмак 3", "soft"),
])
def test_games_are_recognised(path, name, mode):
    g = game.classify(path)
    assert g and (g.name, g.mode) == (name, mode)


@pytest.mark.parametrize("path", [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Steam\steam.exe",
    STEAM + r"\wallpaper_engine\wallpaper64.exe", STEAM + r"\Some Game\UnityCrashHandler64.exe",
    r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
    STEAM + r"\Steamworks Shared\_CommonRedist\vcredist\2019\VC_redist.x64.exe", r"C:\Windows\explorer.exe", "",
])
def test_not_games(path):
    assert game.classify(path) is None


def test_config_lists():
    cfg = {"strict": ["cs2.exe", "valorant-win64-shipping.exe"], "extra": ["mygame.exe"], "ignore": ["hades2.exe"]}
    assert game.classify(r"C:\Riot Games\VALORANT\VALORANT-Win64-Shipping.exe", cfg).mode == "strict"
    assert game.classify(r"C:\Tools\mygame.exe", cfg).mode == "soft"
    assert game.classify(STEAM + r"\Hades II\Ship\Hades2.exe", cfg) is None


def test_watcher_follows_the_process():
    fg = {"v": (1, 0, r"C:\Windows\explorer.exe")}
    alive = {"v": True}
    w = game.Watcher(fg=lambda: fg["v"], is_alive=lambda pid: alive["v"])
    assert w.poll() is None
    fg["v"] = (2, 777, STEAM + r"\Counter-Strike Global Offensive\game\bin\win64\cs2.exe")
    kind, g = w.poll()
    assert kind == "start" and g.pid == 777 and g.is_cs2
    fg["v"] = (3, 5, r"C:\Program Files\Discord\Discord.exe")      # свернул в Discord — игра всё ещё идёт
    assert w.poll() is None and w.current
    alive["v"] = False
    assert w.poll()[0] == "stop" and w.current is None
    assert game.human_duration(95 * 60) == "1 ч 35 мин" and game.human_duration(300) == "5 мин"


# ------------------------------------------------------------------ CS2: файл и сервер
def test_cfg_goes_next_to_the_game(tmp_path):
    root = tmp_path / "Counter-Strike Global Offensive" / "game"
    (root / "csgo" / "cfg").mkdir(parents=True)
    exe = root / "bin" / "win64" / "cs2.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    written, ok = cs2.install("abc123", 3127, str(exe), libraries=[])
    f = root / "csgo" / "cfg" / cs2.CFG_NAME
    assert written == [f] and not ok and '"token"\t"abc123"' in f.read_text(encoding="utf-8")
    assert "127.0.0.1:3127" in f.read_text(encoding="utf-8")
    assert cs2.install("abc123", 3127, str(exe), libraries=[]) == ([], [f])        # уже лежит — не трогаем
    lib = tmp_path / "lib"
    (lib / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg").mkdir(parents=True)
    assert len(cs2.cfg_dirs(None, [lib])) == 1


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_server_accepts_only_our_token():
    got = []
    port = _free_port()
    srv = cs2.Server(port, "tok", got.append)
    srv.start()
    try:
        for token in ("bad", "tok"):
            body = json.dumps({"auth": {"token": token}, "round": {"phase": "live"}}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/", body, {"Content-Type": "application/json"})
            assert urllib.request.urlopen(req, timeout=5).status == 200
        end = time.time() + 3
        while not got and time.time() < end:
            time.sleep(0.05)
        assert len(got) == 1 and got[0]["round"]["phase"] == "live"
    finally:
        srv.stop()


# ------------------------------------------------------------------ CS2: что происходит в матче
ME = "7656119"


def payload(phase="live", bomb=None, money=800, team="CT", rk=0, hs=0, dmg=0, k=0, d=0, rnd=0, ct=0, t=0,
            map_phase="live", win=None, steamid=ME, mode="competitive", losses=0):
    rd = {"phase": phase}
    if bomb:
        rd["bomb"] = bomb
    if win:
        rd["win_team"] = win
    return {"provider": {"steamid": ME},
            "map": {"name": "de_mirage", "mode": mode, "phase": map_phase, "round": rnd,
                    "team_ct": {"score": ct, "consecutive_round_losses": losses if team == "CT" else 0},
                    "team_t": {"score": t, "consecutive_round_losses": losses if team == "T" else 0}},
            "round": rd,
            "player": {"steamid": steamid, "team": team,
                       "state": {"money": money, "round_kills": rk, "round_killhs": hs, "round_totaldmg": dmg},
                       "match_stats": {"kills": k, "deaths": d, "assists": 1, "mvps": 0}}}


def test_round_flow_and_bomb_events():
    tr = cs2.Tracker()
    ev = tr.update(payload("freezetime", money=800))
    assert ev[0][0] == "freezetime" and "Пистолетка" in ev[0][1]["tip"]
    assert [e[0] for e in tr.update(payload("live"))] == ["live"] and tr.live
    tr.update(payload("live", rk=2, hs=1, dmg=180, k=2))
    tr.update(payload("live", steamid="someone-else", rk=0, k=0))   # умер и смотрит за тиммейтом — его цифры не наши
    assert [e[0] for e in tr.update(payload("live", bomb="planted", steamid="someone-else"))] == ["bomb_planted"]
    ev = tr.update(payload("over", bomb="defused", win="CT", ct=1, k=2))
    kinds = [e[0] for e in ev]
    assert "round_over" in kinds and "bomb_defused" in kinds and not tr.live
    rec = dict(ev)["round_over"]
    assert rec == {"n": 1, "won": True, "kills": 2, "hs": 1, "dmg": 180, "died": False}


def test_match_summary_and_stats_phrase():
    tr = cs2.Tracker()
    tr.update(payload("freezetime"))
    tr.update(payload("live", rk=3, hs=2, dmg=250, k=3))
    tr.update(payload("over", win="CT", ct=1, k=3, rk=3, hs=2, dmg=250))
    tr.update(payload("freezetime", rnd=1, ct=1, k=3))
    tr.update(payload("live", rnd=1, ct=1, k=3, d=1))
    tr.update(payload("over", rnd=1, ct=1, t=1, win="T", k=3, d=1))
    ev = tr.update(payload("over", rnd=2, ct=13, t=9, k=21, d=14, map_phase="gameover"))
    d = dict(ev)["match_over"]
    text = cs2.summary_text(d)
    assert "Mirage: победа 13:9" in text and "21–14" in text and "Лучший раунд — 1-й: 3 килла" in text
    assert tr.last_match is d and tr.current()["finished"]
    tr.update({"provider": {"steamid": ME}})                       # вышел в меню
    assert tr.map == "" and tr.current() is d
    assert "сыграй матч" in cs2.summary_text(None)


@pytest.mark.parametrize("money,team,rnd,losses,want", [
    (800, "T", 1, 0, "Пистолетка"), (800, "CT", 13, 0, "Пистолетка"),
    (3000, "T", 12, 0, "Последний раунд"), (5200, "CT", 5, 0, ""), (4400, "T", 5, 0, ""),
    (1500, "T", 4, 3, "Эко. Проиграем — будет около 4400"), (1500, "CT", 4, 3, "Эко, копим"), (1000, "T", 4, 0, "Эко, копим"),
    (3000, "CT", 6, 0, "На полный закуп не хватает"), (800, "CT", 26, 0, ""),
])
def test_econ_tips(money, team, rnd, losses, want):
    tip = cs2.econ_tip(money, team, rnd, "competitive", losses)
    assert (tip.startswith(want) if want else tip == ""), tip
    assert cs2.econ_tip(800, "T", 1, "deathmatch") == ""            # в дезматче — молчим


def test_words():
    assert cs2.kills_word(1) == "1 килл" and cs2.kills_word(3) == "3 килла" and cs2.kills_word(11) == "11 киллов"
    assert cs2.map_title("de_ancient") == "Ancient" and cs2.map_title("cs_office") == "Office"


# ------------------------------------------------------------------ агент в игре
class FakeGame:
    def __init__(self, strict=True, live=False, cs=True):
        self.active, self.strict = True, strict
        self.tracker = cs2.Tracker()
        self.tracker.round_phase = "live" if live else "freezetime"
        self._cs = cs

    def busy_round(self):
        return self.strict and self._cs and self.tracker.live

    def context(self):
        return "Пользователь сейчас играет в Counter-Strike 2."


class Sys:
    def __init__(self):
        self.ran = []

    def run(self, cmd):
        self.ran.append(cmd.op)
        return {}


class NoLLM:
    def chat(self, *a, **k):
        raise AssertionError("локальную модель в игре трогать нельзя")


def agent(g, claude=lambda t, c: "ответ"):
    a = Agent(copy.deepcopy(DEFAULTS), llm=NoLLM(), claude=claude, system=Sys(), apps=AppIndex(Path("/x")))
    a.game = g
    return a


def test_live_round_waits_but_commands_work():
    a = agent(FakeGame(live=True))
    r = a.handle("Капри, громкость 30")
    assert r.route == "rules" and a.system.ran == ["volume_set"]           # команды — сразу
    r = a.handle("кто сделал лучший раунд в истории мейджоров")
    assert r.actions[0].kind == "game_defer" and "после раунда" in r.text
    assert a.handle("разбери мою игру").text.startswith("Пока нечего")     # без данных — честно


def test_between_rounds_questions_go_to_claude_with_context():
    seen = []
    a = agent(FakeGame(live=False), claude=lambda t, c: seen.append(t) or "Бери AWP.")
    assert a.handle("что лучше купить на эко?").text == "Бери AWP." and "Counter-Strike 2" in seen[0]
    a.cfg["claude_code"]["enabled"] = False
    assert "локальную модель не включаю" in a.handle("расскажи анекдот").text


def test_game_phrases():
    a = agent(FakeGame())
    for text, key, on in (("турнирный режим", "cs2_tournament", True), ("выключи турнирный режим", "cs2_tournament", False),
                          ("выключи таймер бомбы", "cs2_bomb_timer", False), ("включи таймер бомбы", "cs2_bomb_timer", True),
                          ("не мешай в играх", "game_assist", False), ("выключи подсказки по экономике", "cs2_econ", False)):
        r = a.handle(text)
        assert r.actions[0].kind == "game_set" and r.actions[0].params == {"key": key, "on": on}, text
    assert "сыграй матч" in a.handle("Капри, как я сыграл?").text
    assert a.handle("игровой режим").text != game.TOGGLE_TEXT["game_assist"][1]   # сценарий «игровой режим» — отдельно


def test_gpu_temperature(monkeypatch):
    a = agent(None)
    monkeypatch.setattr(gpu, "read", lambda: {"temp": 87, "load": 99})
    assert a.handle("какая температура видеокарты?").text == "Видеокарта: 87°, загрузка 99%. Горячо — проверь обдув."
    s = gpu.Stats()
    for t in (60, 70, 88):
        s.add({"temp": t})
    assert "максимум 88°, в среднем 73°" in s.summary() and s.hot


# ------------------------------------------------------------------ снимок экрана
def test_black_frame_detection():
    from PIL import Image
    assert grab.is_black(Image.new("RGB", (200, 100)))
    assert not grab.is_black(Image.new("RGB", (200, 100), (40, 90, 200)))


def test_vosk_transcriber_on_silence():
    pytest.importorskip("vosk")
    import numpy as np

    from capri.voice.vosk_stt import VoskTranscriber
    assert VoskTranscriber(Path("/nope")).transcribe(np.zeros(100, np.float32)) == ("", "")


# ------------------------------------------------------------------ режим целиком (Qt)
class Voice:
    def __init__(self):
        self.said, self.cached = [], []

    def speak(self, text):
        self.said.append(text)

    def play_cached(self, key, fallback=""):
        self.cached.append(key)


class Prefs:
    def __init__(self, **kw):
        self.data = dict(kw)

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v


class Chat:
    def __init__(self):
        self.lines = []

    def add_capri(self, text, route=""):
        self.lines.append(text)

    def add_system(self, text):
        self.lines.append(text)


def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def fake_app(**prefs):
    class App:
        pass
    a = App()
    a.cfg = copy.deepcopy(DEFAULTS)
    a.cfg["games"]["enabled"] = False                  # без опроса окон и без записи файлов
    a.prefs, a.voice, a.chat = Prefs(**prefs), Voice(), Chat()
    a.messages = []
    a.on_message = a.messages.append
    return a


def test_bomb_timer_and_tips():
    q = qt()
    from capri.gamemode import GameMode
    app = fake_app()
    gm = GameMode(app)
    KEEP.append(gm)
    gm.game = game.GameInfo("cs2.exe", "Counter-Strike 2", "strict")
    gm.cfg.update(c4_time=0.5, gsi_delay=0)
    old = cs2.CALLOUT_AT
    cs2.CALLOUT_AT = (("t20", 0.4), ("t10", 0.2))
    try:
        gm.on_event("bomb_planted", {})
        assert app.voice.cached == ["planted"] and len(gm._bomb) == 2
        end = time.time() + 2
        while len(app.voice.cached) < 3 and time.time() < end:
            q.processEvents()
            time.sleep(0.02)
        assert app.voice.cached == ["planted", "t20", "t10"]
        gm.on_event("bomb_planted", {})
        gm.on_event("bomb_defused", {})                    # обезвредили — дальше не считаем
        assert not gm._bomb
    finally:
        cs2.CALLOUT_AT = old
    gm.on_event("freezetime", {"tip": "Эко, копим."})
    assert app.voice.said == ["Эко, копим."]
    gm.defer("кто лучший снайпер")
    gm.on_event("round_over", {})
    end = time.time() + 2
    while not app.messages and time.time() < end:
        q.processEvents()
        time.sleep(0.02)
    assert app.messages == ["кто лучший снайпер"]


def test_tournament_mode_is_silent():
    qt()
    from capri.gamemode import GameMode
    app = fake_app(cs2_tournament=True)
    gm = GameMode(app)
    KEEP.append(gm)
    gm.game = game.GameInfo("cs2.exe", "Counter-Strike 2", "strict")
    gm.on_event("bomb_planted", {})
    gm.on_event("freezetime", {"tip": "Эко."})
    assert not app.voice.cached and not app.voice.said and not gm._bomb
    assert "турнирный" in gm.set_pref("cs2_tournament", False).lower()


def test_enter_and_leave_with_the_real_app(monkeypatch):
    qt()
    import capri.app as appmod
    from capri.applist import AppIndex as AI
    monkeypatch.setattr(appmod.CapriApp, "_init_voice", lambda self: None)
    monkeypatch.setattr(appmod.CapriApp, "start_dancing", lambda self: None)
    monkeypatch.setattr(AI, "refresh", lambda self: 0)
    monkeypatch.setattr(cs2, "install", lambda *a, **k: ([], []))
    monkeypatch.setattr(cs2.Server, "start", lambda self: None)
    monkeypatch.setattr(game, "set_low_priority", lambda low: None)
    app = appmod.CapriApp()
    KEEP.append(app)
    app.warmup.wait(10_000)
    app.game._poll_timer.stop()
    assert app.avatar.isVisible() and not app.in_game
    g = game.GameInfo("cs2.exe", "Counter-Strike 2", "strict", pid=1)
    app.game.enter(g)
    assert app.in_game and not app.avatar.isVisible() and not app.avatar._tick.isActive()
    said = []
    monkeypatch.setattr(app, "timer_fired", lambda text, prefix="": said.append(text))
    app.set_break_reminder(60)
    app._work_since -= 61 * 60
    from capri import control
    monkeypatch.setattr(control, "idle_seconds", lambda: 0)
    app._break_check()
    assert not said and app._break_due                    # посреди игры не дёргаем
    app.game.leave(g)
    app.warmup.wait(10_000)
    assert not app.in_game and app.avatar.isVisible() and app.avatar._tick.isActive()
    assert len(said) == 1 and "ольше 1 ч" in said[0] and not app._break_due   # сказала, когда игру закрыл
    app.set_break_reminder(0)


def test_stats_phrase_is_not_too_greedy():
    from capri.agent import RE_GAME_STATS
    assert RE_GAME_STATS.search("покажи мою статистику") and RE_GAME_STATS.search("статистика матча")
    assert not RE_GAME_STATS.search("покажи статистику процессора")


def test_stuck_player_does_not_hang_capri(monkeypatch):
    from capri import system

    async def never():
        await __import__("asyncio").sleep(30)
    with pytest.raises(system.MediaTimeout):
        system.run_async(never, 0.3)

    async def fine():
        return {"ok": 1}
    assert system.run_async(fine, 2) == {"ok": 1}


def test_new_request_replaces_a_stuck_one(monkeypatch):
    qt()
    import capri.app as appmod
    from capri.agent import Reply
    from capri.applist import AppIndex as AI
    monkeypatch.setattr(appmod.CapriApp, "_init_voice", lambda self: None)
    monkeypatch.setattr(appmod.CapriApp, "start_dancing", lambda self: None)
    monkeypatch.setattr(AI, "refresh", lambda self: 0)
    app = appmod.CapriApp()
    KEEP.append(app)
    app.warmup.wait(10_000)
    app.game._poll_timer.stop()
    import threading
    release = threading.Event()
    replies = []
    monkeypatch.setattr(app, "on_reply", replies.append)

    def handle(text, **k):
        if text == "следующий трек":
            release.wait(10)                # «плеер не отвечает»
        return Reply(f"ок: {text}", route="rules")
    monkeypatch.setattr(app.agent, "handle", handle)
    app.on_message("следующий трек")
    stuck = app.worker
    stuck.started_at -= 60                   # висит уже минуту
    app.on_message("громкость 80")
    assert app.worker is not stuck and stuck.abandoned
    app.worker.wait(5000)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    release.set()
    stuck.wait(5000)
    QApplication.processEvents()
    assert [r.text for r in replies] == ["ок: громкость 80"]      # ответ брошенного запроса не показываем
