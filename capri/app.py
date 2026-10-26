"""Сборка приложения: аватар + чат + агент в отдельном потоке."""
from __future__ import annotations

import sys

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import actions as A
from .agent import Agent, Reply
from .config import expand, load_config
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
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.cfg = load_config()
        self.agent = Agent(self.cfg)

        av = self.cfg["avatar"]
        custom = str(expand(av["custom_image"])) if av.get("custom_image") else ""
        self.avatar = Avatar(av["size"], custom, av.get("fps", 12))
        self.chat = ChatWindow()
        self.worker: Worker | None = None

        self.tray = QSystemTrayIcon(QIcon(self.avatar.grab()), self.qt)
        self.tray.setToolTip("Capri")
        self.tray.activated.connect(lambda *_: self.toggle_chat())
        self.tray.show()

        self.avatar.clicked.connect(self.toggle_chat)
        self.avatar.quit_requested.connect(self.qt.quit)
        self.chat.submitted.connect(self.on_message)
        self.chat.stop_requested.connect(self.stop_current)

        screen = self.qt.primaryScreen().availableGeometry()
        self.avatar.move(screen.right() - self.avatar.width() - 40, screen.bottom() - self.avatar.height() - 40)
        self.avatar.show()
        self.chat.add_capri(f"Йо, {self.cfg['user_name']}. Я в системе. Чем займёмся?", "rules")

        self.voice = None
        self.last_input_was_voice = False
        self._init_voice()

        # грузим модель в видеокарту сразу, пока пользователь ещё ничего не написал
        self.avatar.set_status("LOADING")
        self.warmup = Warmup(self.agent)
        self.warmup.finished.connect(lambda: self.avatar.set_status("ONLINE"))
        self.warmup.start()

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
            self.voice = VoiceController(self.cfg)
        except Exception as e:  # noqa: BLE001
            self.chat.add_system(f"Голос не запустился: {e}")
            return
        self.chat.mic.show()
        self.chat.mic_clicked.connect(self.voice.toggle_record)
        self.voice.state.connect(self.on_voice_state)
        self.voice.transcribed.connect(self.on_transcribed)
        self.voice.error.connect(self.chat.add_system)
        shortcut = QShortcut(QKeySequence(v.get("hotkey", "Ctrl+Space")), self.chat)
        shortcut.setContext(Qt.ApplicationShortcut)
        shortcut.activated.connect(self.voice.toggle_record)
        self.avatar.menu_items = lambda: [
            ("🎤 Сказать", self.voice.toggle_record),
            ("🔇 Выключить озвучку" if self.cfg["voice"]["speak"] else "🔊 Включить озвучку", self.toggle_speak),
        ]
        self.chat.add_system(f"🎤 Голос включён: кнопка 🎤 или {v.get('hotkey', 'Ctrl+Space')}. "
                             "Первый раз скачаются голосовые модели (~500 МБ).")
        self.voice.warmup()

    def toggle_speak(self):
        v = self.cfg["voice"]
        v["speak"] = not v["speak"]
        self.chat.add_system("Озвучка включена." if v["speak"] else "Озвучка выключена.")

    def on_voice_state(self, state: str):
        self.chat.set_recording(state == "LISTENING")
        if state == "IDLE":
            self.avatar.set_status("ONLINE")
        else:
            self.avatar.set_status(state)
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
    def toggle_chat(self):
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
        if self.worker and self.worker.isRunning():
            return
        self.chat.set_busy(True)
        self.worker = Worker(self.agent, text)
        self.worker.status.connect(self.on_status)
        self.worker.token.connect(self.chat.stream)
        self.worker.done.connect(self.on_reply)
        self.worker.start()

    def stop_current(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop_requested = True

    def on_status(self, status: str):
        self.avatar.set_status(status)
        if status == "CLAUDE":
            self.chat.add_system("Передаю задачу Claude… это может занять минуту.")

    def on_reply(self, reply: Reply):
        self.avatar.set_status("ERROR" if reply.route == "error" else "TALKING")
        QTimer.singleShot(2500, lambda: self.avatar.set_status("ONLINE"))
        self.chat.end_stream()
        self.chat.add_capri(reply.text, reply.route)
        for act in reply.actions:
            self.run_action(act)
        self.chat.set_busy(False)
        v = self.cfg["voice"]
        if self.voice and v["speak"] and (self.last_input_was_voice or not v["speak_only_voice"]):
            self.voice.speak(reply.text)
        self.last_input_was_voice = False

    # действия --------------------------------------------------------------
    def run_action(self, act: A.Action):
        if act.needs_confirm and self.cfg.get("confirm_actions", True):
            answer = QMessageBox.question(self.chat if self.chat.isVisible() else None,
                                          "Capri", f"{act.description}?")
            if answer != QMessageBox.Yes:
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
        self.avatar.set_status("TALKING")
        self.tray.showMessage("Capri", f"⏰ {label}")
        self.chat.add_capri(f"⏰ Эй! {label}", "rules")
        if self.voice and self.cfg["voice"]["speak"]:
            self.voice.speak(f"Эй! {label}")
        QTimer.singleShot(5000, lambda: self.avatar.set_status("ONLINE"))

    def run(self) -> int:
        return self.qt.exec()


def main() -> int:
    return CapriApp().run()
