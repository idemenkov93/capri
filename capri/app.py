"""Сборка приложения: аватар + чат + агент в отдельном потоке."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import actions as A
from .agent import Agent, Reply
from . import skins
from . import __version__
from .config import FROZEN, RESOURCE_ROOT, expand, load_config
from .i18n import t
from .state import State
from .ui.avatar import Avatar
from .ui.chat import ChatWindow


class Worker(QThread):
    status = Signal(str)
    token = Signal(str, str)
    done = Signal(object)

    def __init__(self, agent: Agent, text: str, typed: bool = False):
        super().__init__()
        self.agent, self.text, self.typed = agent, text, typed
        self.stop_requested = False
        self.abandoned = False        # завис и брошен — его ответ уже никому не нужен
        self.started_at = time.monotonic()
        self.phase = ""               # THINKING / CLAUDE — думает модель (это может быть долго и нормально)

    def run(self):
        try:
            reply = self.agent.handle(self.text, on_status=self.status.emit, on_token=self.token.emit,
                                      should_stop=lambda: self.stop_requested, typed=self.typed)
        except Exception as e:  # чтобы поток никогда не падал молча
            reply = Reply(f"Что-то сломалось: {e}", route="error")
        self.done.emit(reply)


class UpdateCheck(QThread):
    found = Signal(str)

    def run(self):
        from . import updates
        info = updates.latest()
        if info and updates.newer(info["version"]):
            self.found.emit(info["version"])


class UpdateDownload(QThread):
    """Скачивание установщика новой версии в фоне (100 МБ — не морозим окно)."""
    progress = Signal(int)
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, info: dict):
        super().__init__()
        self.info = info

    def run(self):
        from . import updates
        try:
            self.ready.emit(str(updates.download(self.info, self.progress.emit)))
        except Exception as e:  # noqa: BLE001 — сеть, диск, неполный файл
            self.failed.emit(str(e)[:200])


class Warmup(QThread):
    def __init__(self, agent: Agent):
        super().__init__()
        self.agent = agent

    def run(self):
        self.agent.llm.warmup()


class CapriApp:
    def __init__(self):
        self.qt = QApplication.instance() or QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.cfg = load_config()
        self.prefs = State(expand("data/state.json"))
        if self.prefs.data.get("speak") is not None:
            self.cfg["voice"]["speak"] = bool(self.prefs.data["speak"])
        self._apply_attitude(self.prefs.get("attitude", "sassy"))
        self.agent = Agent(self.cfg)
        self.wake = None
        # список программ из меню «Пуск» — обновляем в фоне при каждом запуске
        import threading

        def refresh_apps():
            from .log import log
            try:
                log.info("программ в «Пуске»: %s", self.agent.apps.refresh())
            except Exception:  # noqa: BLE001
                log.exception("не удалось прочитать список программ")
        threading.Thread(target=refresh_apps, daemon=True).start()

        av = self.cfg["avatar"]
        skin = self.prefs.get("skin")
        custom = self._avatar_source(av, "desktop", skin)
        hud_source = self._avatar_source(av, "hud", skin)
        mode = av.get("desktop_mode", "auto")   # auto | sprite | holo | star
        if skin == skins.STAR:
            mode = "star"
        holo = mode == "holo" or (mode == "auto" and not custom and bool(hud_source))
        if holo:
            custom = hud_source                  # нет прозрачных спрайтов — гуляет голограммой с клипами
        elif mode == "star":
            custom = ""
        wander = av.get("wander", True) and self.prefs.get("wander", True)
        self.avatar = Avatar(av["size"], custom, av.get("fps", 12), wander=wander, holo=holo)
        self.avatar.menu_builder = self.build_menu
        if self.cfg.get("ui", {}).get("style", "hud") == "hud":
            from .ui.hud import HudWindow
            self.chat = HudWindow(hud_source, av.get("fps", 12))
        else:
            self.chat = ChatWindow()
        self.worker: Worker | None = None

        self.tray = QSystemTrayIcon(QIcon(self.avatar.grab()), self.qt)
        self.tray.setToolTip("Capri")
        self.tray.activated.connect(self._tray_clicked)
        self.tray_menu = QMenu()
        self.tray_menu.aboutToShow.connect(lambda: self.build_menu(None, self.tray_menu))
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

        self.avatar.clicked.connect(self.toggle_chat)
        self.avatar.quit_requested.connect(self.qt.quit)
        self.chat.submitted.connect(self.on_message)
        self.agent.chat_brain = self.prefs.get("chat_brain", "auto")
        self.chat.set_brain(self.agent.chat_brain)
        self.chat.brain_changed.connect(self.set_chat_brain)
        self.chat.quick.connect(self.on_quick)
        QTimer.singleShot(3000, self.refresh_brain_buttons)
        self.chat.stop_requested.connect(self.stop_current)

        self.avatar.place_on_ground()
        self.avatar.show()
        for name, sprites in (("рабочий стол", self.avatar.sprites), ("экран HUD", getattr(getattr(self.chat, "view", None), "sprites", None))):
            if sprites:
                kinds = ", ".join(f"{k}×{n}" for k, n in sprites.kinds().items())
                self.chat.add_system(f"Анимации ({name}): {kinds}")
        self.chat.add_capri(f"Йо, {self.cfg['user_name']}. Я в системе. Чем займёмся?", "rules")

        self.voice = None
        self.last_input_was_voice = False
        self._init_voice()
        self.music = None
        self.talking_media = False
        self.start_dancing()
        # режим ожидания: через несколько минут без дела — меньше кадров, реже опросы, модели выгружаются из памяти
        import time as _t
        self._last_active = _t.monotonic()
        self.eco = False
        self.models_unloaded = False
        self._eco_timer = QTimer(interval=15_000, timeout=self._eco_check)
        self._eco_timer.start()
        if self.prefs.get("break_every"):
            self.set_break_reminder(int(self.prefs.get("break_every")))   # режим работы пережил перезапуск
        # игровой помощник: сам замечает игру (CS2 — строгий режим) и не мешает ей
        from .gamemode import GameMode
        self.game = GameMode(self)
        self.agent.game = self.game
        # запоминаем последнее «чужое» окно: «напечатай…», «нажми кнопку…» из чата Капри идут туда, а не в чат
        from . import control
        self._fg_timer = QTimer(interval=1000, timeout=control.remember_foreground)
        self._fg_timer.start()

        self._update_uplink_label()
        # грузим модель в видеокарту сразу, пока пользователь ещё ничего не написал
        self.set_status("LOADING")
        self.warmup = Warmup(self.agent)
        self.warmup.finished.connect(lambda: self.set_status("ONLINE"))
        self.warmup.start()
        # волна 2: окно у курсора (буфер, перевод, письма) и глобальные горячие клавиши
        from .textui import TextOps
        self.text = TextOps(self)
        hk = self.cfg.get("hotkeys") or {}
        from .hotkeys import HotkeyPoller
        self.hotkeys = HotkeyPoller(hk.get("translate", "Ctrl+Shift+T"), hk.get("dictate", "Ctrl+Win"))
        self.hotkeys.translate.connect(self.text.translate_selection)
        self.hotkeys.dictate.connect(self.text.dictate)
        self.hotkeys.dictate_cancel.connect(self.text.dictate_cancel)
        if hk.get("enabled", True):
            self.hotkeys.start()
        # напоминания на дату (переживают перезапуск): data/state.json → reminders
        self._rem_timer = QTimer(interval=30_000, timeout=self._check_reminders)
        self._rem_timer.start()
        # железо — для меню моделей (быстро, но не в главном потоке)
        self.hw = None
        threading.Thread(target=self._detect_hw, daemon=True).start()
        # расход ресурсов раз в минуту → data/usage.csv («сколько ты ешь ресурсов», отчёт для разработчика)
        from . import usage
        self.usage = usage.Recorder(expand("data/usage.csv"),
                                    game=lambda: self.game.game.name if self.game.active else "",
                                    ollama_url=self.cfg["ollama"]["url"])
        self.usage.start()
        if FROZEN:
            # через минуту после запуска — тихо узнать, нет ли новой версии (строчка в чате, без окон)
            self._upd_check = UpdateCheck()
            self._upd_check.found.connect(lambda v: self.dnd_active() or self.chat.add_system(
                t("upd_avail", self.lang).format(v=v)))
            QTimer.singleShot(60_000, self._upd_check.start)

    @staticmethod
    def _avatar_source(av: dict, where: str, skin: str | None = None) -> str:
        """Откуда брать анимации.

        Выбранный в меню скин (папка в skins/) — главнее всего.
        assets/custom/desktop/ — для Капри на рабочем столе (лучше GIF с прозрачным фоном),
        assets/custom/hud/     — для экрана в окне «Personal Link» (подойдут кадры из видео с фоном).
        Если папки нет — берём assets/custom целиком. Путь из config.yaml имеет приоритет над assets/custom.
        """
        if (chosen := skins.source(skin, where)) is not None:
            return chosen
        key = "desktop_images" if where == "desktop" else "hud_images"
        if av.get(key):
            return str(expand(av[key]))
        if av.get("custom_image"):
            return str(expand(av["custom_image"]))

        def has_images(folder):
            return folder.is_dir() and any(p.suffix.lower() in (".gif", ".png", ".webp") for p in folder.rglob("*"))

        sub = expand(f"assets/custom/{where}")
        if has_images(sub):
            return str(sub)
        root = expand("assets/custom")
        # общая папка годится, только если в ней лежат картинки не в подпапках desktop/hud
        if root.is_dir() and any(p.suffix.lower() in (".gif", ".png", ".webp") and p.parent == root for p in root.iterdir()):
            return str(root)
        return ""

    # голос ------------------------------------------------------------------
    def _init_voice(self):
        v = self.cfg["voice"]
        if not v.get("enabled", True):
            return
        from .voice.controller import VoiceController, voice_available

        ok, why = voice_available()
        if not ok:
            self.chat.add_system(why + ". Установка: .venv\\Scripts\\python -m pip install -r requirements-voice.txt")
            return
        try:
            self.voice = VoiceController(self.cfg, self.prefs.get("input_device"))
            self.voice.chime_volume = float(self.prefs.get("chime_volume", 0.7))   # в data/state.json можно поменять 0…1
        except Exception as e:  # noqa: BLE001
            self.chat.add_system(f"Голос не запустился: {e}")
            return
        self.chat.mic.show()
        self.chat.mic_clicked.connect(self.voice.toggle_record)
        if hasattr(self.chat, "speak_toggled"):
            self.chat.speak_toggled.connect(self.toggle_speak)
            self.chat.set_speak_icon(v["speak"])
        if hasattr(self.chat, "set_level"):
            self._level_timer = QTimer(interval=50, timeout=lambda: self.chat.set_level(getattr(self.voice.recorder, "level", 0.0)))
            self._level_timer.start()
        self.voice.state.connect(self.on_voice_state)
        self.voice.transcribed.connect(self.on_transcribed)
        self.voice.error.connect(self.chat.add_system)
        shortcut = QShortcut(QKeySequence(v.get("hotkey", "Ctrl+Space")), self.chat)
        shortcut.setContext(Qt.ApplicationShortcut)
        shortcut.activated.connect(self.voice.toggle_record)
        self.chat.add_system(f"🎤 Голос включён: кнопка 🎤 или {v.get('hotkey', 'Ctrl+Space')}. "
                             "Первый раз скачаются голосовые модели (~500 МБ).")
        if self.prefs.get("voice_tune"):
            self.voice.apply_tune(self.prefs.get("voice_tune"))
        self.voice.warmup()
        self.start_wake()

    # «Привет, Капри» ----------------------------------------------------------
    def start_wake(self):
        self.stop_wake()
        if not self.voice or not self.prefs.get("wake_word", True):
            return
        from .voice.wake import WakeListener, wake_available

        models = expand(self.cfg["voice"]["models_dir"])
        ok, _why = wake_available(models)
        if not ok:
            self.chat.add_system(t("wake_missing", self.lang))
            return
        self.wake = WakeListener(models, self.prefs.get("input_device"), short=self.prefs.get("wake_short", False))
        self._apply_wake_mode()
        self.wake.woke.connect(self.on_wake)
        self.wake.failed.connect(lambda e: self.chat.add_system(f"«Привет, Капри»: {e}"))
        self.wake.start()

    def stop_wake(self):
        if self.wake:
            self.wake.stop()
            self.wake = None

    def on_wake(self, heard: str):
        from .log import log
        if not self.voice or self.voice.recording:
            log.info("wake пропущен: уже слушаю")
            return
        audio = getattr(self.wake, "last_audio", None)
        stt = getattr(self.voice, "stt_override", None) or self.voice.stt     # в игре — Whisper на процессоре
        # вторая проверка: Whisper слушает 3 секунды до «Капри». Пишем звук сразу (чтобы не потерять начало команды),
        # а «дзынь» — когда Whisper подтвердил имя. Не подтвердил — молча перестаём слушать.
        # в обычных играх Whisper в видеокарте остаётся — проверяем имя и там (друзья в Discord);
        # в CS2 он выгружен — там строже слушает сам vosk
        strict_game = self.in_game and self.game.strict
        verify = (self.prefs.get("wake_verify", True) and audio is not None and not strict_game
                  and hasattr(stt, "heard_name"))
        self._wake_ok = None if verify else True
        self.voice.quiet()
        self._wake_pending = True
        self.voice.listen_once()
        self._wake_pending = False
        if not verify:
            self._wake_confirmed()
            return
        token = object()
        self._wake_token = token

        def done(result):
            ok, text = result
            if self._wake_token is not token:
                return
            if ok:
                log.info("wake: Whisper подтвердил: «%s»", text)
                self._wake_confirmed()
            else:
                log.info("wake: ложное срабатывание — Whisper услышал «%s»", text)
                self._wake_ok = False
                self.voice.cancel_record()

        def failed(err):
            log.warning("wake: проверка не удалась (%s) — верю vosk", err)
            if self._wake_token is token:
                self._wake_confirmed()
        self.voice._run(lambda: stt.heard_name(audio), done, failed)

    def _listen_for_answer(self):
        if not getattr(self, "_listen_after_speech", False):
            return
        self._listen_after_speech = False
        if not self.voice or self.voice.recording or (self.worker and self.worker.isRunning()):
            return
        from .log import log
        log.info("слушаю ответ на свой вопрос")
        self._follow_up = True
        self.voice.chime()
        self.voice.listen_once()

    def _wake_confirmed(self):
        self._wake_ok = True
        self.wake_up()
        if self.worker and self.worker.isRunning():
            self.stop_current()          # позвали, пока думала, — бросаем старое и слушаем новое
        if not self.avatar.isVisible() and not self.in_game:   # в игре не выскакиваем поверх неё
            self.avatar.show()
        self.voice.chime()

    def call_capri(self):
        """Позвать: показаться, открыть окно и сразу слушать."""
        if not self.avatar.isVisible():
            self.avatar.show()
        if not self.chat.isVisible():
            self.toggle_chat()
        if self.voice and not self.voice.recording:
            self.voice.quiet()
            self.voice.listen_once()

    def be_quiet(self):
        if self.voice:
            self.voice.quiet()
        self.stop_current()
        self.set_status("ONLINE")

    def check_pc(self):
        self.chat.add_system(t("checking", self.lang))
        self.on_message(t("pc_check_cmd", self.lang))

    def toggle_speak(self):
        v = self.cfg["voice"]
        v["speak"] = not v["speak"]
        self.prefs.set("speak", v["speak"])
        if hasattr(self.chat, "set_speak_icon"):
            self.chat.set_speak_icon(v["speak"])
        self.chat.add_system(t("speak_set_on" if v["speak"] else "speak_set_off", self.lang))

    # меню (одно и то же в трее и по правому клику на Капри) ------------------------
    @property
    def lang(self) -> str:
        return self.prefs.get("language", "ru")

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_chat()

    def build_menu(self, parent=None, menu: QMenu | None = None) -> QMenu:
        L = self.lang
        if menu is None:
            menu = QMenu(parent)
        else:
            menu.clear()
        menu.addAction(t("call", L), self.call_capri)
        menu.addAction(t("chat", L), self.open_chat)
        if self.voice:
            menu.addAction(t("speak_now", L), self.voice.toggle_record)
        menu.addAction(t("quiet", L), self.be_quiet)
        menu.addAction(t("pc_check", L), self.check_pc)
        gm = menu.addMenu(t("google", L))
        gm.addAction(t("google_cal", L), lambda: self.connect_google("connect_calendar"))
        gm.addAction(t("google_mail", L), lambda: self.connect_google("connect_mail"))
        gm.addAction(t("google_full", L), lambda: __import__("webbrowser").open(
            "https://github.com/idemenkov93/capri/blob/main/docs/GOOGLE.md"))
        menu.addSeparator()

        if self.voice:
            from .system import input_devices

            mic = menu.addMenu(t("mic", L))
            group = QActionGroup(mic)
            current = self.prefs.get("input_device")
            choices = [(None, t("mic_default", L))] + input_devices()
            for idx, name in choices:
                act = QAction(name, mic, checkable=True, checked=(idx == current))
                act.triggered.connect(lambda _=False, i=idx, n=name: self.set_mic(i, n))
                group.addAction(act)
                mic.addAction(act)
            if len(choices) == 1:
                mic.addAction(t("mic_none", L)).setEnabled(False)
            menu.addAction(self._check(menu, t("wake", L), self.prefs.get("wake_word", True), self.toggle_wake))
            menu.addAction(self._check(menu, t("wake_short", L), self.prefs.get("wake_short", False), self.toggle_wake_short))
            menu.addAction(self._check(menu, t("strict_video", L), self.prefs.get("strict_during_video", False), self.toggle_strict_video))
            menu.addAction(self._check(menu, t("speak_on", L), self.cfg["voice"]["speak"], self.toggle_speak))
            menu.addAction(self._check(menu, t("speak_typed", L), self.prefs.get("speak_typed", False),
                                       lambda: self.prefs.set("speak_typed", not self.prefs.get("speak_typed", False))))
            menu.addAction(t("voice_lab", L), self.open_voice_lab)
        am = menu.addMenu(t("attitude", L))
        ag = QActionGroup(am)
        for code in ("soft", "sassy", "rude", "psycho"):
            act = QAction(t(f"att_{code}", L), am, checkable=True, checked=(code == self.prefs.get("attitude", "sassy")))
            act.triggered.connect(lambda _=False, c=code: self.set_attitude(c))
            ag.addAction(act)
            am.addAction(act)
        am.addSeparator()
        am.addAction(self._check(am, t("slang", L), self.prefs.get("slang", True), self.toggle_slang))
        scm = menu.addMenu(t("scenarios", L))
        try:
            from . import scenarios
            for name, sc in scenarios.load().items():
                if isinstance(sc, dict):
                    phrase = (sc.get("phrases") or [name])[0]
                    scm.addAction(phrase[:1].upper() + phrase[1:], lambda p=phrase: self.on_message(p))
        except Exception:  # noqa: BLE001
            pass
        scm.addSeparator()
        scm.addAction(t("scenarios_edit", L), self.edit_scenarios)
        if self.prefs.get("break_every"):
            scm.addAction(t("breaks_off", L), lambda: self.set_break_reminder(0))
        gm2 = menu.addMenu(t("games", L))
        cs2_on = self.cfg.get("games", {}).get("cs2_assistant", False)
        toggles = [("game_assist", "game_assist", True)]
        if cs2_on:      # таймер бомбы и экономика — только если включены в config.yaml (games.cs2_assistant)
            toggles += [("cs2_bomb_timer", "bomb_timer", True), ("cs2_econ", "econ_tips", True),
                        ("cs2_tournament", "tournament", False)]
        for key, label, default in toggles:
            gm2.addAction(self._check(gm2, t(label, L), self.prefs.get(key, default),
                                      lambda k=key, d=default: self.chat.add_system(self.game.set_pref(k, not self.prefs.get(k, d)))))
        if cs2_on:
            gm2.addSeparator()
            gm2.addAction(t("game_stats", L), lambda: self.on_message("как я сыграл"))
        menu.addAction(self._check(menu, t("wander", L), self.avatar.wander, self.toggle_wander))
        dm = menu.addMenu(t("dance", L))
        dm.addAction(self._check(dm, t("dance_on", L), self.prefs.get("dance", True), self.toggle_dance))
        dm.addAction(self._check(dm, t("dance_any", L), self.prefs.get("dance_any", False), self.toggle_dance_any))
        bm = menu.addMenu(t("brain", L))
        bg = QActionGroup(bm)
        want = self.cfg.get("brain", {}).get("provider", "claude")
        for code in ("claude", "gemini", "ollama"):
            act = QAction(t(f"brain_{code}", L), bm, checkable=True, checked=(code == want))
            act.triggered.connect(lambda _=False, c=code: self.set_brain(c))
            bg.addAction(act)
            bm.addAction(act)
        now = self.agent.provider()
        if now != want:
            bm.addSeparator()
            bm.addAction(t("brain_now", L).format(t(f"brain_{now}", L))).setEnabled(False)
        from .ui import models as models_menu
        models_menu.build(self, menu, t("models", L))
        self._dnd_menu(menu)
        tm = menu.addMenu("📋 Буфер и экран")
        tm.addAction("❓ Что в буфере?", lambda: self.on_message("что в буфере"))
        tm.addAction("🌐 Перевести буфер", lambda: self.on_message("переведи буфер"))
        tm.addAction("✉ Объяснить письмо на экране", lambda: self.on_message("объясни письмо"))
        tm.addAction("📄 Скопировать текст с экрана", lambda: self.on_message("скопируй текст с экрана"))
        tm.addAction("🔳 QR-код из ссылки в буфере", lambda: self.on_message("сделай qr-код"))
        hk = self.cfg.get("hotkeys") or {}
        tm.addSeparator()
        tm.addAction(f"Перевод выделенного: {hk.get('translate', 'Ctrl+Shift+T')}").setEnabled(False)
        tm.addAction(f"Диктовка: держи {hk.get('dictate', 'Ctrl+Win')}").setEnabled(False)
        sm = menu.addMenu(t("skin", L))
        sg = QActionGroup(sm)
        current_skin = self.prefs.get("skin") or ""
        for code, label in [("", t("skin_default", L))] + [(n, n) for n in skins.list_skins()] + [(skins.STAR, t("skin_star", L))]:
            act = QAction(label, sm, checkable=True, checked=(code == current_skin))
            act.triggered.connect(lambda _=False, c=code: self.set_skin(c))
            sg.addAction(act)
            sm.addAction(act)
        sm.addSeparator()
        sm.addAction(t("skin_folder", L), lambda: self._open_folder(skins.skins_dir()))
        sm.addAction(t("anim_folder", L), lambda: self._open_folder(skins.custom_dir()))
        menu.addAction(t("hide", L) if self.avatar.isVisible() else t("show", L), self.toggle_avatar)
        from . import autostart
        if sys.platform == "win32":
            menu.addAction(self._check(menu, t("autostart", L), autostart.enabled(), self.toggle_autostart))
        menu.addAction(t("usage", L), lambda: self.on_message("сколько ты ешь ресурсов"))
        menu.addAction(t("report", L), self.make_report)

        lm = menu.addMenu(t("language", L))
        lg = QActionGroup(lm)
        for code, name in (("ru", "Русский"), ("en", "English")):
            act = QAction(name, lm, checkable=True, checked=(code == L))
            act.triggered.connect(lambda _=False, c=code: self.set_language(c))
            lg.addAction(act)
            lm.addAction(act)
        menu.addSeparator()
        menu.addAction(t("setup", L), self.open_setup)
        if FROZEN:
            menu.addAction(t("updates", L), self.check_updates)
        menu.addAction(t("restart", L), self.restart)
        menu.addAction(t("quit", L), self.qt.quit)
        return menu

    # модели ------------------------------------------------------------------
    def _save_cfg(self, values: dict):
        try:
            from .setup_config import apply
            from .ui.setup_wizard import config_path
            path = config_path()
            if path.exists():
                path.write_text(apply(path.read_text(encoding="utf-8"), values), encoding="utf-8")
        except OSError:
            pass

    def set_model(self, use: str, name: str):
        """use: voice | text. Модель для голоса — сразу в работе; для текста — со следующего письменного вопроса."""
        from . import installer
        o = self.cfg["ollama"]
        if name and name not in installer.installed_models(o["url"]):
            self.chat.add_system(f"Сначала скачаю {name} — потом переключусь.")
            self.pull_model(name, then=(use, name))
            return
        if use == "voice":
            o["model"] = name
            self.agent.llm.model = name
            from . import hardware
            fits = bool(self.hw and name in hardware.MODELS and self.hw.vram_gb >= hardware.MODELS[name][1])
            opts = getattr(self.agent.llm, "options", None)
            if isinstance(opts, dict):
                if fits:
                    opts["num_gpu"] = 99
                else:
                    opts.pop("num_gpu", None)
            self._save_cfg({("ollama", "model"): name})
            self.chat.add_system(f"🎙 Для голоса теперь {name}.")
        else:
            o["text_model"] = name
            self.agent._llm_text = None
            self._save_cfg({("ollama", "text_model"): name})
            self.chat.add_system(f"📝 Для письменных вопросов теперь {name or 'та же модель, что для голоса'}.")

    def pull_model(self, name: str, then: tuple | None = None):
        from .ui.models import ModelPull
        if getattr(self, "_pull", None) and self._pull.isRunning():
            self.chat.add_system("Уже качаю модель — дождись.")
            return
        self.chat.show()
        self.chat.add_system(f"⬇ Качаю {name}…")
        self._pull = ModelPull(self.cfg["ollama"]["url"], name)
        self._pull.progress.connect(self.chat.add_system)
        self._pull.done.connect(lambda n, err: self.chat.add_system(f"Не скачалась {n}: {err}") if err else (
            self.chat.add_system(f"✓ {n} скачана."), then and self.set_model(*then)))
        self._pull.start()

    def delete_model(self, name: str):
        from . import installer
        if not self._confirm(f"Удалить модель {name} с диска? Скачать снова можно в меню «🧠 Модели»."):
            return
        try:
            installer.delete_model(self.cfg["ollama"]["url"], name)
            self.chat.add_system(f"🗑 {name} удалена.")
        except Exception as e:  # noqa: BLE001
            self.chat.add_system(str(e))

    def on_quick(self, text: str):
        """Быстрая кнопка в чате — как будто написал эту фразу (ответ текстом, без озвучки)."""
        self.last_input_was_voice = False
        self.chat.add_user(text)
        self.on_message(text)

    def refresh_brain_buttons(self):
        """Кнопки «Отвечает: …»: недоступных не выбрать, в подсказке — почему и как подключить."""
        a = self.agent
        tm = self.cfg["ollama"].get("text_model") or self.cfg["ollama"].get("model")
        avail = {
            "auto": (True, "Как обычно: простое — локальная модель, сложное — помощник из мастера"),
            "local": (True, f"Только модель на этом компьютере ({tm}), без интернета"),
            "gemini": (a.gemini_ready(), "Только Gemini" if a.gemini_ready()
                       else "Gemini не подключён — скажи «как подключить gemini» (ключ бесплатный)"),
            "claude": (a._claude_ready(), "Только Claude (подписка)" if a._claude_ready()
                       else "Claude не установлен или выключен — «⚙ Мастер настройки»"),
        }
        if hasattr(self.chat, "set_brain_available"):
            self.chat.set_brain_available(avail)
        if not avail.get(a.chat_brain, (True,))[0]:
            self.set_chat_brain("auto")
            self.chat.set_brain("auto")

    def set_chat_brain(self, code: str):
        """Кто отвечает на письменные вопросы в чате (переключатель над полем ввода)."""
        self.agent.chat_brain = code
        self.prefs.set("chat_brain", code)
        tm = self.cfg["ollama"].get("text_model")
        self.chat.add_system({"auto": "Отвечаю как обычно: простое — сама, сложное — помощник из мастера.",
                              "local": "В чате отвечает локальная модель" + (f" ({tm})" if tm else "") + ".",
                              "gemini": "В чате отвечает Gemini.", "claude": "В чате отвечает Claude."}[code])

    def set_brain(self, code: str):
        """Сменить умного помощника сразу, без перезапуска (и записать в config.yaml)."""
        self.cfg.setdefault("brain", {})["provider"] = code
        try:
            from .setup_config import apply
            from .ui.setup_wizard import config_path
            path = config_path()
            if path.exists():
                path.write_text(apply(path.read_text(encoding="utf-8"), {("brain", "provider"): code}), encoding="utf-8")
        except OSError:
            pass
        self._update_uplink_label()
        now = self.agent.provider()
        if now != code:
            self.chat.add_system(t("brain_missing", self.lang).format(t(f"brain_{code}", self.lang), t(f"brain_{now}", self.lang)))

    def _update_uplink_label(self):
        try:
            from .ui import hud
            hud.STATE_LABEL["CLAUDE"] = "UPLINK // " + {"claude": "CLAUDE", "gemini": "GEMINI"}.get(self.agent.provider(), "VISION")
        except Exception:  # noqa: BLE001
            pass

    # внешность, мастер настройки, перезапуск -----------------------------------
    def set_skin(self, code: str):
        if code == (self.prefs.get("skin") or ""):
            return
        self.prefs.set("skin", code)
        if self._confirm(t("restart_ask", self.lang)):
            self.restart()

    # 🔕 не беспокоить и учёба -----------------------------------------------------
    def dnd_active(self) -> bool:
        from . import dnd
        return dnd.active(self.prefs.get("dnd_until") or 0)

    def set_dnd(self, until: float):
        """until — timestamp конца, dnd.FOREVER или 0 (выключить)."""
        from . import dnd
        was = self.dnd_active()
        self.prefs.set("dnd_until", until)
        if not getattr(self, "_dnd_timer", None):
            self._dnd_timer = QTimer(interval=30_000, timeout=self._dnd_check)
        on = self.dnd_active()
        if on:
            self._dnd_timer.start()
            self._wander_before = self.avatar.wander if not was else getattr(self, "_wander_before", self.avatar.wander)
            self.avatar.wander = False                    # не гуляет по экрану
            self.avatar.set_dance(0.0)
            self.tray.setToolTip(f"Capri — 🔕 {dnd.describe(until)}")
            if hasattr(self.chat, "set_dnd_label"):
                self.chat.set_dnd_label(dnd.describe(until))
            self.chat.add_system(f"🔕 Не беспокою {dnd.describe(until)}. Таймеры звенят, «Привет, Капри» работает.")
        else:
            self._dnd_timer.stop()
            if was:
                self.avatar.wander = getattr(self, "_wander_before", self.avatar.wander)
                self.tray.setToolTip("Capri")
                if hasattr(self.chat, "set_dnd_label"):
                    self.chat.set_dnd_label("")
                self.chat.add_system("🔔 «Не беспокоить» закончилось.")

    def _dnd_check(self):
        if not self.dnd_active():
            self.set_dnd(0)

    def _dnd_menu(self, menu: QMenu):
        from . import dnd
        L = self.lang
        on = self.dnd_active()
        dm = menu.addMenu(("🔕 Не беспокоить — " + dnd.describe(self.prefs.get("dnd_until"))) if on else "🔕 Не беспокоить")
        for title, choice in dnd.CHOICES:
            dm.addAction(title, lambda c=choice: self.set_dnd(dnd.until(c)))
        if on:
            dm.addSeparator()
            dm.addAction("🔔 Выключить", lambda: self.set_dnd(0))
        dm.addSeparator()
        st = dm.addMenu("📚 Учёба (не беспокою, в конце спрошу, что выучил)")
        for m in (25, 45, 60, 90):
            st.addAction(f"{m} минут", lambda m=m: self.start_study(m))
        del L

    def start_study(self, minutes: float):
        from . import dnd
        self.set_dnd(dnd.until(minutes))
        self._study_minutes = minutes
        if getattr(self, "_study_timer", None):
            self._study_timer.stop()
        self._study_timer = QTimer(singleShot=True, interval=int(minutes * 60_000), timeout=self.study_done)
        self._study_timer.start()

    def study_done(self):
        """Учёба закончилась: снять 🔕, спросить «что выучил?» и записать ответ в заметки."""
        import time as _t
        minutes = getattr(self, "_study_minutes", 0)
        self.set_dnd(0)
        self.agent.pending = {"kind": "study_note", "at": _t.monotonic(), "minutes": f"{minutes:g}",
                              "no": "Окей, без записи. Отдохни!", "ttl": 600}
        self.timer_fired("Учёба закончилась! Что выучил? Скажи или напиши — запишу в заметки.", prefix="📚 ")
        if self.voice and self.prefs.get("follow_up", True) and not self.in_game:
            self._listen_after_speech = True              # ответ — без «Привет, Капри»

    # волна 2 -------------------------------------------------------------------
    def run_text_action(self, act):
        from PySide6.QtGui import QGuiApplication
        k, p = act.kind, act.params
        if k == "clip_explain":
            self.text.explain_clipboard()
        elif k == "clip_translate":
            self.text.translate_clipboard(p.get("lang", ""))
        elif k == "screen_text":
            self.text.screen_text(bool(p.get("translate")))
        elif k == "letter_explain":
            self.text.explain_letter_on_screen()
        elif k == "letter_reply":
            if p.get("instruction"):
                self.text.reply(p["instruction"])
            else:
                self.text.ask_reply()
        elif k == "qr_make":
            src = QGuiApplication.clipboard().text().strip()
            if not src:
                self.text.popup.show_text("QR-код", "Скопируй ссылку (Ctrl+C) и скажи ещё раз.", copy=False)
                return
            path = self.text.make_qr(src)
            self.text.popup.show_text("QR-код готов — картинка в буфере", f"![qr]({path})\n\n`{src[:120]}`",
                                      copy=False)

    def add_reminder(self, at: float, text: str):
        rem = list(self.prefs.get("reminders") or [])
        rem.append({"at": at, "text": text})
        self.prefs.set("reminders", rem)

    def _check_reminders(self):
        now = time.time()
        rem = list(self.prefs.get("reminders") or [])
        due = [r for r in rem if r.get("at", 0) <= now]
        if not due:
            return
        self.prefs.set("reminders", [r for r in rem if r.get("at", 0) > now])
        for r in due:
            self.timer_fired(r.get("text") or "Напоминание", prefix="⏰ ")

    def _detect_hw(self):
        try:
            from . import hardware
            self.hw = hardware.detect()
        except Exception:  # noqa: BLE001
            self.hw = None

    def toggle_autostart(self):
        from . import autostart
        on = not autostart.enabled()
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self.chat.add_system(f"Не получилось: {e}")
            return
        self.chat.add_system(t("autostart_on" if on else "autostart_off", self.lang))

    def make_report(self):
        """zip с журналом и расходом ресурсов на рабочий стол — друзья присылают его после теста."""
        from . import files, usage
        try:
            path = usage.make_report(expand("data"), files.known_folder("desktop"),
                                     extra=f"Капри {__version__}, помощник: {self.agent.provider()}, "
                                           f"модель: {self.cfg['ollama'].get('model')}")
        except Exception as e:  # noqa: BLE001
            self.chat.add_system(f"Отчёт не собрался: {e}")
            return
        self.chat.add_system(t("report_done", self.lang).format(path.name))
        if sys.platform == "win32":
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(path)])   # noqa: S603,S607

    @staticmethod
    def _open_folder(path):
        import os
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606

    def open_setup(self):
        from .ui.setup_wizard import run_wizard
        if run_wizard(first_run=False) and self._confirm(t("restart_ask", self.lang)):
            self.restart()

    def check_updates(self, quiet: bool = False):
        """Есть новая версия → скачать установщик, поставить тихо, Капри перезапустится сама.
        quiet — фоновая проверка при запуске: только строчка в чате, без окон."""
        from . import updates
        info = updates.latest()
        if quiet:
            if info and updates.newer(info["version"]):
                self.chat.add_system(t("upd_avail", self.lang).format(v=info["version"]))
            return
        if info is None:
            QMessageBox.information(None, "Capri", t("upd_fail", self.lang))
        elif not updates.newer(info["version"]):
            QMessageBox.information(None, "Capri", t("upd_none", self.lang))
        elif not FROZEN or not info.get("asset"):
            # из исходников обновляются через git pull; без установщика в релизе — открываем страницу
            if self._confirm(t("upd_new", self.lang).format(v=info["version"])):
                __import__("webbrowser").open(info["url"])
        elif self._confirm(t("upd_install", self.lang).format(v=info["version"], cur=__version__)):
            self._start_update(info)

    def _start_update(self, info: dict):
        if getattr(self, "_updater", None) and self._updater.isRunning():
            return
        self.chat.show()
        self.chat.add_system(t("upd_progress", self.lang).format(v=info["version"], p=0))
        self._updater = UpdateDownload(info)
        self._updater.progress.connect(lambda p: p % 25 == 0 and 0 < p < 100 and self.chat.add_system(
            t("upd_progress", self.lang).format(v=info["version"], p=p)))
        self._updater.failed.connect(lambda e: self.chat.add_system(t("upd_err", self.lang).format(e=e)))
        self._updater.ready.connect(lambda path: self._install_update(path, info["version"]))
        self._updater.start()

    def _install_update(self, path: str, version: str):
        from pathlib import Path

        from . import updates
        from .log import log
        log.info("обновление: ставлю %s из %s", version, path)
        self.chat.add_system(t("upd_go", self.lang).format(v=version))
        try:
            updates.install(Path(path))
        except OSError as e:
            self.chat.add_system(t("upd_err", self.lang).format(e=e))
            return
        QTimer.singleShot(800, self.qt.quit)        # освобождаем файлы программы — установщик заменит их

    def restart(self):
        """Новая Капри дождётся, пока эта закроется (--wait-pid), и запустится с новыми настройками."""
        import os
        import subprocess
        cmd = [sys.executable] if FROZEN else [sys.executable, "-m", "capri"]
        cmd.append(f"--wait-pid={os.getpid()}")
        try:
            subprocess.Popen(cmd, cwd=None if FROZEN else str(RESOURCE_ROOT), close_fds=True)
        except OSError:
            return
        self.qt.quit()

    def _confirm(self, text: str) -> bool:
        box = QMessageBox(QMessageBox.Question, "Capri", text, QMessageBox.Yes | QMessageBox.No)
        box.setWindowFlag(Qt.WindowStaysOnTopHint)
        return box.exec() == QMessageBox.Yes

    @staticmethod
    def _check(parent, label: str, checked: bool, slot) -> QAction:
        act = QAction(label, parent, checkable=True, checked=checked)
        act.triggered.connect(lambda *_: slot())
        return act

    def _apply_attitude(self, code: str):
        self.cfg.setdefault("persona", {})["attitude"] = code
        self.cfg["claude_code"]["attitude"] = code
        slang = bool(self.prefs.get("slang", True))
        self.cfg["persona"]["slang"] = slang
        self.cfg["claude_code"]["slang"] = slang

    def toggle_slang(self):
        self.prefs.set("slang", not self.prefs.get("slang", True))
        self._apply_attitude(self.prefs.get("attitude", "sassy"))

    def set_attitude(self, code: str):
        self.prefs.set("attitude", code)
        self._apply_attitude(code)
        self.chat.add_system(t("att_set", self.lang, name=t(f"att_{code}", self.lang)))

    def open_voice_lab(self):
        if not self.voice:
            return
        from .ui.voice_lab import VoiceLab

        before = self.voice.tune()

        def play(tune, text, lang):
            self.voice.apply_tune(tune)
            self.voice.say_now(text, lang)

        def saved(tune):
            self.voice.apply_tune(tune)
            self.prefs.set("voice_tune", tune)
            self.chat.add_system(t("voice_saved", self.lang))

        dlg = VoiceLab(before, self.lang, play)
        dlg.saved.connect(saved)
        if dlg.exec() != VoiceLab.Accepted:
            self.voice.apply_tune(before)   # «Отмена» — вернуть как было

    def set_language(self, code: str):
        self.prefs.set("language", code)
        self.chat.add_system(t("lang_set", code))

    def set_mic(self, index, name: str):
        self.prefs.set("input_device", index)
        if self.voice:
            self.voice.set_device(index)
        self.chat.add_system(t("mic_set", self.lang, name=name))
        self.start_wake()

    def toggle_wake(self):
        on = not self.prefs.get("wake_word", True)
        self.prefs.set("wake_word", on)
        if on:
            self.start_wake()
            if self.wake:
                self.chat.add_system(t("wake_on", self.lang))
        else:
            self.stop_wake()
            self.chat.add_system(t("wake_off", self.lang))

    def toggle_strict_video(self):
        self.prefs.set("strict_during_video", not self.prefs.get("strict_during_video", False))
        self._apply_wake_mode()

    def toggle_wake_short(self):
        on = not self.prefs.get("wake_short", False)
        self.prefs.set("wake_short", on)
        self._apply_wake_mode()

    # танцы -----------------------------------------------------------------------
    def start_dancing(self):
        # следим за музыкой всегда: даже без танцев это нужно, чтобы во время видео не реагировать на «Капри» из колонок
        if self.music:
            return
        from .music import MusicWatcher
        self.music = MusicWatcher(any_media=self.prefs.get("dance_any", False), apps=self.agent.apps)
        self.music.level.connect(self._on_music_level)
        self.music.kick.connect(self.avatar.kick)
        self.music.talking.connect(self._on_talking_media)
        self.music.start()

    def _on_talking_media(self, talking: bool):
        """Играет видео/подкаст: там много речи, и «Капри» может послышаться из колонок.
        По умолчанию «Капри» работает и тогда, но засчитывается только при очень уверенном распознавании;
        в меню можно включить строгий режим — во время видео только «Привет, Капри»."""
        from .log import log
        self.talking_media = talking
        self._apply_wake_mode()
        log.info("видео/речь в колонках: %s", talking)

    def _apply_wake_mode(self):
        if not self.wake:
            return
        talking = getattr(self, "talking_media", False)
        strict_video = self.prefs.get("strict_during_video", False)
        self.wake.short = self.prefs.get("wake_short", False) and not (talking and strict_video)
        self.wake.strict = talking

    def stop_dancing(self):
        if self.music:
            self.music.stop()
            self.music = None
        self.avatar.dance = 0.0

    def _on_music_level(self, level: float):
        if not self.prefs.get("dance", True) or self.dnd_active():
            level = 0.0
        # пока сама слушает или говорит — не танцует
        busy = getattr(self, "state", "") in ("LISTENING", "RECOGNIZING", "SPEAKING", "THINKING", "CLAUDE")
        self.avatar.set_dance(0.0 if busy else level)

    def toggle_dance(self):
        self.prefs.set("dance", not self.prefs.get("dance", True))

    def toggle_dance_any(self):
        self.prefs.set("dance_any", not self.prefs.get("dance_any", False))
        if self.music:
            self.music.any_media = self.prefs.get("dance_any", False)

    # режим ожидания ---------------------------------------------------------------
    ECO_AFTER = 3 * 60        # без дела 3 мин — экономим кадры и опросы
    UNLOAD_AFTER = 10 * 60    # 10 мин — выгружаем модели из видеопамяти

    def wake_up(self, *_):
        """Позвали, тыкнули, написали — выходим из ожидания и заранее грузим модели, пока ты говоришь."""
        import time as _t
        from .log import log
        self._last_active = _t.monotonic()
        if self.eco:
            self.eco = False
            self.avatar.eco = False
            if self.music:
                self.music.slow = False
            log.info("режим ожидания: выход")
        if self.models_unloaded and not self.in_game:     # в игре модели не грузим — видеокарта занята
            self.models_unloaded = False
            log.info("загружаю модели обратно")
            self.warmup = Warmup(self.agent)
            self.warmup.start()
            if self.voice:
                self.voice.warmup()

    def _eco_check(self):
        import time as _t
        from .log import log
        idle = _t.monotonic() - self._last_active
        busy = (self.worker and self.worker.isRunning()) or (self.voice and (self.voice.recording or self.voice._speaking))
        if busy or getattr(self, "state", "ONLINE") not in ("ONLINE", "ERROR"):
            self._last_active = _t.monotonic()
            return
        if not self.eco and idle > self.ECO_AFTER:
            self.eco = True
            self.avatar.eco = True
            if self.music:
                self.music.slow = True
            log.info("режим ожидания: вход (меньше кадров и опросов)")
        if not self.models_unloaded and idle > self.UNLOAD_AFTER and self.prefs.get("eco_unload", True) and not self.in_game:
            self.models_unloaded = True
            log.info("режим ожидания: выгружаю модели из памяти")
            import threading
            threading.Thread(target=self.agent.llm.unload, daemon=True).start()
            if self.voice:
                self.voice.stt.unload()

    @property
    def in_game(self) -> bool:
        return bool(getattr(self, "game", None) and self.game.active)

    def toggle_wander(self):
        self.avatar._toggle_wander()
        self.prefs.set("wander", self.avatar.wander)

    def toggle_avatar(self):
        self.avatar.setVisible(not self.avatar.isVisible())

    def on_voice_state(self, state: str):
        from .log import log
        log.debug("голос: %s", state)
        if self.wake:
            self.wake.paused = state != "IDLE"   # пока сама слушает или говорит — фразу не ищем
        if state == "IDLE" and getattr(self, "_listen_after_speech", False):
            QTimer.singleShot(250, self._listen_for_answer)      # договорила вопрос — слушаем ответ
        if state == "LISTENING" and not getattr(self, "_wake_pending", False):
            self._wake_ok = True                 # слушаю по кнопке 🎤 / горячей клавише — проверять нечего
        if self.prefs.get("duck_music", True) and self.voice:
            if not hasattr(self, "ducker"):
                from .voice.duck import Ducker
                self.ducker = Ducker()
            if state == "LISTENING":
                self.ducker.duck()               # музыка тише, пока слушаю, — иначе не слышу, где ты договорил
            else:
                self.ducker.restore()
        self.chat.set_recording(state == "LISTENING")
        if state == "IDLE":
            self.set_status("ONLINE")
        else:
            self.set_status(state)
        if state == "RECOGNIZING":
            self.chat.add_system("Распознаю…")

    def on_transcribed(self, text: str, lang: str):
        if getattr(self, "text", None) and self.text._dictating:
            self.text.paste(text)                    # диктовка (Ctrl+Win): печатаем туда, где курсор
            return
        if getattr(self, "_wake_ok", True) is False:
            return                               # ложное «Капри» — не отвечаем
        follow_up, self._follow_up = getattr(self, "_follow_up", False), False
        if not text and follow_up:
            self.agent.pending = None            # на свой вопрос ответа не дождалась — молча забываем
            return
        if not text:
            self.chat.add_system("Ничего не расслышала. Попробуй ещё раз.")
            return
        self.last_input_was_voice = True
        self.chat.add_user(f"🎤 {text}")
        self.on_message(text)

    # чат -------------------------------------------------------------------
    def open_chat(self):
        if not self.chat.isVisible():
            self.toggle_chat()
        else:
            self.chat.raise_()
            self.chat.activateWindow()
            self.chat.input.setFocus()

    def toggle_chat(self):
        self.wake_up()
        if self.chat.isVisible():
            self.chat.hide()
            return
        a = self.avatar.frameGeometry()
        self.chat.move(max(0, a.left() - self.chat.width() - 10), max(0, a.bottom() - self.chat.height()))
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.input.setFocus()

    def on_message(self, text: str):
        import re as _re

        from .log import log
        self.wake_up()
        if self.worker and self.worker.isRunning():
            limit = self.STUCK_AFTER_BRAIN if self.worker.phase in ("THINKING", "CLAUDE") else self.STUCK_AFTER
            if time.monotonic() - self.worker.started_at > limit:
                # старый запрос висит (плеер или программа не отвечает) — бросаем его и выполняем новый
                log.warning("запрос «%s» висит %.0f с — бросаю его", self.worker.text, time.monotonic() - self.worker.started_at)
                self.worker.abandoned = True
                self.worker.stop_requested = True
                self._stale_workers = [w for w in getattr(self, "_stale_workers", []) if w.isRunning()] + [self.worker]
                self.worker = None
            else:
                # раньше новый запрос молча терялся; теперь — ставим в очередь (последний побеждает)
                log.info("занята, откладываю: «%s»", _re.sub(r"AIza[\w-]{10,}|AQ\.[\w.-]{10,}", "(ключ скрыт)", text))
                self._queued = text
                self.stop_current()
                return
        log.info("запрос: «%s»", _re.sub(r"AIza[\w-]{10,}|AQ\.[\w.-]{10,}", "(ключ скрыт)", text))
        self.chat.set_busy(True)
        self.worker = Worker(self.agent, text, typed=not self.last_input_was_voice)
        self.worker.status.connect(self.on_status)
        self.worker.token.connect(self.chat.stream)
        w = self.worker
        w.status.connect(lambda st, w=w: setattr(w, "phase", st))
        w.done.connect(lambda reply, w=w: None if w.abandoned else self.on_reply(reply))
        self.worker.start()

    STUCK_AFTER = 12          # секунд: быстрые команды (музыка, громкость, окна) дольше не идут — значит, зависла
    STUCK_AFTER_BRAIN = 150   # модель или Claude думают — ждём дольше

    def stop_current(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop_requested = True

    def set_status(self, status: str):
        self.state = status
        self.avatar.set_status(status)
        if hasattr(self.chat, "set_status"):
            self.chat.set_status(status)

    def on_status(self, status: str):
        self.set_status(status)
        if status == "CLAUDE":
            who = {"claude": "Claude", "gemini": "Gemini"}.get(self.agent.provider(), "локальной модели со зрением")
            self.chat.add_system(f"Передаю задачу {who}… это может занять минуту.")

    def on_reply(self, reply: Reply):
        from .log import log
        if reply.route == "ignored":
            log.info("в игре: не вопрос и не команда — молчу (наверное, это разговор с друзьями)")
            self.chat.end_stream()
            self.chat.set_busy(False)
            self.set_status("ONLINE")
            self.last_input_was_voice = False
            return
        log.info("ответ [%s]: «%s»", reply.route, reply.text[:200].replace("\n", " "))
        self.set_status("ERROR" if reply.route == "error" else "TALKING")
        QTimer.singleShot(2500, lambda: self.state in ("TALKING", "ERROR") and self.set_status("ONLINE"))
        self.chat.end_stream()
        self.chat.add_capri(reply.text, reply.route)
        for act in reply.actions:
            self.run_action(act)
        self.chat.set_busy(False)
        v = self.cfg["voice"]
        # спросила («На сколько?», «Включить?») — после своей фразы сама слушает ответ, без «Капри»
        self._listen_after_speech = bool(reply.ask and self.voice and self.last_input_was_voice
                                         and self.prefs.get("follow_up", True) and not self.in_game)
        if self.voice and v["speak"] and (self.last_input_was_voice or self.prefs.get("speak_typed", False)):
            if any(a.kind == "game_defer" for a in reply.actions):
                self.voice.play_cached("later", reply.text)     # посреди раунда — записанной фразой, без сети
            else:
                self.voice.speak(reply.text)
        self.last_input_was_voice = False
        if self._listen_after_speech and not (self.voice and self.voice._speaking):
            QTimer.singleShot(300, self._listen_for_answer)      # озвучка выключена — слушаем сразу
        if getattr(self, "_queued", None):
            text, self._queued = self._queued, None
            QTimer.singleShot(0, lambda: self.on_message(text))

    # действия --------------------------------------------------------------
    def connect_google(self, what: str):
        """Простое подключение: окошко со ссылкой/паролем (в главном потоке — это окна)."""
        from . import google_simple as S
        from .ui.connect_dialogs import ask_calendar, ask_mail
        parent = self.chat if self.chat.isVisible() else None
        if what in ("connect_calendar", "connect_simple"):
            def check_cal(url):
                if not S.valid_ical_url(url):
                    return "Это не та ссылка: нужна «Секретный адрес в формате iCal» (начинается с https://calendar.google.com/calendar/ical/ и кончается на .ics)."
                try:
                    import datetime as dt
                    S.fetch_agenda(url, dt.date.today(), dt.datetime.now().astimezone().tzinfo)
                except Exception as e:  # noqa: BLE001
                    return f"Не получилось открыть календарь: {str(e)[:120]}"
                return None
            if url := ask_calendar(parent, check_cal):
                S.set_secret("ical_url", url)
                self.chat.add_capri("Календарь подключён. Спроси: «что у меня завтра?»", "rules")
        if what in ("connect_mail", "connect_simple"):
            def check_mail(addr, pw):
                if "@" not in addr or len(pw.replace(" ", "")) != 16:
                    return "Нужна почта и пароль приложения — ровно 16 букв (пробелы не важны)."
                try:
                    S.unread_imap(addr, pw, limit=1)
                except Exception as e:  # noqa: BLE001
                    return f"Google не пустил: {str(e)[:120]}. Проверь почту и пароль приложения."
                return None
            if res := ask_mail(parent, check_mail):
                S.set_secret("gmail_address", res[0])
                S.set_secret("gmail_app_password", res[1])
                self.chat.add_capri("Почта подключена. Спроси: «есть новые письма?»", "rules")

    def run_action(self, act: A.Action):
        if act.kind == "quiet":
            self.be_quiet()
            return
        if act.kind == "break_reminder":
            self.set_break_reminder(int(act.params.get("minutes") or 0))
            return
        if act.kind == "game_defer":
            self.game.defer(act.params["text"])
            return
        if act.kind == "check_updates":
            self.check_updates()
            return
        if act.kind in ("clip_explain", "clip_translate", "screen_text", "letter_explain", "letter_reply", "qr_make"):
            self.run_text_action(act)
            return
        if act.kind == "dnd":
            from . import dnd
            ch = act.params.get("choice")
            self.set_dnd(dnd.until(ch) if ch else 0)
            return
        if act.kind == "study":
            self.start_study(float(act.params["minutes"]))
            return
        if act.kind == "make_report":
            self.make_report()
            return
        if act.kind == "set_attitude":
            self.set_attitude(act.params["code"])
            return
        if act.kind == "game_set":
            self.game.set_pref(act.params["key"], bool(act.params["on"]))   # что сделала — уже сказала в ответе
            return
        if act.kind in ("connect_mail", "connect_calendar", "connect_simple"):
            self.connect_google(act.kind)
            return
        # удаление — всегда с вопросом, даже если подтверждения выключены
        if (act.needs_confirm and self.cfg.get("confirm_actions", True)) or act.kind in ("trash", "empty_bin") or \
                (act.kind == "power" and act.needs_confirm):      # выключить/перезагрузить — всегда с вопросом
            # окно подтверждения всегда поверх Капри и чата — раньше оно пряталось сзади, и казалось, что всё зависло
            box = QMessageBox(QMessageBox.Question, "Capri", f"{act.description}?",
                              QMessageBox.Yes | QMessageBox.No, self.chat if self.chat.isVisible() else None)
            box.setWindowFlag(Qt.WindowStaysOnTopHint)
            box.raise_()
            box.activateWindow()
            if box.exec() != QMessageBox.Yes:
                self.chat.add_system("Отменено.")
                return
        if act.kind == "timer":
            ms = int(act.params["minutes"] * 60_000)
            label = act.params.get("label") or "Время вышло!"
            QTimer.singleShot(ms, lambda: self.timer_fired(label))
            return
        try:
            result = A.execute(act, self.cfg)
            self.chat.add_system(result)
        except Exception as e:
            self.chat.add_system(f"Не получилось: {e}")

    def edit_scenarios(self):
        from . import actions as AA
        from . import scenarios
        scenarios.load()                     # создаст файл с примерами, если его ещё нет
        AA.execute(AA.Action("open_file", "Сценарии", {"path": str(scenarios.path())}), self.cfg)

    # напоминание об отдыхе -------------------------------------------------------
    BREAK_IDLE = 10 * 60          # не трогал мышь и клавиатуру 10 минут — значит, отдыхал: отсчёт заново

    def set_break_reminder(self, minutes: int):
        """Каждые minutes минут работы за компьютером — «дельтуй, разомнись». 0 — выключить."""
        import time as _t
        self.prefs.set("break_every", minutes)
        self._work_since = _t.monotonic()
        if minutes and not getattr(self, "_break_timer", None):
            self._break_timer = QTimer(interval=60_000, timeout=self._break_check)
        if minutes:
            self._break_timer.start()
        elif getattr(self, "_break_timer", None):
            self._break_timer.stop()

    def _break_check(self):
        import time as _t

        from . import control
        every = int(self.prefs.get("break_every") or 0)
        if not every:
            return
        if self.dnd_active():
            self._work_since = _t.monotonic()        # 🔕 — не дёргаем; отсчёт после «не беспокоить»
            return
        now = _t.monotonic()
        if control.idle_seconds() >= self.BREAK_IDLE:
            self._work_since = now                  # отошёл от компьютера — это и есть перерыв
            return
        if now - self._work_since >= every * 60:
            self._work_since = now
            if self.in_game:
                self._break_due = True          # посреди игры не дёргаем — скажу после матча или когда закроешь игру
                return
            self.timer_fired(self.break_text(every), prefix="")

    def break_text(self, every: int, played: bool = False) -> str:
        import random
        h = every / 60
        dur = f"{h:g} ч" if every >= 60 else f"{every} мин"
        if played:
            return random.choice((f"Чумба, ты за экраном уже больше {dur}. Перед следующей каткой — встань, вода, разомнись.",
                                  f"Больше {dur} без перерыва. Пять минут отдыха — и в бой.")) if self.prefs.get("slang", True) \
                else f"Ты за компьютером больше {dur}. Перед следующей игрой сделай перерыв."
        return random.choice((
            f"Чумба, ты в деле уже {dur}. Дельтуй минут на десять: вода, окно, спина.",
            f"{dur} без перерыва — даже хрому нужен отдых. Встань, разомнись.",
            f"Перерыв! {dur} за экраном. Пять минут без монитора — глаза спасибо скажут.",
        ) if self.prefs.get("slang", True) else (
            f"Ты за компьютером уже {dur}. Сделай перерыв минут на десять.",
            f"{dur} без перерыва — встань, разомнись, попей воды.",
        ))

    def timer_fired(self, label: str, prefix: str = "⏰ Эй! "):
        self.set_status("TALKING")
        if not self.in_game:                    # всплывашка поверх полноэкранной игры может сбить вывод кадров
            self.tray.showMessage("Capri", label)
        self.chat.add_capri(f"{prefix}{label}", "rules")
        if self.voice and self.cfg["voice"]["speak"]:
            self.voice.speak(f"{prefix.replace('⏰ ', '')}{label}")
        QTimer.singleShot(5000, lambda: self.state == "TALKING" and self.set_status("ONLINE"))

    def run(self) -> int:
        self.qt.aboutToQuit.connect(self.stop_wake)
        self.qt.aboutToQuit.connect(self.stop_dancing)
        self.qt.aboutToQuit.connect(self.game.shutdown)
        self.qt.aboutToQuit.connect(lambda: hasattr(self, "hotkeys") and (self.hotkeys.stop(), self.hotkeys.wait(500)))
        self.qt.aboutToQuit.connect(lambda: hasattr(self, "ducker") and self.ducker.close())
        self._watch_hangs()
        return self.qt.exec()

    def _watch_hangs(self):
        """Если окно «зависнет» дольше 20 с — записать, где стоит каждый поток, в data/hang.log."""
        import faulthandler
        try:
            path = expand("data/hang.log")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._hang_file = open(path, "a", encoding="utf-8")  # noqa: SIM115 — открыт всё время работы
        except OSError:
            return

        def rearm():
            faulthandler.dump_traceback_later(20, exit=False, file=self._hang_file)
        rearm()
        self._hang_timer = QTimer(interval=2000, timeout=rearm)
        self._hang_timer.start()
        self.qt.aboutToQuit.connect(faulthandler.cancel_dump_traceback_later)


INSTANCE_NAME = "capri-desktop-companion"


def _pid_file():
    return expand("data/capri.pid")


def _kill_stale(log) -> None:
    """Старая Капри не отвечает (зависла при выходе и держит «замок» одного экземпляра) — завершаем её."""
    import os
    import subprocess
    try:
        pid = int(_pid_file().read_text().strip())
    except (OSError, ValueError):
        return
    if pid == os.getpid():
        return
    log.warning("старая Капри (PID %s) не отвечает — завершаю её", pid)
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=0x08000000)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _wait_pid(pid: int, timeout: float = 20.0) -> None:
    """Перезапуск: ждём, пока старая Капри закроется (иначе новая решит, что Капри уже запущена)."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if h:
            k32.WaitForSingleObject(h, int(timeout * 1000))
            k32.CloseHandle(h)
        return
    import os
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def main() -> int:
    import os

    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    args = sys.argv[1:]
    if any(a.startswith("--check") for a in args):           # сборка: проверить, что всё упаковалось
        from .selfcheck import run
        return run(next((a.split("=", 1)[1] for a in args if a.startswith("--check=")), None))
    if any(a == "--selftest" for a in args):
        from .selftest import main as selftest
        return selftest() or 0
    if wait := next((a.split("=", 1)[1] for a in args if a.startswith("--wait-pid=")), None):
        _wait_pid(int(wait))

    from .log import log, setup
    setup()
    log.info("=== Капри запускается (%s) ===", "Capri.exe" if FROZEN else "из исходников")

    # Только одна Капри: если она уже запущена — показываем её чат и выходим.
    # Живая Капри отвечает «ok»; если ответа нет — это зависший процесс: завершаем его и запускаемся сами.
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_NAME)
    if probe.waitForConnected(500):
        probe.write(b"show")
        probe.waitForBytesWritten(500)
        if probe.waitForReadyRead(1500) and probe.readAll().data().startswith(b"ok"):
            log.info("Капри уже запущена — показываю её окно")
            return 0
        probe.abort()
        _kill_stale(log)
        time.sleep(1.0)

    qt_app = QApplication.instance() or QApplication(sys.argv)   # держим ссылку, иначе Qt удалит приложение
    qt_app.setApplicationName("Capri")
    try:
        skins.first_run_copy()
    except OSError:
        log.exception("не удалось скопировать анимации")
    from .ui.setup_wizard import needs_setup, run_wizard
    if "--setup" in args or needs_setup():
        log.info("мастер настройки")
        run_wizard(first_run=True)
    try:
        app = CapriApp()
    except Exception:
        log.exception("Капри не смогла запуститься")
        raise
    QLocalServer.removeServer(INSTANCE_NAME)  # хвост от упавшего запуска
    server = QLocalServer()
    server.listen(INSTANCE_NAME)
    try:
        _pid_file().write_text(str(os.getpid()))
    except OSError:
        pass

    def on_connect():
        conn = server.nextPendingConnection()
        if conn:
            conn.write(b"ok")
            conn.flush()
            conn.disconnected.connect(conn.deleteLater)
            if not app.chat.isVisible():
                app.toggle_chat()
            app.chat.raise_()
            app.chat.activateWindow()

    server.newConnection.connect(on_connect)
    app.instance_server = server
    code = app.run()
    log.info("=== Капри выключается ===")
    server.close()
    try:
        _pid_file().unlink()
    except OSError:
        pass
    # выходим сразу: фоновые потоки (микрофон, звук, сеть) не должны держать процесс «зомби» после выхода
    for h in log.handlers:
        h.flush()
    os._exit(code)
