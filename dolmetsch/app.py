"""Запуск и связь модулей: источник субтитров → конвейер перевода → окно, значок в трее, горячие клавиши."""
from __future__ import annotations

import argparse
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import __version__, hardware
from .config import Config, app_dir, get_deepl_key, set_deepl_key
from .log import log, setup as setup_log
from .translate.deepl import DeepLTranslator
from .translate.opusmt import OpusMTTranslator, find_model
from .translate.router import PROFILE_NAMES, Router

HOW_TEAMS = "В звонке Teams: «…» (Weitere) → «Sprache und Sprachausgabe» → «Liveuntertitel anzeigen»."
HOW_WINDOWS = "Не получилось включить субтитры Windows. Нажми Win+Ctrl+L и выбери язык Deutsch."
STATUS = {
    "no_window": ("Ищу встречу…", "warn"),
    "no_panel": ("Teams найден, субтитры не включены", "warn"),
    "panel_empty": ("Субтитры Teams включены, жду речь", "ok"),
    "no_text": ("Субтитры Windows открыты, текста пока нет", "warn"),
    "lost": ("Субтитры пропали, ищу снова…", "warn"),
}


class Report:
    """Отчёт для служебных режимов (--smoke, --bench, --probe): в файл и, если есть консоль, на экран."""

    def __init__(self, path: str | None):
        self.path, self.lines = path, []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)
        if sys.stdout:
            try:
                print(line, flush=True)
            except Exception:  # noqa: BLE001
                pass
        if self.path:
            Path(self.path).write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def status_text(running: bool, source: str, status: str, profile: str, engine: str, ms: int, error: str) -> tuple[str, str]:
    """Строка состояния под переводом и её цвет."""
    if not running:
        return "Пауза", "warn"
    if status != "ok":
        return STATUS.get(status, ("Ищу встречу…", "warn"))
    parts = [source, f"профиль «{PROFILE_NAMES.get(profile, profile)}»"]
    if engine:
        parts.append(f"{engine}, {ms / 1000:.1f} с".replace(".", ","))
    if error:
        parts.append(error)
    return " · ".join(parts), ("warn" if error else "ok")


def make_controller():
    """Собирает приложение. Qt подключается здесь, чтобы служебные режимы работали и без него."""
    from PySide6.QtCore import QObject, QTimer, QUrl, Signal
    from PySide6.QtGui import QAction, QActionGroup, QCursor, QDesktopServices
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QSystemTrayIcon

    from .capture.selector import CaptureLoop, Selector
    from .capture.teams import TeamsSource
    from .capture.wincaptions import WindowsCaptionsSource
    from .hotkeys import Hotkeys
    from .pipeline import Pipeline
    from .transcript import Transcript, documents_dir, lessons_root
    from .ui import style
    from .ui.overlay import Overlay, set_capture_hidden
    from .ui.prompt import AskBar

    class Bridge(QObject):
        """Сигналы из фоновых потоков в окно (Qt сам переносит их в главный поток)."""
        line = Signal(object)
        partial = Signal(str, str)
        draft = Signal(str)
        state = Signal(str, str)
        ask = Signal()
        hotkey = Signal(str)
        notice = Signal(str)
        model = Signal(str)

    class Controller:
        def __init__(self, use_hotkeys: bool = True):
            self.cfg = Config()
            self.hw = hardware.detect()
            self.offline = OpusMTTranslator(device=self.cfg["device"], threads=self.cfg["threads"])
            self.online = DeepLTranslator(get_deepl_key)
            self.router = Router(self.offline, self.online, self.cfg["profile"], self.cfg["lesson_hours"],
                                 state=self.cfg["deepl"])
            self.transcript = Transcript()
            self.running = False
            self.pipeline = self.capture = None
            self.source, self.status, self.engine, self.ms = "", "no_window", "", 0

            self.bridge = Bridge()
            self.overlay = Overlay(self.cfg["font_size"], self.cfg["opacity"], self.cfg["lines"],
                                   self.cfg["show_original"], self.cfg["window"])
            self.askbar = AskBar()
            b = self.bridge
            b.line.connect(self.on_line)
            b.partial.connect(self.overlay.set_partial)
            b.draft.connect(self.overlay.set_draft)
            b.state.connect(self.on_state)
            b.ask.connect(self.on_ask)
            b.hotkey.connect(self.on_hotkey)
            b.notice.connect(self.overlay.flash)
            b.model.connect(self.on_model)
            self.askbar.answered.connect(self.on_answer)
            self.askbar.how.connect(lambda: self.overlay.flash(HOW_TEAMS, 20))
            self.overlay.menu_requested.connect(lambda pos: self.menu.popup(pos))
            self.overlay.geometry_changed.connect(lambda g: self.cfg.set("window", g))

            self.menu = QMenu()
            self.build_menu(QAction, QActionGroup)
            self.tray = None
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray = QSystemTrayIcon(style.icon(False))
                self.tray.setToolTip("Dolmetsch")
                self.tray.setContextMenu(self.menu)
                self.tray.activated.connect(lambda reason: self.toggle_window()
                                            if reason == QSystemTrayIcon.Trigger else None)
                self.tray.show()

            self.hotkeys = None
            if use_hotkeys:
                self.hotkeys = Hotkeys(self.cfg["hotkeys"], self.bridge.hotkey.emit)
                self.hotkeys.start()
                self.hotkeys.ready.wait(2)
                if self.hotkeys.failed:
                    self.overlay.flash("Сочетания заняты другой программой: " + ", ".join(self.hotkeys.failed)
                                       + ". Поменяй их в config.json (меню → Папка настроек).", 20)

            self.timer = QTimer(interval=30_000, timeout=self.tick)
            self.timer.start()
            threading.Thread(target=self.load_model, name="model", daemon=True).start()

        # ---------- меню
        def build_menu(self, QAction, QActionGroup) -> None:
            m, hk = self.menu, self.cfg["hotkeys"]
            self.act_toggle = m.addAction(f"Старт\t{hk['toggle']}", self.toggle)
            m.addAction(f"Показать / спрятать окно\t{hk['window']}", self.toggle_window)
            m.addSeparator()
            prof = m.addMenu("Профиль")
            group = QActionGroup(prof)
            for key, title in (("auto", "Авто: в будни 9–17 «Урок», иначе «Важное»"),
                               ("lesson", "Урок — только офлайн"), ("important", "Важное — DeepL")):
                act = prof.addAction(title, lambda checked=False, k=key: self.set_profile(k))
                act.setCheckable(True)
                act.setChecked(self.cfg["profile"] == key)
                group.addAction(act)
            self.profile_actions = dict(zip(("auto", "lesson", "important"), group.actions()))
            m.addAction("Ключ DeepL…", self.ask_key)
            m.addSeparator()
            for key, title in (("show_original", "Показывать немецкий оригинал"),
                               ("save_transcript", "Сохранять текст встречи"),
                               ("hide_from_capture", "Не показывать окно при демонстрации экрана"),
                               ("autostart", "Начинать перевод при запуске")):
                act = m.addAction(title, lambda checked=False, k=key: self.flip(k))
                act.setCheckable(True)
                act.setChecked(bool(self.cfg[key]))
            size = m.addMenu("Размер шрифта")
            size.addAction("Крупнее", lambda: self.font(+1))
            size.addAction("Мельче", lambda: self.font(-1))
            m.addSeparator()
            m.addAction("Папка с текстами встреч", lambda: self.open(lessons_root()))
            m.addAction("Папка настроек и журнала", lambda: self.open(app_dir()))
            m.addAction("Сохранить отчёт для разбора", self.save_report)
            m.addSeparator()
            m.addAction("Выход", self.quit)

        def open(self, path: Path) -> None:
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def flip(self, key: str) -> None:
            self.cfg.set(key, not self.cfg[key])
            if key == "show_original":
                self.overlay.show_original = self.cfg[key]
                self.overlay.render()
            elif key == "hide_from_capture":
                self.apply_capture()
            elif key == "save_transcript" and self.pipeline is not None:
                self.pipeline.transcript = self.transcript if self.cfg[key] else None

        def font(self, delta: int) -> None:
            self.overlay.set_font_size(self.overlay.font_size + delta)
            self.cfg.set("font_size", self.overlay.font_size)

        def apply_capture(self) -> None:
            for w in (self.overlay, self.askbar):
                set_capture_hidden(w, bool(self.cfg["hide_from_capture"]))

        # ---------- профиль и ключ
        def set_profile(self, profile: str, save: bool = True) -> None:
            self.router.profile = profile
            if save:                         # из меню — запоминаем; с клавиши — только до перезапуска
                self.cfg.set("profile", profile)
            self.profile_actions[profile].setChecked(True)
            active = self.router.active_profile()
            note = f"Профиль: «{PROFILE_NAMES[active]}»"
            if profile == "important" and not self.online.ready():
                note += ". Ключа DeepL нет — пока перевожу офлайн (меню → Ключ DeepL)."
            self.overlay.flash(note, 6)
            self.refresh()

        def ask_key(self) -> None:
            key, ok = QInputDialog.getText(None, "Ключ DeepL", "Ключ DeepL API (хранится в Windows, в файлы не пишется).\n"
                                           "Пустая строка — удалить ключ.", QLineEdit.Password)
            if not ok:
                return
            if not set_deepl_key(key):
                return self.overlay.flash("Не получилось сохранить ключ в хранилище Windows.", 10)
            if not key.strip():
                return self.overlay.flash("Ключ DeepL удалён.", 6)
            try:
                used, limit = self.online.usage()
                self.router.state.pop("blocked_month", None)
                self.overlay.flash(f"Ключ принят. В этом месяце использовано {used:,} из {limit:,} символов."
                                   .replace(",", " "), 12)
            except Exception as e:  # noqa: BLE001
                self.overlay.flash(f"Ключ сохранён, но проверка не прошла: {e}", 12)

        # ---------- старт и стоп
        def load_model(self) -> None:
            try:
                self.offline.load()
                log.info("модель: устройство %s, железо класса %s (видеопамять %.1f ГБ, потоков %d)",
                         self.offline.used_device, self.hw.cls, self.hw.vram_gb, self.hw.threads)
                self.bridge.model.emit("")
            except Exception as e:  # noqa: BLE001
                log.error("модель: %s", e)
                self.bridge.model.emit(str(e))

        def draft_interval(self):
            """Черновой перевод недописанной фразы — только когда модель работает на видеокарте."""
            setting = self.cfg["live_partial"]
            if self.offline.used_device == "cuda" or setting == "on":
                return hardware.live_interval(self.hw, setting)
            return None

        def on_model(self, error: str) -> None:
            if error:
                self.overlay.flash(f"Офлайн-перевод недоступен: {error}", 30)
            if self.pipeline is not None:
                self.pipeline.live_interval = self.draft_interval()
            self.refresh()

        def start(self) -> None:
            if self.running:
                return
            self.running = True
            self.source, self.status, self.engine = "", "no_window", ""
            self.pipeline = Pipeline(
                self.router, on_line=self.bridge.line.emit, on_partial=self.bridge.partial.emit,
                on_draft=self.bridge.draft.emit,
                transcript=self.transcript if self.cfg["save_transcript"] else None,
                live_interval=self.draft_interval())
            self.pipeline.start()

            def make_selector(auto):
                return Selector(TeamsSource(auto=auto), WindowsCaptionsSource(auto=auto),
                                on_event=self.pipeline.push, on_state=self.bridge.state.emit,
                                on_ask=self.bridge.ask.emit)

            self.capture = CaptureLoop(make_selector, on_error=lambda text: log.warning("%s", text))
            self.capture.start()
            log.info("старт: профиль %s", self.router.active_profile())
            self.refresh()

        def stop(self) -> None:
            if not self.running:
                return
            self.running = False
            self.askbar.hide()
            for worker in (self.capture, self.pipeline):
                worker.stop()
            for worker in (self.capture, self.pipeline):
                worker.join(2)
            log.info("стоп: %s", self.pipeline.summary())
            self.log_reads()
            path = self.transcript.close()
            self.overlay.set_partial("", "")
            if path:
                self.overlay.flash(f"Текст встречи сохранён: {path.parent.name}", 10)
            self.cfg.save()                  # счётчик символов DeepL
            self.refresh()

        def toggle(self) -> None:
            self.stop() if self.running else self.start()

        def toggle_window(self) -> None:
            self.overlay.setVisible(not self.overlay.isVisible())

        # ---------- события
        def on_hotkey(self, action: str) -> None:
            if action == "toggle":
                self.toggle()
            elif action == "window":
                self.toggle_window()
            elif action == "profile":
                self.set_profile(self.router.toggle(), save=False)

        def on_line(self, line) -> None:
            self.engine, self.ms = line.engine or self.engine, line.ms
            self.overlay.add_line(line)
            if self.transcript.error:
                self.overlay.flash(self.transcript.error, 10)
            self.refresh(line.error if not line.translation else self.router.last_error)

        def on_state(self, source: str, status: str) -> None:
            self.source, self.status = source, status
            selector = self.capture.selector if self.capture else None
            if status == "ok":
                self.askbar.hide()
                if selector and source == "Teams":
                    self.transcript.set_title(selector.teams.title)
            log.info("источник: %s / %s", source or "-", status)
            self.refresh()

        def on_ask(self) -> None:
            selector = self.capture.selector if self.capture else None
            teams_running = bool(selector and selector.teams.status != "no_window")
            self.askbar.ask(self.overlay, teams_running)
            self.apply_capture()

        def on_answer(self, yes: bool) -> None:
            selector = self.capture.selector if self.capture else None
            if selector is None:
                return
            if not selector.answer(yes):
                self.overlay.flash(HOW_WINDOWS, 20)
            elif yes:
                self.overlay.flash("Включаю субтитры Windows. Язык распознавания должен быть Deutsch.", 10)
            log.info("субтитры Windows: ответ %s", "да" if yes else "нет")

        def refresh(self, error: str = "") -> None:
            if not self.offline.ready():
                error = error or "нет офлайн-модели (папка models)"
            text, level = status_text(self.running, self.source, self.status, self.router.active_profile(),
                                      self.engine, self.ms, error)
            if not self.running:
                text = f"Пауза — {self.cfg['hotkeys']['toggle']}, чтобы начать"
            self.overlay.set_status(text, level)
            self.act_toggle.setText(("Стоп" if self.running else "Старт") + f"\t{self.cfg['hotkeys']['toggle']}")
            if self.tray:
                self.tray.setIcon(style.icon(self.running))
                self.tray.setToolTip(f"Dolmetsch — {text}")

        def log_reads(self) -> None:
            """В журнал: сколько миллисекунд занимает одно чтение субтитров (только числа)."""
            selector = self.capture.selector if self.capture else None
            source = selector.active if selector else None
            if source is None or not source.read_ms:
                return
            ms, source.read_ms = sorted(source.read_ms), []
            log.info("чтение %s: %d раз, медиана %.0f мс, 95 %% до %.0f мс", source.name, len(ms),
                     ms[len(ms) // 2], ms[min(len(ms) - 1, int(len(ms) * 0.95))])

        def tick(self) -> None:
            if self.running:
                self.log_reads()
                self.refresh()

        # ---------- отчёт и выход
        def save_report(self) -> None:
            self.overlay.flash("Собираю отчёт: около минуты, не закрывай звонок…", 60)

            def work():
                try:
                    from .capture import uia
                    from .tools import uia_dump
                    auto = uia.load()
                    with auto.UIAutomationInitializerInThread():
                        args = argparse.Namespace(snapshots=3, pause=10, max_nodes=8000, max_depth=70,
                                                  show_text=False, also=["livecaptions.exe"])
                        dump = uia_dump.run(auto, args, app_dir(), log=lambda *a: None)
                    out = documents_dir() / f"Dolmetsch-отчёт-{time.strftime('%Y%m%d-%H%M')}.zip"
                    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                        for f in [dump, app_dir() / "dolmetsch.log", app_dir() / "config.json"]:
                            if f and Path(f).exists():
                                z.write(f, Path(f).name)
                    self.bridge.notice.emit(f"Отчёт сохранён в «Документы»: {out.name}")
                except Exception as e:  # noqa: BLE001
                    log.error("отчёт: %s", e)
                    self.bridge.notice.emit(f"Отчёт не собрался: {e}")

            threading.Thread(target=work, name="report", daemon=True).start()

        def quit(self) -> None:
            self.stop()
            if self.hotkeys:
                self.hotkeys.stop()
            self.cfg.save()
            if self.tray:
                self.tray.hide()
            QApplication.quit()

    _ = QCursor                                # (импорт нужен PyInstaller: меню открывается у курсора)
    return Controller


# ---------------------------------------------------------------- служебные режимы
def smoke(report) -> int:
    """Проверка сборки: все части подключаются, окно создаётся. Перевод не запускается."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if not sys.platform.startswith("win") else "windows")
    failed = 0
    for module in ["ctranslate2", "sentencepiece", "keyring", "PySide6.QtWidgets"] + \
            (["uiautomation"] if sys.platform.startswith("win") else []):
        try:
            __import__(module)
            report(f"OK   {module}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            report(f"FAIL {module}: {e}")
    report(f"{'OK  ' if find_model() else 'WARN'} офлайн-модель: {find_model() or 'не найдена'}")
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from .pipeline import Line
        app = QApplication.instance() or QApplication([])
        controller = make_controller()(use_hotkeys=False)
        controller.overlay.show()
        controller.overlay.add_line(Line("Тест", "Guten Morgen zusammen.", "Всем доброе утро.", "офлайн", 120))
        controller.overlay.set_partial("Тест", "Wir fangen heute mit")
        controller.askbar.ask(controller.overlay, True)
        QTimer.singleShot(1500, app.quit)
        app.exec()
        report("OK   окно и меню")
    except Exception as e:  # noqa: BLE001
        failed += 1
        report(f"FAIL окно: {type(e).__name__}: {e}")
    report(f"Dolmetsch {__version__}: {'сборка в порядке' if not failed else 'ЕСТЬ ОШИБКИ'}")
    return 1 if failed else 0


def probe(report, seconds: float) -> int:
    """Проверка захвата без окна: что видит источник субтитров (только длины, без текста)."""
    from .capture.selector import CaptureLoop, Selector
    from .capture.teams import TeamsSource
    from .capture.wincaptions import WindowsCaptionsSource
    holder = {}

    def make(auto):
        holder["sel"] = Selector(
            TeamsSource(auto=auto), WindowsCaptionsSource(auto=auto),
            on_event=lambda e: report(f"{time.strftime('%H:%M:%S')} {e.kind:7} имя {len(e.speaker):2} симв., текст {len(e.text):3} симв."),
            on_state=lambda source, status: report(f"{time.strftime('%H:%M:%S')} источник: {source or '-'} / {status}"),
            on_ask=lambda: report("предложил бы включить субтитры Windows"))
        return holder["sel"]

    loop = CaptureLoop(make, on_error=report)
    loop.start()
    time.sleep(seconds)
    loop.stop()
    loop.join(3)
    source = holder.get("sel") and holder["sel"].active
    if source and source.read_ms:
        ms = sorted(source.read_ms)
        report(f"чтение {source.name}: {len(ms)} раз, медиана {ms[len(ms) // 2]:.0f} мс, худшее {ms[-1]:.0f} мс")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="Dolmetsch", description="Перевод субтитров встреч (DE → RU).")
    parser.add_argument("--smoke", action="store_true", help="проверить сборку и выйти")
    parser.add_argument("--bench", action="store_true", help="замерить скорость офлайн-перевода")
    parser.add_argument("--probe", type=float, metavar="СЕКУНД", help="проверить захват субтитров без окна")
    parser.add_argument("--out", metavar="ФАЙЛ", help="куда записать отчёт служебного режима")
    parser.add_argument("--version", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:                   # у оконной программы нет консоли — не падаем на справке
        return int(e.code or 0)
    report = Report(args.out)
    if args.version:
        report(f"Dolmetsch {__version__}")
        return 0
    if args.smoke:
        return smoke(report)
    if args.bench:
        from . import bench
        return bench.run(report)
    if args.probe:
        return probe(report, args.probe)

    setup_log()
    from .log import crash
    sys.excepthook = lambda kind, value, tb: crash("окно", value)          # у оконной программы нет консоли —
    threading.excepthook = lambda a: crash(a.thread.name if a.thread else "поток", a.exc_value)   # сбои пишем в журнал
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(app_dir() / "dolmetsch.lock"))
    if not lock.tryLock(200):
        QMessageBox.information(None, "Dolmetsch", "Dolmetsch уже запущен — значок рядом с часами.")
        return 0
    log.info("=== Dolmetsch %s ===", __version__)
    controller = make_controller()()
    controller.overlay.show()
    controller.apply_capture()
    if controller.cfg["autostart"]:
        controller.start()
    else:
        controller.refresh()
    code = app.exec()
    lock.unlock()
    return code
