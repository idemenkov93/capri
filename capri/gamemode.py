"""Игровой помощник: Капри сама замечает игру и не мешает ей.

Строгий режим (CS2): Капри прячется и замирает, выгружает модели из видеокарты (Ollama и Whisper),
останавливает танцы и слежку за музыкой, понижает себе приоритет. Слушает «Капри» как обычно, но фразу
распознаёт vosk на процессоре. Таймер бомбы — заранее записанными фразами. Вопросы к Claude посреди раунда
ждут конца раунда. Подсказки по экономике — в закупке. После матча — итог.

Мягкий режим (остальные игры): Капри прячется, выгружает локальную модель, вопросы идут в Claude.

Игру закрыл — всё возвращается, как было: аватар, танцы, модели, обычный приоритет.
"""
from __future__ import annotations

import json
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import cs2, game, gpu


class GameMode(QObject):
    gsi = Signal(object)          # сообщения CS2 приходят в потоке сервера → обрабатываем в главном

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = dict(app.cfg.get("games") or {})
        self.watcher = game.Watcher(self.cfg)
        self.game: game.GameInfo | None = None
        self.tracker = cs2.Tracker()
        self.server: cs2.Server | None = None
        self.gpu_stats: gpu.Stats | None = None
        self._bomb: list[QTimer] = []
        self._deferred: str | None = None
        self._was: dict = {}
        self.gsi.connect(self._on_gsi)
        self._poll_timer = QTimer(interval=2000, timeout=self._poll)
        self._gpu_timer = QTimer(interval=30_000, timeout=self._gpu_sample)
        if self.cfg.get("enabled", True):
            self._poll_timer.start()
            threading.Thread(target=self._install_cfg, daemon=True).start()   # CS2 прочитает файл при следующем запуске

    # ------------------------------------------------------------------ настройки
    def pref(self, key: str, default=True):
        return self.app.prefs.get(key, default)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True)) and bool(self.pref("game_assist"))

    @property
    def active(self) -> bool:
        return self.game is not None

    @property
    def strict(self) -> bool:
        return bool(self.game) and self.game.mode == "strict"

    @property
    def tournament(self) -> bool:
        return bool(self.pref("cs2_tournament", False))

    def set_pref(self, key: str, on: bool) -> str:
        self.app.prefs.set(key, on)
        if key == "game_assist" and not on and self.game:
            self.leave(self.game, quiet=True)
        if key == "cs2_tournament" and on:
            self._cancel_bomb()
        return game.TOGGLE_TEXT.get(key, ("Готово.", "Готово."))[bool(on)]

    # ------------------------------------------------------------------ вход и выход
    def _poll(self):
        if not self.enabled and not self.game:
            return
        try:
            r = self.watcher.poll()
        except Exception:  # noqa: BLE001
            from .log import log
            log.exception("игры: опрос")
            return
        if not r:
            return
        kind, g = r
        if kind == "start" and self.enabled:
            self.enter(g)
        elif kind == "stop" and self.game:
            self.leave(g)

    def enter(self, g: game.GameInfo) -> None:
        from .log import log
        a = self.app
        self.game = g
        log.info("игра: %s (%s), режим %s", g.name, g.exe, g.mode)
        self.gpu_stats = gpu.Stats()
        self._gpu_sample()
        self._gpu_timer.start()
        self._was = {"avatar": a.avatar.isVisible(), "chat": a.chat.isVisible(), "dance": a.music is not None}
        # окно поверх игры ломает быстрый вывод кадров (independent flip) — прячемся и не рисуем
        a.avatar.hide()
        a.avatar._tick.stop()
        if getattr(a.avatar, "sprites", None):
            a.avatar.sprites.pause_all()
        if a.chat.isVisible():
            a.chat.hide()
        # видеопамять — игре: локальная модель уходит из видеокарты
        threading.Thread(target=a.agent.llm.unload, daemon=True).start()
        a.models_unloaded = False
        if g.mode == "strict":
            a.stop_dancing()
            a.talking_media = False          # слежка за музыкой остановлена — «видео в колонках» больше не считаем
            a._apply_wake_mode()
            if a.wake:
                a.wake.strict = True             # Whisper выгружен — «Капри» засчитываем только при уверенном распознавании
            if a.voice:
                a.voice.stt.unload()
                from .config import expand
                from .voice.vosk_stt import VoskTranscriber
                stt = VoskTranscriber(expand(a.cfg["voice"]["models_dir"]), getattr(a.wake, "model", None))
                a.voice.stt_override = stt
                threading.Thread(target=self._safe(stt.warmup), daemon=True).start()
            game.set_low_priority(True)
        if g.is_cs2:
            self._start_cs2(g)
        mode = ("строгий режим: я спряталась, видеокарта и процессор — игре. Зови «Капри» для команд."
                if g.mode == "strict" else "я спряталась и освободила видеокарту. Зови «Капри», если что.")
        a.chat.add_system(f"🎮 {g.name} — {mode}")

    def leave(self, g: game.GameInfo, quiet: bool = False) -> None:
        from .log import log
        a = self.app
        log.info("игра закрыта: %s", g.name)
        self._gpu_timer.stop()
        self._cancel_bomb()
        if self.server:
            self.server.stop()
            self.server = None
        game.set_low_priority(False)
        self.game = None
        self.watcher.current = None
        if a.voice:
            a.voice.stt_override = None
        a._apply_wake_mode()
        a.avatar._tick.start()
        if self._was.get("avatar", True):
            a.avatar.show()
        if self._was.get("dance") and a.music is None:
            a.start_dancing()
        # модели обратно — ты уже на рабочем столе
        from .app import Warmup
        a.warmup = Warmup(a.agent)
        a.warmup.start()
        if a.voice:
            a.voice.warmup()
        if quiet:
            return
        parts = [f"{g.name}: {game.human_duration(time.monotonic() - g.started)}."]
        if self.gpu_stats and (s := self.gpu_stats.summary()):
            parts.append(s)
        text = " ".join(parts)
        a.chat.add_capri(f"🎮 {text}", "rules")
        if self.gpu_stats and self.gpu_stats.hot and a.voice and a.cfg["voice"]["speak"]:
            a.voice.speak(text)
        self._break_if_due()

    def shutdown(self) -> None:
        if self.server:
            self.server.stop()
        game.set_low_priority(False)

    # ------------------------------------------------------------------ CS2
    def _token(self) -> str:
        tok = self.app.prefs.get("cs2_token")
        if not tok:
            tok = cs2.new_token()
            self.app.prefs.set("cs2_token", tok)
        return tok

    def _install_cfg(self, exe_path: str | None = None):
        try:
            return cs2.install(self._token(), int(self.cfg.get("cs2_port", cs2.DEFAULT_PORT)), exe_path)
        except Exception:  # noqa: BLE001
            from .log import log
            log.exception("cs2: файл не положила")
            return [], []

    def _start_cs2(self, g: game.GameInfo) -> None:
        a = self.app
        written, ok = self._install_cfg(g.path)
        if written and not ok:
            a.chat.add_system("CS2: положила файл Game State Integration. Перезапусти CS2 один раз — "
                              "тогда заработают таймер бомбы и статистика.")
        port = int(self.cfg.get("cs2_port", cs2.DEFAULT_PORT))
        try:
            self.server = cs2.Server(port, self._token(), self.gsi.emit)
            self.server.start()
        except OSError as e:
            self.server = None
            a.chat.add_system(f"CS2: порт {port} занят ({e}). Поменяй games.cs2_port в config.yaml.")
        if a.voice:
            a.voice.precache({**cs2.CALLOUTS, "later": "Отвечу после раунда."})

    def _on_gsi(self, payload: dict) -> None:
        for kind, data in self.tracker.update(payload):
            self.on_event(kind, data)

    def on_event(self, kind: str, data: dict) -> None:
        a = self.app
        loud = not self.tournament
        if kind == "bomb_planted":
            self._cancel_bomb()
            if loud and self.pref("cs2_bomb_timer"):
                self._say("planted")
                c4 = float(self.cfg.get("c4_time", cs2.C4_TIME))
                late = float(self.cfg.get("gsi_delay", 0.3))   # сообщение от игры приходит чуть позже установки
                for key, left in cs2.CALLOUT_AT:
                    t = QTimer(self, singleShot=True, timeout=lambda k=key: self._say(k))
                    t.start(max(0, int((c4 - left - late) * 1000)))
                    self._bomb.append(t)
        elif kind in ("bomb_defused", "bomb_exploded"):
            self._cancel_bomb()
        elif kind == "round_over":
            self._cancel_bomb()
            self._run_deferred()
        elif kind == "freezetime":
            self._cancel_bomb()
            if loud and self.pref("cs2_econ") and data.get("tip") and a.voice and a.cfg["voice"]["speak"]:
                a.voice.speak(data["tip"])
            self._run_deferred()
        elif kind == "match_over":
            text = cs2.summary_text(data)
            a.chat.add_capri(f"🏁 {text}", "rules")
            if a.voice and a.cfg["voice"]["speak"] and loud:
                a.voice.speak(text)
            self._save_match(data)
            self._break_if_due()

    def _say(self, key: str) -> None:
        if self.app.voice:
            self.app.voice.play_cached(key, cs2.CALLOUTS.get(key, ""))

    def _cancel_bomb(self) -> None:
        for t in self._bomb:
            t.stop()
        self._bomb.clear()

    def _save_match(self, d: dict) -> None:
        from .config import expand
        try:
            p = expand("data/cs2_matches.jsonl")
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": time.strftime("%Y-%m-%d %H:%M"), **d}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ вопросы посреди раунда
    def busy_round(self) -> bool:
        """Идёт живой раунд CS2 в строгом режиме — с долгими вопросами подождём."""
        return self.strict and bool(self.game and self.game.is_cs2) and self.tracker.live

    def defer(self, text: str) -> None:
        self._deferred = text

    def _run_deferred(self) -> None:
        if self._deferred:
            text, self._deferred = self._deferred, None
            QTimer.singleShot(300, lambda: self.app.on_message(text))

    # ------------------------------------------------------------------ прочее
    def _gpu_sample(self) -> None:
        if self.gpu_stats is not None:
            try:
                self.gpu_stats.add(gpu.read())
            except Exception:  # noqa: BLE001
                pass

    def _break_if_due(self) -> None:
        a = self.app
        if getattr(a, "_break_due", False):
            a._break_due = False
            a.timer_fired(a.break_text(int(a.prefs.get("break_every") or 0), played=True), prefix="")

    def context(self) -> str:
        """Пара слов об игре для Claude."""
        if not self.game:
            return ""
        s = f"Пользователь сейчас играет в {self.game.name}."
        if self.game.is_cs2 and self.tracker.map:
            t = self.tracker
            mine, theirs = (t.ct, t.t) if t.team == "CT" else (t.t, t.ct)
            s += f" CS2: карта {cs2.map_title(t.map)}, счёт {mine}:{theirs}, сторона {t.team or '?'}, деньги {t.money}."
        return s

    @staticmethod
    def _safe(fn):
        def run():
            try:
                fn()
            except Exception:  # noqa: BLE001
                from .log import log
                log.exception("игры: фон")
        return run
