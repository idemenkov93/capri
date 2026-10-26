"""Сборка приложения: аватар + чат + агент в отдельном потоке."""
from __future__ import annotations

import sys

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QIcon
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

    def run(self):
        try:
            reply = self.agent.handle(self.text, on_status=self.status.emit, on_token=self.token.emit)
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

        screen = self.qt.primaryScreen().availableGeometry()
        self.avatar.move(screen.right() - self.avatar.width() - 40, screen.bottom() - self.avatar.height() - 40)
        self.avatar.show()
        self.chat.add_capri(f"Йо, {self.cfg['user_name']}. Я в системе. Чем займёмся?", "rules")

        # грузим модель в видеокарту сразу, пока пользователь ещё ничего не написал
        self.avatar.set_status("LOADING")
        self.warmup = Warmup(self.agent)
        self.warmup.finished.connect(lambda: self.avatar.set_status("ONLINE"))
        self.warmup.start()

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
        QTimer.singleShot(5000, lambda: self.avatar.set_status("ONLINE"))

    def run(self) -> int:
        return self.qt.exec()


def main() -> int:
    return CapriApp().run()
