"""Мастер первого запуска: смотрит, что за компьютер, предлагает модель и сам всё ставит.

1. Привет — имя и город (для погоды).
2. Железо — процессор, память, видеокарта; три варианта модели: быстрая / средняя / умная.
3. Что поставить — Ollama, модель, голос, ускорение на видеокарте, Claude Code.
4. Установка — полоска прогресса; ошибка одного шага не останавливает остальные.
В конце пишет выбор в config.yaml (комментарии в файле остаются).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QProgressBar, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import hardware, installer
from ..config import DEFAULTS, FROZEN, PROJECT_ROOT, expand, load_config, resource
from ..setup_config import REMOVE, apply

STYLE = """
QDialog, QWidget { background: #070b16; color: #d8e3f5; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; }
QLabel#title { color: #3ff3ff; font-size: 18px; font-weight: bold; letter-spacing: 2px; }
QLabel#hint { color: #5b6b85; }
QFrame#card { background: #0b1222; border: 1px solid #1d3550; border-left: 3px solid #ff2a6d; }
QLineEdit { background: #0b1222; border: 1px solid #1d3550; padding: 7px; color: #e8f4ff; }
QLineEdit:focus { border-color: #3ff3ff; }
QPushButton { background: transparent; border: 1px solid #1d3550; color: #3ff3ff; padding: 7px 16px; }
QPushButton:hover { border-color: #3ff3ff; background: #0f2235; }
QPushButton:disabled { color: #2a3a52; border-color: #142236; }
QPushButton#primary { border-color: #ff2a6d; color: #ff2a6d; }
QRadioButton, QCheckBox { spacing: 8px; padding: 4px; }
QRadioButton:disabled, QCheckBox:disabled { color: #3a4a62; }
QRadioButton::indicator, QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #3ff3ff; background: #0b1222; }
QRadioButton::indicator { border-radius: 7px; }
QRadioButton::indicator:checked, QCheckBox::indicator:checked { background: #3ff3ff; }
QRadioButton::indicator:disabled, QCheckBox::indicator:disabled { border-color: #2a3a52; }
QCheckBox::indicator:checked:disabled { background: #1d6a78; }
QProgressBar { background: #0b1222; border: 1px solid #1d3550; height: 16px; text-align: center; color: #d8e3f5; }
QProgressBar::chunk { background: #3ff3ff; }
QListWidget { background: #0b1222; border: 1px solid #1d3550; }
"""


def config_path() -> Path:
    return PROJECT_ROOT / "config.yaml"


def needs_setup() -> bool:
    """Мастер нужен, если настроек ещё нет (свежая установка). Старые установки он не трогает."""
    return not config_path().exists()


def config_values(preset: hardware.Preset, name: str, city: str, claude: bool) -> dict:
    return {
        ("user_name",): name or DEFAULTS["user_name"],
        ("city",): city or DEFAULTS["city"],
        ("ollama", "model"): preset.model,
        ("ollama", "options", "num_gpu"): 99 if preset.on_gpu else REMOVE,
        ("ollama", "options", "num_ctx"): 4096,
        ("voice", "whisper_model"): preset.whisper,
        ("claude_code", "enabled"): bool(claude),
    }


def save_config(values: dict, path: Path | None = None) -> Path:
    path = path or config_path()
    template = path if path.exists() else resource("config.example.yaml")
    text = template.read_text(encoding="utf-8") if template.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(apply(text, values), encoding="utf-8")
    return path


class Detect(QThread):
    done = Signal(object)

    def run(self):
        try:
            hw = hardware.detect()
        except Exception:  # noqa: BLE001
            hw = hardware.Hardware()
        self.done.emit(hw)


class Install(QThread):
    progress = Signal(str, float)     # текст, доля (-1 — неизвестно)
    logged = Signal(str)

    def __init__(self, steps: list):
        super().__init__()
        self.steps = steps
        self.failed: list[str] = []

    def run(self):
        for title, fn in self.steps:
            self.logged.emit(f"▸ {title}")

            def prog(text, frac=None):
                self.progress.emit(text, -1.0 if frac is None else float(frac))
            try:
                fn(prog)
                self.logged.emit(f"  ✓ {title}")
            except Exception as e:  # noqa: BLE001 — один шаг не должен ронять установку
                self.failed.append(title)
                self.logged.emit(f"  ✗ {title}: {e}")
        self.progress.emit("Готово" if not self.failed else "Готово, но не всё получилось — см. список", 1.0)


class SetupWizard(QDialog):
    def __init__(self, parent=None, first_run: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Капри — настройка")
        self.setMinimumSize(640, 520)
        self.setStyleSheet(STYLE)
        self.cfg = load_config(config_path() if config_path().exists() else None)
        self.url = self.cfg["ollama"]["url"]
        self.hw: hardware.Hardware | None = None
        self.presets: list[hardware.Preset] = []
        self.finished_ok = False

        root = QVBoxLayout(self)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        nav = QHBoxLayout()
        self.back = QPushButton("← Назад")
        self.next = QPushButton("Далее →")
        self.next.setObjectName("primary")
        self.cancel = QPushButton("Позже" if first_run else "Отмена")
        nav.addWidget(self.cancel)
        nav.addStretch(1)
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        root.addLayout(nav)
        self.back.clicked.connect(lambda: self._go(self.pages.currentIndex() - 1))
        self.next.clicked.connect(self._next)
        self.cancel.clicked.connect(self.reject)

        self.pages.addWidget(self._page_hello())
        self.pages.addWidget(self._page_hardware())
        self.pages.addWidget(self._page_components())
        self.pages.addWidget(self._page_install())
        self._go(0)
        self.detect = Detect()
        self.detect.done.connect(self._detected)
        self.detect.start()

    # ------------------------------------------------------------------ страницы
    @staticmethod
    def _page(title: str, hint: str = "") -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        t = QLabel(title)
        t.setObjectName("title")
        lay.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            lay.addWidget(h)
        return w, lay

    def _page_hello(self):
        w, lay = self._page("// ПРИВЕТ, Я КАПРИ", "Пара вопросов — и я сама поставлю всё, что нужно под твой компьютер. "
                            "Права администратора не нужны.")
        lay.addSpacing(12)
        lay.addWidget(QLabel("Как тебя зовут?"))
        self.name = QLineEdit(self.cfg.get("user_name", ""))
        lay.addWidget(self.name)
        lay.addWidget(QLabel("Город для погоды:"))
        self.city = QLineEdit(self.cfg.get("city", ""))
        lay.addWidget(self.city)
        lay.addStretch(1)
        return w

    def _page_hardware(self):
        w, lay = self._page("// ТВОЁ ЖЕЛЕЗО", "Для модели важна память видеокарты, а не её серия. "
                            "Выбор можно поменять потом: меню → «⚙ Мастер настройки».")
        self.hw_label = QLabel("Смотрю, что за компьютер…")
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.addWidget(self.hw_label)
        lay.addWidget(card)
        lay.addSpacing(8)
        lay.addWidget(QLabel("Какой будет Капри в разговоре:"))
        self.group = QButtonGroup(self)
        self.radios: list[QRadioButton] = []
        for i in range(3):
            r = QRadioButton("…")
            r.setEnabled(False)
            self.group.addButton(r, i)
            self.radios.append(r)
            lay.addWidget(r)
        self.group.idToggled.connect(lambda *_: self._refresh_components())
        note = QLabel("Команды (музыка, окна, громкость…) от модели не зависят и работают одинаково быстро. "
                      "Сложные задачи всё равно решает Claude.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _page_components(self):
        w, lay = self._page("// ЧТО ПОСТАВИТЬ")
        self.cb_ollama = QCheckBox()
        self.cb_model = QCheckBox()
        self.cb_voice = QCheckBox("Голос: «Капри» + распознавание речи (~0.5 ГБ)")
        self.cb_cuda = QCheckBox("Быстрое распознавание речи на видеокарте NVIDIA (~1.3 ГБ)")
        self.cb_claude = QCheckBox()
        for cb in (self.cb_ollama, self.cb_model, self.cb_voice, self.cb_cuda, self.cb_claude):
            cb.setChecked(True)
            lay.addWidget(cb)
        hint = QLabel("Claude отвечает на сложные вопросы и смотрит на экран. Нужна подписка Claude Pro или Max — "
                      "после установки откроется окно входа.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.total = QLabel("")
        lay.addWidget(self.total)
        for cb in (self.cb_model, self.cb_voice, self.cb_cuda):
            cb.toggled.connect(lambda *_: self._refresh_components())
        lay.addStretch(1)
        return w

    def _page_install(self):
        w, lay = self._page("// УСТАНОВКА", "Можно свернуть окно — я сама всё докачаю.")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.step = QLabel("")
        self.step.setWordWrap(True)
        self.log = QListWidget()
        self.login = QPushButton("Войти в Claude…")
        self.login.hide()
        self.login.clicked.connect(installer.open_claude_login)
        self.retry = QPushButton("Повторить то, что не вышло")
        self.retry.hide()
        self.retry.clicked.connect(self._retry)
        lay.addWidget(self.bar)
        lay.addWidget(self.step)
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addWidget(self.retry)
        row.addWidget(self.login)
        lay.addLayout(row)
        return w

    # ------------------------------------------------------------------ логика
    def _detected(self, hw: hardware.Hardware):
        self.hw = hw
        self.hw_label.setText(hw.summary())
        self.presets = hardware.presets(hw)
        for r, p in zip(self.radios, self.presets):
            tag = "  ★ рекомендую" if p.recommended else ""
            warn = "" if p.fits else "  (не советую)"
            r.setText(f"{p.title} — {p.model}, ~{p.download_gb:.1f} ГБ · {p.note}{tag}{warn}")
            r.setEnabled(True)
            r.setChecked(p.recommended)
        self._refresh_components()

    def _preset(self) -> hardware.Preset | None:
        i = self.group.checkedId()
        return self.presets[i] if 0 <= i < len(self.presets) else None

    def _refresh_components(self):
        p = self._preset()
        have_ollama = bool(installer.ollama_exe())
        self.cb_ollama.setText("Ollama — уже установлена ✓" if have_ollama else "Ollama — движок для локальной модели (~1 ГБ)")
        self.cb_ollama.setChecked(True)
        self.cb_ollama.setEnabled(not have_ollama)
        if p:
            self.cb_model.setText(f"Модель {p.model} (~{p.download_gb:.1f} ГБ)")
        nvidia = bool(self.hw and self.hw.nvidia)
        self.cb_cuda.setVisible(nvidia and FROZEN)
        have_claude = bool(installer.claude_exe())
        self.cb_claude.setText("Claude Code — уже установлен ✓" if have_claude else "Claude Code — для сложных задач")
        self.cb_claude.setEnabled(not have_claude)
        gb = (0 if have_ollama else 1.0) + (p.download_gb if p and self.cb_model.isChecked() else 0) \
            + (0.5 if self.cb_voice.isChecked() else 0) + (1.3 if self.cb_cuda.isVisible() and self.cb_cuda.isChecked() else 0)
        self.total.setText(f"Скачается примерно {gb:.1f} ГБ.")

    def _go(self, i: int):
        i = max(0, min(i, self.pages.count() - 1))
        self.pages.setCurrentIndex(i)
        self.back.setVisible(0 < i < 3)
        self.next.setText({2: "Установить", 3: "Готово"}.get(i, "Далее →"))
        self.next.setEnabled(i != 3)
        self.cancel.setVisible(i != 3)
        if i == 2:
            self._refresh_components()

    def _next(self):
        i = self.pages.currentIndex()
        if i == 1 and not self._preset():
            return
        if i == 2:
            self._go(3)
            self._start_install()
            self.next.setEnabled(False)
            return
        if i == 3:
            self.accept()
            return
        self._go(i + 1)

    def _steps(self) -> list:
        p = self._preset()
        models = expand(self.cfg["voice"]["models_dir"])
        steps = []
        if self.cb_ollama.isChecked() or self.cb_model.isChecked():
            steps.append(("Ollama", lambda pr: installer.install_ollama(self.url, pr)))
        if self.cb_model.isChecked() and p:
            steps.append((f"Модель {p.model}", lambda pr: installer.pull_model(self.url, p.model, pr)))
        if self.cb_voice.isChecked():
            steps.append(("Модель для «Капри»", lambda pr: installer.install_vosk(models, pr)))
            steps.append(("Распознавание речи", lambda pr: installer.install_whisper(p.whisper if p else "small", models, pr)))
        if self.cb_cuda.isVisible() and self.cb_cuda.isChecked():
            steps.append(("Ускорение на видеокарте", installer.install_cuda))
        if self.cb_claude.isEnabled() and self.cb_claude.isChecked():
            steps.append(("Claude Code", installer.install_claude))
        return steps

    def _start_install(self):
        p = self._preset()
        claude = bool(installer.claude_exe()) or self.cb_claude.isChecked()
        try:
            path = save_config(config_values(p, self.name.text().strip(), self.city.text().strip(), claude))
            self.log.addItem(f"✓ Настройки сохранены: {path}")
        except OSError as e:
            self.log.addItem(f"✗ Не смогла сохранить настройки: {e}")
        self._run(self._steps())

    def _run(self, steps: list):
        self.retry.hide()
        self.next.setEnabled(False)
        self.all_steps = steps
        self.worker = Install(steps)
        self.worker.progress.connect(self._on_progress)
        self.worker.logged.connect(lambda t: (self.log.addItem(t), self.log.scrollToBottom()))
        self.worker.finished.connect(self._installed)
        self.worker.start()

    def _on_progress(self, text: str, frac: float):
        self.step.setText(text)
        if frac < 0:
            self.bar.setRange(0, 0)          # «бегущая» полоска — сколько осталось, неизвестно
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))

    def _installed(self):
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.finished_ok = not self.worker.failed
        self.next.setEnabled(True)
        self.retry.setVisible(bool(self.worker.failed))
        if installer.claude_exe():
            self.login.show()


    def done(self, result):
        w = getattr(self, "worker", None)
        if w is not None and w.isRunning():
            self.step.setText("Подожди — идёт установка, закрою, когда закончу.")
            return
        self.detect.wait(15000)
        super().done(result)

    def closeEvent(self, e):
        w = getattr(self, "worker", None)
        if w is not None and w.isRunning():
            e.ignore()
            self.step.setText("Подожди — идёт установка, закрою, когда закончу.")
            return
        self.detect.wait(15000)
        super().closeEvent(e)

    def _retry(self):
        failed = set(self.worker.failed)
        self._run([st for st in self.all_steps if st[0] in failed])


def run_wizard(parent=None, first_run: bool = True) -> bool:
    """True — мастер дошёл до конца (настройки записаны)."""
    w = SetupWizard(parent, first_run)
    return w.exec() == QDialog.Accepted and config_path().exists()
