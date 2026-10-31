"""Мастер первого запуска: смотрит, что за компьютер, предлагает модель и сам всё ставит.

1. Привет — имя и город (для погоды).
2. Железо — процессор, память, видеокарта; три варианта модели: быстрая / средняя / умная.
3. Что поставить — Ollama, модель, голос, ускорение на видеокарте, Claude Code.
4. Установка — полоска прогресса; ошибка одного шага не останавливает остальные.
В конце пишет выбор в config.yaml (комментарии в файле остаются).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QProgressBar, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import hardware, installer
from ..llm import gemini
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


HELLO, HARDWARE, BRAIN, COMPONENTS, INSTALL = range(5)
BRAINS = ("claude", "gemini_free", "gemini_paid", "ollama")


def config_path() -> Path:
    return PROJECT_ROOT / "config.yaml"


def needs_setup() -> bool:
    """Мастер нужен, если настроек ещё нет (свежая установка). Старые установки он не трогает."""
    return not config_path().exists()


def config_values(preset: hardware.Preset, name: str, city: str, claude: bool, brain: str = "claude",
                  gemini_model: str = "", vision_model: str = "") -> dict:
    provider = {"gemini_free": "gemini", "gemini_paid": "gemini"}.get(brain, brain)
    values = {
        ("user_name",): name or DEFAULTS["user_name"],
        ("city",): city or DEFAULTS["city"],
        ("ollama", "model"): preset.model,
        ("ollama", "options", "num_gpu"): 99 if preset.on_gpu else REMOVE,
        ("ollama", "options", "num_ctx"): 4096,
        ("voice", "whisper_model"): preset.whisper,
        ("claude_code", "enabled"): bool(claude),
        ("brain", "provider"): provider,
        ("brain", "vision_model"): vision_model,
    }
    if gemini_model:
        values[("brain", "gemini", "model")] = gemini_model
    return values


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
        self.pages.addWidget(self._page_brain())
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

    def _page_brain(self):
        w, lay = self._page("// УМНЫЙ ПОМОЩНИК", "Кто решает сложные задачи (скрипты, разбор ошибок, длинные тексты) "
                            "и смотрит на экран. Команды и обычный разговор работают и без него.")
        self.brain_group = QButtonGroup(self)
        texts = {
            "claude": "Claude — подписка Pro или Max. Самый умный вариант.",
            "gemini_free": "Gemini бесплатно — ключ Google AI Studio, без карты.",
            "gemini_paid": "Gemini платно — тот же ключ с оплатой в AI Studio, модель Pro.",
            "ollama": "Только локально — без интернета и аккаунтов, сложное — попроще.",
        }
        self.brain_radio = {}
        for i, code in enumerate(BRAINS):
            r = QRadioButton(texts[code])
            self.brain_group.addButton(r, i)
            self.brain_radio[code] = r
            lay.addWidget(r)
        self.brain_group.idToggled.connect(lambda *_: self._refresh_brain())

        self.gem_box = QFrame()
        self.gem_box.setObjectName("card")
        gl = QVBoxLayout(self.gem_box)
        info = QLabel("1. Нажми «Получить ключ» → войди в Google → «Create API key» → скопируй.<br>"
                      "2. Вставь сюда и нажми «Проверить».<br>"
                      "<span style='color:#f5e663'>Бесплатный уровень: мало запросов в день, а Google может "
                      "использовать запросы для улучшения моделей — личное туда не отправляй.</span>")
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        gl.addWidget(info)
        row = QHBoxLayout()
        self.gem_key = QLineEdit(gemini.get_key() or "")
        self.gem_key.setEchoMode(QLineEdit.Password)
        self.gem_key.setPlaceholderText("API-ключ Gemini (AIza…)")
        self.gem_key.textChanged.connect(lambda *_: setattr(self, "gem_model", ""))
        get = QPushButton("Получить ключ")
        get.clicked.connect(lambda: __import__("webbrowser").open(gemini.KEY_PAGE))
        chk = QPushButton("Проверить")
        chk.clicked.connect(self._check_gemini)
        row.addWidget(self.gem_key, 1)
        row.addWidget(get)
        row.addWidget(chk)
        gl.addLayout(row)
        self.gem_smart = QCheckBox("Flash вместо Flash-Lite: умнее, но бесплатно всего ~20 запросов в день")
        self.gem_smart.toggled.connect(lambda *_: setattr(self, "gem_model", ""))
        gl.addWidget(self.gem_smart)
        self.gem_status = QLabel("")
        self.gem_status.setWordWrap(True)
        gl.addWidget(self.gem_status)
        lay.addWidget(self.gem_box)
        self.gem_model = ""

        self.cb_vision = QCheckBox()
        lay.addWidget(self.cb_vision)
        lay.addStretch(1)
        have = bool(installer.claude_exe())
        self.brain_radio["claude" if have else "ollama"].setChecked(True)
        if have:
            self.brain_radio["claude"].setText(texts["claude"] + "  ✓ установлен")
        return w

    def _brain(self) -> str:
        i = self.brain_group.checkedId()
        return BRAINS[i] if 0 <= i < len(BRAINS) else "ollama"

    def _gemini_tier(self) -> str:
        if self._brain() == "gemini_paid":
            return "paid"
        return "free_smart" if self.gem_smart.isChecked() else "free"

    def _refresh_brain(self, reset: bool = True):
        b = self._brain()
        self.gem_box.setVisible(b.startswith("gemini"))
        self.gem_smart.setVisible(b == "gemini_free")
        vm = hardware.vision_model(self.hw) if self.hw else "qwen3-vl:4b"
        size = hardware.MODELS.get(vm, (3.3, 0))[0]
        self.cb_vision.setText(f"Локальная модель со зрением ({vm}, ~{size:.1f} ГБ): «посмотри на экран» без интернета"
                               + ("" if b == "ollama" else " — запасной вариант"))
        if reset:
            self.cb_vision.setChecked(b == "ollama")

    def _check_gemini(self) -> bool:
        key = self.gem_key.text().strip()
        if not key:
            self.gem_status.setText("<span style='color:#ff3355'>Вставь ключ.</span>")
            return False
        self.gem_status.setText("Проверяю…")
        self.gem_status.repaint()
        try:
            self.gem_model = gemini.check_key(key, self._gemini_tier())
        except Exception as e:  # noqa: BLE001
            self.gem_model = ""
            self.gem_status.setText(f"<span style='color:#ff3355'>✗ {e}</span>")
            return False
        self.gem_status.setText(f"<span style='color:#3ff3ff'>✓ Ключ работает. Модель: {self.gem_model}</span>")
        return True

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
        self.cb_claude.setVisible(self._brain() == "claude")
        gb = (0 if have_ollama else 1.0) + (p.download_gb if p and self.cb_model.isChecked() else 0) \
            + (0.5 if self.cb_voice.isChecked() else 0) + (1.3 if not self.cb_cuda.isHidden() and self.cb_cuda.isChecked() else 0) \
            + (hardware.MODELS.get(self._vision(), (0, 0))[0] if self._vision() else 0)
        self.total.setText(f"Скачается примерно {gb:.1f} ГБ.")

    def _go(self, i: int):
        i = max(0, min(i, self.pages.count() - 1))
        self.pages.setCurrentIndex(i)
        self.back.setVisible(HELLO < i < INSTALL)
        self.next.setText({COMPONENTS: "Установить", INSTALL: "Готово"}.get(i, "Далее →"))
        self.next.setEnabled(i != INSTALL)
        self.cancel.setVisible(i != INSTALL)
        if i == BRAIN:
            self._refresh_brain(reset=False)
        if i == COMPONENTS:
            self._refresh_components()

    def _next(self):
        i = self.pages.currentIndex()
        if i == HARDWARE and not self._preset():
            return
        if i == BRAIN and self._brain().startswith("gemini") and not self.gem_model and not self._check_gemini():
            return                                   # без рабочего ключа дальше не пускаем
        if i == COMPONENTS:
            self._go(INSTALL)
            self._start_install()
            self.next.setEnabled(False)
            return
        if i == INSTALL:
            self.accept()
            return
        self._go(i + 1)

    def _vision(self) -> str:
        return (hardware.vision_model(self.hw) if self.hw else "qwen3-vl:4b") if self.cb_vision.isChecked() else ""

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
        if not self.cb_cuda.isHidden() and self.cb_cuda.isChecked():
            steps.append(("Ускорение на видеокарте", installer.install_cuda))
        if vm := self._vision():
            steps.append((f"Модель со зрением {vm}", lambda pr: installer.pull_model(self.url, vm, pr)))
        if not self.cb_claude.isHidden() and self.cb_claude.isEnabled() and self.cb_claude.isChecked():
            steps.append(("Claude Code", installer.install_claude))
        return steps

    def _start_install(self):
        p = self._preset()
        brain = self._brain()
        claude = bool(installer.claude_exe()) or (brain == "claude" and self.cb_claude.isChecked())
        gem_model = ""
        if brain.startswith("gemini"):
            gem_model = self.gem_model
            try:
                gemini.set_key(self.gem_key.text())
                self.log.addItem("✓ Ключ Gemini сохранён в Диспетчере учётных данных Windows")
            except Exception as e:  # noqa: BLE001
                self.log.addItem(f"✗ Не смогла сохранить ключ Gemini: {e}")
        try:
            path = save_config(config_values(p, self.name.text().strip(), self.city.text().strip(), claude,
                                             brain, gem_model, self._vision()))
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
