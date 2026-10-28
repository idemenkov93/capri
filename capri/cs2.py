"""CS2 через Game State Integration — официальный способ Valve отдавать состояние игры программам.

Как это работает: в папку игры кладём файл gamestate_integration_capri.cfg. CS2 при запуске читает его и
сама шлёт нам на 127.0.0.1 короткие JSON-сообщения: карта, счёт, раунд, бомба, твои деньги и статистика.
В игру ничего не внедряется, память не читается — это то же, чем пользуются стримерские оверлеи и
турнирные HUD-ы, VAC такое не трогает. Если файла не было, CS2 надо перезапустить один раз.

Что Капри делает с этим:
  * таймер бомбы — заранее записанные фразы: «Бомба. Сорок», «Двадцать», «Десять — без кита впритык», «Пять — только с китом»;
  * подсказка по экономике в начале раунда (только когда решение неочевидно);
  * итог матча и ответ на «как я сыграл?».
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import threading
from pathlib import Path

CFG_NAME = "gamestate_integration_capri.cfg"
DEFAULT_PORT = 3127
C4_TIME = 40.0          # секунд от установки до взрыва (соревновательный и Premier)

CALLOUTS = {             # ключ → фраза. Записываются голосом Капри заранее, во время игры только проигрываются.
    "planted": "Бомба. Сорок.",
    "t20": "Двадцать.",
    "t10": "Десять — без кита впритык.",
    "t5": "Пять — только с китом.",
}
CALLOUT_AT = (("t20", 20.0), ("t10", 10.0), ("t5", 5.0))   # за сколько секунд до взрыва


def cfg_text(token: str, port: int = DEFAULT_PORT) -> str:
    return f'''"Capri Desktop Companion"
{{
	"uri"		"http://127.0.0.1:{port}/"
	"timeout"	"1.1"
	"buffer"	"0.0"
	"throttle"	"0.1"
	"heartbeat"	"10.0"
	"auth"
	{{
		"token"	"{token}"
	}}
	"data"
	{{
		"provider"		"1"
		"map"			"1"
		"round"			"1"
		"player_id"		"1"
		"player_state"		"1"
		"player_match_stats"	"1"
	}}
}}
'''


def new_token() -> str:
    return secrets.token_hex(8)


# ------------------------------------------------------------------ где лежит CS2
def steam_libraries() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        import winreg
        for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, val)[0]))
            except OSError:
                continue
    libs: list[Path] = []
    for root in roots:
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")))
        except OSError:
            pass
    out: list[Path] = []
    for p in libs:
        if p not in out:
            out.append(p)
    return out


def cfg_dirs(exe_path: str | None = None, libraries: list[Path] | None = None) -> list[Path]:
    """Папки game/csgo/cfg: от запущенной cs2.exe (…\\game\\bin\\win64\\cs2.exe) и из библиотек Steam."""
    dirs: list[Path] = []
    if exe_path:
        p = Path(exe_path.replace("\\", "/")) if sys.platform != "win32" else Path(exe_path)
        if len(p.parents) >= 3:
            cand = p.parents[2] / "csgo" / "cfg"
            if cand.is_dir():
                dirs.append(cand)
    for lib in steam_libraries() if libraries is None else libraries:
        cand = Path(lib) / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg"
        if cand.is_dir() and cand not in dirs:
            dirs.append(cand)
    return dirs


def install(token: str, port: int = DEFAULT_PORT, exe_path: str | None = None,
            libraries: list[Path] | None = None) -> tuple[list[Path], list[Path]]:
    """Положить cfg. → (куда записали заново, где уже был нужный)."""
    text = cfg_text(token, port)
    written, ok = [], []
    for d in cfg_dirs(exe_path, libraries):
        f = d / CFG_NAME
        try:
            if f.exists() and f.read_text(encoding="utf-8", errors="ignore") == text:
                ok.append(f)
                continue
            f.write_text(text, encoding="utf-8")
            written.append(f)
        except OSError:
            continue
    return written, ok


# ------------------------------------------------------------------ приём сообщений от игры
class Server:
    """Маленький HTTP-сервер только на 127.0.0.1. on_payload(dict) зовётся из его потока."""

    def __init__(self, port: int, token: str, on_payload):
        self.port, self.token, self.on_payload = port, token, on_payload
        self.httpd = None

    def start(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(n) if n else b""
                finally:
                    self.send_response(200)
                    self.end_headers()
                try:
                    data = json.loads(body.decode("utf-8") or "{}")
                except ValueError:
                    return
                if (data.get("auth") or {}).get("token") != outer.token:
                    return
                try:
                    outer.on_payload(data)
                except Exception:  # noqa: BLE001
                    from .log import log
                    log.exception("cs2: ошибка разбора")

            def log_message(self, *a):  # тишина в консоли
                pass

        self.httpd = HTTPServer(("127.0.0.1", self.port), H)   # один поток — сообщения по порядку
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True,
                         name="cs2-gsi").start()

    def stop(self) -> None:
        if self.httpd:
            h, self.httpd = self.httpd, None
            threading.Thread(target=lambda: (h.shutdown(), h.server_close()), daemon=True).start()


# ------------------------------------------------------------------ что происходит в матче
def map_title(name: str) -> str:
    n = re.sub(r"^(de|cs|ar|dz|gd)_", "", name or "")
    return n.replace("_", " ").title() if n else "карта"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def kills_word(n: int) -> str:
    return f"{n} {_plural(n, 'килл', 'килла', 'киллов')}"


COMPETITIVE = {"competitive": 12, "premier": 12, "scrimcomp2v2": 8}


def econ_tip(money: int | None, team: str, round_no: int, mode: str, losses: int = 0) -> str:
    """Совет в начале раунда round_no (с 1). Пусто — когда всё и так ясно (полный закуп)."""
    half = COMPETITIVE.get(mode or "")
    if not half or money is None or round_no < 1:
        return ""
    if round_no in (1, half + 1):
        return "Пистолетка. " + ("Броня или кит — договорись с командой." if team == "CT" else "Броня или граната — одно из двух.")
    if round_no in (half, 2 * half):
        return "Последний раунд половины — деньги сгорят, трать всё."
    if round_no > 2 * half:
        return ""                           # овертайм: там своя экономика
    full = 4300 if team == "T" else 5000
    if money >= full:
        return ""
    if money < 2000:
        nxt = money + min(1400 + 500 * max(0, losses), 3400)
        if nxt >= full:
            return f"Эко. Проиграем — будет около {nxt}, хватит на полный закуп."
        return "Эко, копим. Максимум пистолет."
    return f"На полный закуп не хватает, у тебя {money}. Форс или эко — по команде."


class Tracker:
    """Разбирает сообщения GSI → события и статистика. Работает в главном потоке."""

    def __init__(self):
        self.last_match: dict | None = None
        self._reset("")

    def _reset(self, name: str):
        self.map, self.mode = name, ""
        self.map_phase = self.round_phase = self.bomb = ""
        self.team, self.money, self.completed = "", None, 0
        self.ct = self.t = 0
        self.ct_losses = self.t_losses = 0
        self.stats: dict = {}
        self.rounds: list[dict] = []
        self.own_round: dict = {}
        self._deaths_before = 0
        self.finished = False

    @property
    def live(self) -> bool:
        """Идёт раунд (а не закупка, не конец раунда, не меню)."""
        return self.round_phase == "live" and self.map_phase in ("live", "")

    @property
    def in_match(self) -> bool:
        return bool(self.map) and self.map_phase in ("live", "warmup", "intermission") and not self.finished

    def update(self, p: dict) -> list[tuple[str, dict]]:
        ev: list[tuple[str, dict]] = []
        me = (p.get("provider") or {}).get("steamid")
        mp, rd, pl = p.get("map") or {}, p.get("round") or {}, p.get("player") or {}
        name = mp.get("name") or ""
        mph = mp.get("phase") or ""
        if not mp and self.map:
            if self.rounds and not self.finished:
                self.last_match = self.summary_data()          # вышел в меню посреди матча — запомним, что было
            self._reset("")
        elif name and (name != self.map or (self.finished and mph in ("warmup", "live"))):
            if self.map and self.rounds and not self.finished:
                self.last_match = self.summary_data()
            self._reset(name)                                  # новая карта или новый матч на той же
        if mp:
            self.mode = mp.get("mode") or self.mode
            self.completed = int(mp.get("round") or 0)
            ct, t = mp.get("team_ct") or {}, mp.get("team_t") or {}
            self.ct, self.t = int(ct.get("score") or 0), int(t.get("score") or 0)
            self.ct_losses = int(ct.get("consecutive_round_losses") or 0)
            self.t_losses = int(t.get("consecutive_round_losses") or 0)
        own = bool(pl) and (not me or pl.get("steamid") == me)
        if own:
            st, ms = pl.get("state") or {}, pl.get("match_stats") or {}
            self.team = pl.get("team") or self.team
            if st.get("money") is not None:
                self.money = int(st["money"])
            for k in ("round_kills", "round_killhs", "round_totaldmg"):
                if st.get(k) is not None:
                    self.own_round[k] = max(int(st[k]), int(self.own_round.get(k) or 0))
            if ms:
                self.stats = ms

        phase = rd.get("phase") or ""
        if phase != self.round_phase:
            self.round_phase = phase
            if phase == "freezetime":
                self.own_round = {}
                self._deaths_before = int(self.stats.get("deaths") or 0)
                losses = self.ct_losses if self.team == "CT" else self.t_losses
                tip = "" if mph == "warmup" else econ_tip(self.money, self.team, self.completed + 1, self.mode, losses)
                ev.append(("freezetime", {"round": self.completed + 1, "money": self.money, "team": self.team, "tip": tip}))
            elif phase == "live":
                ev.append(("live", {}))
            elif phase == "over":
                rec = {"n": len(self.rounds) + 1, "won": bool(self.team) and rd.get("win_team") == self.team,
                       "kills": int(self.own_round.get("round_kills") or 0),
                       "hs": int(self.own_round.get("round_killhs") or 0),
                       "dmg": int(self.own_round.get("round_totaldmg") or 0),
                       "died": int(self.stats.get("deaths") or 0) > self._deaths_before}
                self.rounds.append(rec)
                ev.append(("round_over", rec))
        bomb = rd.get("bomb") or ""
        if bomb != self.bomb:
            self.bomb = bomb
            if bomb in ("planted", "defused", "exploded"):
                ev.append((f"bomb_{bomb}", {}))
        if mp and mph != self.map_phase:
            self.map_phase = mph
            if mph == "gameover" and not self.finished:
                self.finished = True
                self.last_match = self.summary_data()
                ev.append(("match_over", self.last_match))
            elif mph == "live":
                ev.append(("match_live", {}))
        return ev

    # ------------------------------------------------------------------ итоги
    def summary_data(self) -> dict:
        mine, theirs = (self.ct, self.t) if self.team == "CT" else (self.t, self.ct)
        s = self.stats
        best = max(self.rounds, key=lambda r: (r["kills"], r["dmg"]), default=None)
        return {"map": self.map, "mode": self.mode, "mine": mine, "theirs": theirs, "finished": self.finished,
                "kills": int(s.get("kills") or 0), "deaths": int(s.get("deaths") or 0),
                "assists": int(s.get("assists") or 0), "mvps": int(s.get("mvps") or 0),
                "hs": sum(r["hs"] for r in self.rounds), "dmg": sum(r["dmg"] for r in self.rounds),
                "rounds": list(self.rounds), "best": best}

    def current(self) -> dict | None:
        if self.map and (self.rounds or self.stats):
            return self.summary_data()
        return self.last_match


def summary_text(d: dict | None) -> str:
    if not d:
        return "Пока нечего рассказать: сыграй матч в CS2 — я посчитаю."
    k, de, a = d["kills"], d["deaths"], d["assists"]
    if d.get("finished"):
        res = "победа" if d["mine"] > d["theirs"] else "поражение" if d["mine"] < d["theirs"] else "ничья"
        head = f"{map_title(d['map'])}: {res} {d['mine']}:{d['theirs']}."
    else:
        head = f"{map_title(d['map'])}, пока {d['mine']}:{d['theirs']}."
    parts = [head, f"Ты {k}–{de}, ассистов {a}."]
    n = len(d["rounds"])
    if k:
        parts.append(f"Хедшотов {d['hs']} из {k} — {round(100 * d['hs'] / k)}%.")
    if n and d["dmg"]:
        parts.append(f"Урон в среднем {round(d['dmg'] / n)} за раунд.")
    if d["mvps"]:
        parts.append(f"MVP: {d['mvps']}.")
    b = d.get("best")
    if b and b["kills"] >= 2:
        parts.append(f"Лучший раунд — {b['n']}-й: {kills_word(b['kills'])}.")
    if n >= 5:
        kd = k / max(1, de)
        parts.append("Хорошо отстрелял, чумба." if kd >= 1.2 else "Ровно." if kd >= 0.9 else "Тяжёлая карта. Бывает — следующая наша.")
    return " ".join(parts)


def coach_prompt(question: str, d: dict | None) -> str:
    """Для Claude: «разбери мою игру» — со статистикой по раундам."""
    data = json.dumps(d or {}, ensure_ascii=False)
    return (f"Пользователь играет в Counter-Strike 2 и просит: «{question}». Статистика матча из Game State Integration "
            f"(раунды по порядку: won — выиграли ли раунд, kills/hs/dmg — его киллы, хедшоты, урон, died — умер ли): {data}\n"
            "Дай короткий разбор для озвучки: 3–5 предложений, что получилось, что улучшить, один конкретный совет. "
            "Без markdown и списков.")
