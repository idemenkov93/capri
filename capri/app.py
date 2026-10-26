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
from .config import expand, load_config
from .i18n import t
from .state import State
from .ui.avatar import Avatar
from .ui.chat import ChatWindow


class Worker(QThread):
    status = Signal(str)
    token = Signal(str, str)
    done = Signal(object)

    def __init__(self, agent: Agent, text: str):
        super().__init__()
        self.agent, self.text = agent, text
        self.stop_requested = False

    def run(self):
        try:
            reply = self.agent.handle(self.text, on_status=self.status.emit, on_token=self.token.emit,
                                      should_stop=lambda: self.stop_requested)
        except Exception as e:  # чтобы поток никогда не падал молча
            reply = Reply(f"Что-то сломалось: {e}", route="error")
        self.done.emit(reply)


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
        custom = self._avatar_source(av, "desktop")
        hud_source = self._avatar_source(av, "hud")
        mode = av.get("desktop_mode", "auto")   # auto | sprite | holo | star
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
        # запоминаем последнее «чужое» окно: «напечатай…», «нажми кнопку…» из чата Капри идут туда, а не в чат
        from . import control
        self._fg_timer = QTimer(interval=1000, timeout=control.remember_foreground)
        self._fg_timer.start()

        # грузим модель в видеокарту сразу, пока пользователь ещё ничего не написал
        self.set_status("LOADING")
        self.warmup = Warmup(self.agent)
        self.warmup.finished.connect(lambda: self.set_status("ONLINE"))
        self.warmup.start()

    @staticmethod
    def _avatar_source(av: dict, where: str) -> str:
        """Откуда брать анимации.

        assets/custom/desktop/ — для Капри на рабочем столе (лучше GIF с прозрачным фоном),
        assets/custom/hud/     — для экрана в окне «Personal Link» (подойдут кадры из видео с фоном).
        Если папки нет — берём assets/custom целиком. Путь из config.yaml имеет приоритет.
        """
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
        self.wake = WakeListener(models, self.prefs.get("input_device"), short=self.prefs.get("wake_short", True))
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
        self.wake_up()
        if not self.voice or self.voice.recording:
            log.info("wake пропущен: уже слушаю")
            return
        if self.worker and self.worker.isRunning():
            self.stop_current()          # позвали, пока думала, — бросаем старое и слушаем новое
        if not self.avatar.isVisible():
            self.avatar.show()
        self.voice.quiet()
        self.voice.chime()
        self.voice.listen_once()

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
            menu.addAction(self._check(menu, t("wake_short", L), self.prefs.get("wake_short", True), self.toggle_wake_short))
            menu.addAction(self._check(menu, t("strict_video", L), self.prefs.get("strict_during_video", False), self.toggle_strict_video))
            menu.addAction(self._check(menu, t("speak_on", L), self.cfg["voice"]["speak"], self.toggle_speak))
            menu.addAction(self._check(menu, t("speak_typed", L), self.prefs.get("speak_typed", True),
                                       lambda: self.prefs.set("speak_typed", not self.prefs.get("speak_typed", True))))
            menu.addAction(t("voice_lab", L), self.open_voice_lab)
        am = menu.addMenu(t("attitude", L))
        ag = QActionGroup(am)
        for code in ("soft", "sassy", "rude"):
            act = QAction(t(f"att_{code}", L), am, checkable=True, checked=(code == self.prefs.get("attitude", "sassy")))
            act.triggered.connect(lambda _=False, c=code: self.set_attitude(c))
            ag.addAction(act)
            am.addAction(act)
        menu.addAction(self._check(menu, t("wander", L), self.avatar.wander, self.toggle_wander))
        dm = menu.addMenu(t("dance", L))
        dm.addAction(self._check(dm, t("dance_on", L), self.prefs.get("dance", True), self.toggle_dance))
        dm.addAction(self._check(dm, t("dance_any", L), self.prefs.get("dance_any", False), self.toggle_dance_any))
        menu.addAction(t("hide", L) if self.avatar.isVisible() else t("show", L), self.toggle_avatar)

        lm = menu.addMenu(t("language", L))
        lg = QActionGroup(lm)
        for code, name in (("ru", "Русский"), ("en", "English")):
            act = QAction(name, lm, checkable=True, checked=(code == L))
            act.triggered.connect(lambda _=False, c=code: self.set_language(c))
            lg.addAction(act)
            lm.addAction(act)
        menu.addSeparator()
        menu.addAction(t("quit", L), self.qt.quit)
        return menu

    @staticmethod
    def _check(parent, label: str, checked: bool, slot) -> QAction:
        act = QAction(label, parent, checkable=True, checked=checked)
        act.triggered.connect(lambda *_: slot())
        return act

    def _apply_attitude(self, code: str):
        self.cfg.setdefault("persona", {})["attitude"] = code
        self.cfg["claude_code"]["attitude"] = code

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
        on = not self.prefs.get("wake_short", True)
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
        self.wake.short = self.prefs.get("wake_short", True) and not (talking and strict_video)
        self.wake.strict = talking

    def stop_dancing(self):
        if self.music:
            self.music.stop()
            self.music = None
        self.avatar.dance = 0.0

    def _on_music_level(self, level: float):
        if not self.prefs.get("dance", True):
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
        if self.models_unloaded:
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
        if not self.models_unloaded and idle > self.UNLOAD_AFTER and self.prefs.get("eco_unload", True):
            self.models_unloaded = True
            log.info("режим ожидания: выгружаю модели из памяти")
            import threading
            threading.Thread(target=self.agent.llm.unload, daemon=True).start()
            if self.voice:
                self.voice.stt.unload()

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
        self.chat.set_recording(state == "LISTENING")
        if state == "IDLE":
            self.set_status("ONLINE")
        else:
            self.set_status(state)
        if state == "RECOGNIZING":
            self.chat.add_system("Распознаю…")

    def on_transcribed(self, text: str, lang: str):
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
        from .log import log
        self.wake_up()
        if self.worker and self.worker.isRunning():
            # раньше новый запрос молча терялся; теперь — ставим в очередь (последний побеждает)
            log.info("занята, откладываю: «%s»", text)
            self._queued = text
            self.stop_current()
            return
        log.info("запрос: «%s»", text)
        self.chat.set_busy(True)
        self.worker = Worker(self.agent, text)
        self.worker.status.connect(self.on_status)
        self.worker.token.connect(self.chat.stream)
        self.worker.done.connect(self.on_reply)
        self.worker.start()

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
            self.chat.add_system("Передаю задачу Claude… это может занять минуту.")

    def on_reply(self, reply: Reply):
        from .log import log
        log.info("ответ [%s]: «%s»", reply.route, reply.text[:200].replace("\n", " "))
        self.set_status("ERROR" if reply.route == "error" else "TALKING")
        QTimer.singleShot(2500, lambda: self.state in ("TALKING", "ERROR") and self.set_status("ONLINE"))
        self.chat.end_stream()
        self.chat.add_capri(reply.text, reply.route)
        for act in reply.actions:
            self.run_action(act)
        self.chat.set_busy(False)
        v = self.cfg["voice"]
        if self.voice and v["speak"] and (self.last_input_was_voice or self.prefs.get("speak_typed", True)):
            self.voice.speak(reply.text)
        self.last_input_was_voice = False
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
        if act.kind in ("connect_mail", "connect_calendar", "connect_simple"):
            self.connect_google(act.kind)
            return
        # удаление — всегда с вопросом, даже если подтверждения выключены
        if (act.needs_confirm and self.cfg.get("confirm_actions", True)) or act.kind == "trash":
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

    def timer_fired(self, label: str):
        self.set_status("TALKING")
        self.tray.showMessage("Capri", f"⏰ {label}")
        self.chat.add_capri(f"⏰ Эй! {label}", "rules")
        if self.voice and self.cfg["voice"]["speak"]:
            self.voice.speak(f"Эй! {label}")
        QTimer.singleShot(5000, lambda: self.state == "TALKING" and self.set_status("ONLINE"))

    def run(self) -> int:
        self.qt.aboutToQuit.connect(self.stop_wake)
        self.qt.aboutToQuit.connect(self.stop_dancing)
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


def main() -> int:
    import os

    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    from .log import log, setup
    setup()
    log.info("=== Капри запускается ===")

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
