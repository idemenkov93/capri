"""Окно чата в киберпанк-стиле."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QCompleter, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QTextBrowser, QToolButton, QVBoxLayout, QWidget)

# быстрые кнопки над полем ввода: (надпись, подсказка, фраза или [(пункт меню, фраза), …])
QUICK = (
    ("📋 Буфер", "Что в буфере: текст, скриншот или файл — объясню (письмо, ошибка, QR)", "что в буфере"),
    ("🌐 Перевод", "Перевести то, что в буфере (выделенное в любой программе — Win+Alt+X)", "переведи буфер"),
    ("✉ Письмо", "Письмо на экране: объяснить или ответить", [("Объяснить письмо на экране", "объясни письмо"),
                                                              ("Ответить на письмо…", "ответь на письмо")]),
    ("📄 Экран", "Текст с экрана", [("Скопировать текст с экрана", "скопируй текст с экрана"),
                                    ("Перевести, что на экране", "переведи, что на экране"),
                                    ("Что у меня на экране?", "что у меня на экране")]),
    ("🔕", "Не беспокоить", [("30 минут", "не беспокой 30 минут"), ("1 час", "не беспокой меня час"),
                             ("2 часа", "не беспокой 2 часа"), ("До вечера", "не беспокой до вечера"),
                             ("До утра", "не беспокой до утра"), ("Можно беспокоить", "можно беспокоить")]),
    ("🎧", "Субтитры урока / разговора с переводом", [("📋 Подготовь к уроку", "подготовь к уроку"),
                                                        ("🎧 Включить субтитры (немецкий)", "включи субтитры"),
                                                        ("🎧 Субтитры с английского", "субтитры с английского"),
                                                        ("⏹ Выключить субтитры", "стоп субтитры"),
                                                        ("📝 Конспект урока", "сделай конспект"),
                                                        ("📖 Прошлый урок", "повтори прошлый урок"),
                                                        ("💡 Что спросили — подскажи", "подскажи ответ"),
                                                        ("💬 Хочу ответить (Win+Alt+A)", "__answer__"),
                                                        ("📥 Импорт из Granola", "импортируй гранолу"),
                                                        ("📥 Импорт материалов курса", "импортируй материалы"),
                                                        ("🗣 Фразы для урока", "фразы для урока"),
                                                        ("📖 Новые термины урока → бестиарий", "новые термины урока"),
                                                        ("📖 Импорт бестиария (CSV)", "импортируй бестиарий"),
                                                        ("💾 Выгрузить бестиарий в CSV", "выгрузи бестиарий")]),
    ("🎓", "Наставник: локальная модель, твои уроки и бестиарий", [("🎓 Спросить наставника…", "наставник: "),
                                                                  ("🎯 Что мне подучить?", "что мне подучить"),
                                                                  ("▶ Видео по теме…", "видео про "),
                                                                  ("🧠 Какая у меня локальная модель", "какая у меня локальная модель")]),
    ("🎯", "Тренировка и язык", [("🎯 Собеседование (немецкий)", "тренируй собеседование"),
                                 ("🙋 Самопрезентация", "тренируй самопрезентацию"),
                                 ("☎ Злой клиент (ролевая игра)", "тренируй клиента"),
                                 ("🃏 5 вопросов по Linux", "спроси меня 5 вопросов по Linux"),
                                 ("🔁 Слабые темы", "повтори слабые темы"),
                                 ("🇩🇪 Слово дня", "слово дня"), ("☀ Сводка на сегодня", "утренняя сводка")]),
    ("📚", "Учёба: не отвлекаю, в конце спрошу «что выучил?»", [("25 минут", "начни учёбу на 25 минут"),
                                                                ("45 минут", "начни учёбу на 45 минут"),
                                                                ("60 минут", "начни учёбу на 60 минут"),
                                                                ("Режим учёбы (Teams + Claude)", "режим учёбы")]),
    ("❓", "Что я умею", "что ты умеешь"),
)

# подсказки при наборе (Ctrl+пробел не нужен — выпадают сами по совпадению)
COMPLETIONS = [
    "что в буфере", "переведи буфер", "переведи буфер на немецкий", "переведи буфер на английский",
    "переведи буфер на иврит", "объясни письмо", "ответь на письмо: ", "скопируй текст с экрана",
    "переведи, что на экране", "сделай qr-код", "что у меня на экране", "не беспокой меня час", "не беспокой до вечера",
    "можно беспокоить", "начни учёбу на 45 минут", "режим учёбы", "урок закончился", "поиск работы", "игровой режим",
    "ночной режим", "режим работы", "какие у меня экраны", "громкость 30", "поставь таймер на 10 минут",
    "напомни через час ", "запиши: ", "погода", "сколько ты ешь ресурсов", "собери отчёт", "обнови капри",
    "проверь gemini", "как подключить gemini", "как подключить groq", "как подключить дипсик",
    "проверь groq", "проверь дипсик", "ключ groq ", "ключ openrouter ", "открой ", "найди на ютубе ", "что сейчас играет", "следующий трек",
    "конвертер", "какой у меня ip", "проверь интернет", "что грузит компьютер", "ошибки за сегодня",
    "почему компьютер перезагрузился", "пароль от вайфая", "как в powershell ", "проверь сайт ", "следи за сайтом ",
    "сделай бэкап", "настрой бэкап", "включи субтитры", "субтитры с английского", "стоп субтитры", "сделай конспект",
    "повтори прошлый урок", "что мы проходили по ", "сигнал на слово ", "какие слова слушаешь", "спикер 1 это ", "кто говорит", "как сказать по-немецки ", "помоги ответить: ",
    "как сказать на иврите ", "исправь мою фразу: ", "слово дня", "утренняя сводка", "подскажи ответ", "тренируй собеседование",
    "тренируй самопрезентацию", "тренируй клиента", "спроси меня 5 вопросов по ", "повтори слабые темы", "стоп тренировка",
    "импортируй гранолу", "импортируй материалы", "импортируй бестиарий", "выгрузи бестиарий", "новые термины урока", "подготовь к уроку",
    "фразы для урока", "наставник: ", "по учёбе: ",
    "вопрос по работе: ", "что мне подучить", "план обучения", "видео про ", "какая у меня локальная модель",
    "в тимсе меня зовут ", "что такое ", "горячие клавиши", "объясни по моим урокам ", "курс заканчивается ", "/claude ", "что ты умеешь",
]

CODE_RX = re.compile(r"```[^\n]*\n(.*?)```", re.S)
BRAINS = (("auto", "Авто", "Как обычно: простое — локальная модель, сложное — помощник из мастера"),
          ("local", "Локальная", "Только модель на этом компьютере (без интернета)"),
          ("gemini", "Gemini", "Только Gemini (бесплатный ключ)"),
          ("claude", "Claude", "Только Claude (подписка)"))
# бесплатные облачные — в меню «Ещё ▾», чтобы строка не разъезжалась
MORE_BRAINS = (("groq", "Groq", "Groq — бесплатно, очень быстро (Llama, Qwen, GPT-OSS)"),
               ("openrouter", "DeepSeek (OpenRouter)", "OpenRouter — только бесплатные модели: DeepSeek, Qwen, Llama…"))
LOOKS = (("glow", "✨", "Вид: свечение по краю экрана (без персонажа)"), ("capri", "🧍", "Вид: Капри на экране"))

STYLE = """
QWidget { background: #0b0b14; color: #e6e6f0; font-family: Consolas, 'JetBrains Mono', monospace; font-size: 13px; }
QTextBrowser { border: 1px solid #ff2a6d; padding: 6px; }
QLineEdit { border: 1px solid #00f0ff; padding: 6px; background: #12121f; }
QPushButton { border: 1px solid #00f0ff; padding: 6px 12px; background: #12121f; color: #00f0ff; }
QPushButton:hover { background: #00f0ff; color: #0b0b14; }
QLabel#live { color: #ffd23f; border: 1px dashed #ffd23f; padding: 6px; }
"""

ROUTE_LABEL = {"rules": "⚡", "local": "◆ local", "claude": "✦ Claude", "gemini": "✧ Gemini", "groq": "✧ Groq",
               "openrouter": "✧ OpenRouter", "error": "✖"}


class ChatExtras:
    """Общее для обоих окон чата: «📋 копировать» под ответами и переключатель «Отвечает: …»."""

    def _init_extras(self, log: QTextBrowser):
        self._clips: dict[str, str] = {}
        log.setOpenExternalLinks(False)
        log.setOpenLinks(False)
        log.anchorClicked.connect(self._link)

    def _quick_pick(self, phrase: str):
        """Фраза с продолжением («наставник: », «видео про ») — в поле ввода, дописать вопрос; остальные — сразу."""
        if phrase.endswith(" "):
            self.input.setText(phrase)
            self.input.setFocus()
            self.input.end(False)
        else:
            self.quick.emit(phrase)

    def _quick_row(self) -> QHBoxLayout:
        """Быстрые кнопки: буфер, перевод, письмо, экран, 🔕, учёба, ❓. Нажатие = будто написал фразу."""
        row = QHBoxLayout()
        row.setSpacing(4)
        self.quick_buttons = {}
        for title, tip, what in QUICK:
            b = QToolButton(objectName="quick")
            b.setText(title)
            b.setToolTip(tip)
            if isinstance(what, str):
                b.clicked.connect(lambda _=False, w=what: self.quick.emit(w))
            else:
                m = QMenu(b)
                for label, phrase in what:
                    m.addAction(label, lambda p=phrase: self._quick_pick(p))
                b.setMenu(m)
                b.setPopupMode(QToolButton.InstantPopup)
            row.addWidget(b)
            self.quick_buttons[title] = b
        row.addStretch(1)
        return row

    def _setup_input(self, line: QLineEdit):
        line.setPlaceholderText("Спроси или скажи, что сделать…")
        line.setToolTip("Начни печатать — появятся подсказки команд. Enter — отправить, /claude — сразу к Claude")
        comp = QCompleter(COMPLETIONS, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setMaxVisibleItems(8)
        line.setCompleter(comp)

    def set_brain_available(self, avail: dict):
        """{code: (можно?, подсказка)} — недоступных «мозгов» не выбрать, в подсказке — почему.
        В «Ещё ▾» неподключённые не прячем: выбор такого пункта — инструкция «как подключить»."""
        for code, (ok, tip) in avail.items():
            if b := self.brain_buttons.get(code):
                b.setEnabled(ok)
                b.setToolTip(tip)
            elif act := self.more_actions.get(code):
                self._more_ok[code] = ok
                title = dict((c, t) for c, t, _ in MORE_BRAINS)[code]
                act.setText(title if ok else f"{title} — подключить…")
                act.setToolTip(tip)

    def _brain_row(self) -> QHBoxLayout:
        brow = QHBoxLayout()
        brow.setSpacing(4)
        lbl = QLabel("Отвечает:")
        lbl.setStyleSheet("color:#5b6b85; font-size:10px;")
        brow.addWidget(lbl)
        self.brain_group = QButtonGroup(self)
        self.brain_buttons: dict[str, QPushButton] = {}
        for code, title, tip in BRAINS:
            b = QPushButton(title, objectName="brain", checkable=True)
            b.setToolTip(tip)
            self.brain_group.addButton(b)
            self.brain_buttons[code] = b
            b.clicked.connect(lambda _=False, c=code: self.brain_changed.emit(c))
            brow.addWidget(b)
        # «Ещё ▾»: Groq, DeepSeek (OpenRouter) — бесплатные
        self.more_btn = QPushButton("Ещё ▾", objectName="brain", checkable=True)
        self.more_btn.setToolTip("Бесплатные облачные: Groq, DeepSeek (через OpenRouter)")
        self.brain_group.addButton(self.more_btn)
        menu = QMenu(self.more_btn)
        self.more_actions = {}
        self._more_ok: dict[str, bool] = {}
        for code, title, tip in MORE_BRAINS:
            act = menu.addAction(title)
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, c=code: self._pick_more(c))
            self.more_actions[code] = act
        menu.setToolTipsVisible(True)
        # меню закрыли, ничего не выбрав, — вернуть отметку на того, кто отвечает сейчас
        menu.aboutToHide.connect(lambda: QTimer.singleShot(0, lambda: self.set_brain(self._brain_now)))
        self.more_btn.setMenu(menu)
        self.brain_group.buttonToggled.connect(self._brain_toggled)
        brow.addWidget(self.more_btn)
        brow.addStretch(1)
        # вид: свечение или Капри — как радиокнопки
        lbl2 = QLabel("Вид:")
        lbl2.setStyleSheet("color:#5b6b85; font-size:10px;")
        brow.addWidget(lbl2)
        self.look_group = QButtonGroup(self)
        self.look_buttons: dict[str, QPushButton] = {}
        for code, title, tip in LOOKS:
            b = QPushButton(title, objectName="brain", checkable=True)
            b.setToolTip(tip)
            self.look_group.addButton(b)
            self.look_buttons[code] = b
            b.clicked.connect(lambda _=False, c=code: self.look_changed.emit(c))
            brow.addWidget(b)
        self.brain_buttons["auto"].setChecked(True)
        return brow

    def _pick_more(self, code: str):
        if not self._more_ok.get(code, True):
            self.quick.emit(f"как подключить {code}")      # не подключён — покажем, как (ключ бесплатный)
            self.set_brain(self._brain_now)
            return
        self.set_brain(code)
        self.brain_changed.emit(code)

    def _brain_toggled(self, button, checked: bool):
        if button is self.more_btn and not checked:
            self.more_btn.setText("Ещё ▾")

    _brain_now = "auto"

    def set_brain(self, code: str):
        self._brain_now = code
        if b := self.brain_buttons.get(code):
            b.setChecked(True)
        elif code in self.more_actions:
            self.more_btn.setChecked(True)
            self.more_btn.setText(dict((c, t) for c, t, _ in MORE_BRAINS)[code].split(" (")[0] + " ▾")

    def set_look(self, code: str):
        if b := self.look_buttons.get(code):
            b.setChecked(True)

    def set_dnd_label(self, text: str):
        self.setWindowTitle("CAPRI // link" + (f"  🔕 {text}" if text else ""))

    def copy_links(self, text: str) -> str:
        """Строка «📋 копировать ответ · 📋 код 1 …» под ответом."""
        links = [self._clip_link(text.strip(), "📋 копировать ответ")]
        codes = [c.strip("\n") for c in CODE_RX.findall(text)]
        for i, code in enumerate(codes, 1):
            links.append(self._clip_link(code, "📋 код" if len(codes) == 1 else f"📋 код {i}"))
        return '<p style="font-size:11px">' + ' <span style="color:#444">·</span> '.join(links) + "</p>"

    def _clip_link(self, text: str, title: str) -> str:
        key = str(len(self._clips))
        self._clips[key] = text
        return f'<a href="copy:{key}" style="color:#00f0ff; text-decoration:none">{html.escape(title)}</a>'

    def copy_text(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self.add_system("Скопировано в буфер.")

    def _link(self, url: QUrl):
        if url.scheme() == "copy":
            self.copy_text(self._clips.get(url.path(), ""))
        else:
            QDesktopServices.openUrl(url)


BRAIN_CSS = """
QToolButton#quick { border: 1px solid #1d3550; color: #9fb3cf; background: transparent; padding: 3px 7px; font-size: 11px; }
QToolButton#quick:hover { border-color: #3ff3ff; color: #3ff3ff; }
QToolButton#quick::menu-indicator { image: none; width: 0; }
QPushButton#brain:disabled { color: #2c3a50; border-color: #16233a; }
QPushButton#brain { border: 1px solid #1d3550; color: #5b6b85; padding: 2px 8px; font-size: 10px; background: transparent; }
QPushButton#brain:checked { border-color: #ff2a6d; color: #ff2a6d; }
QPushButton#brain::menu-indicator { image: none; width: 0; }
"""


class ChatWindow(ChatExtras, QWidget):
    quick = Signal(str)
    submitted = Signal(str)
    stop_requested = Signal()
    mic_clicked = Signal()
    brain_changed = Signal(str)
    look_changed = Signal(str)

    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("CAPRI // link")
        self.setStyleSheet(STYLE + BRAIN_CSS)
        self.resize(460, 560)

        self.log = QTextBrowser()
        self._init_extras(self.log)
        self.input = QLineEdit()
        self.send = QPushButton("SEND")
        self.stop = QPushButton("STOP")
        self.stop.setStyleSheet("border-color:#ff2a6d; color:#ff2a6d;")
        self.stop.hide()
        self.mic = QPushButton("🎤")
        self.mic.setToolTip("Сказать голосом (нажми, говори, нажми ещё раз)")
        self.mic.hide()

        row = QHBoxLayout()
        row.addWidget(self.mic)
        row.addWidget(self.input)
        row.addWidget(self.send)
        row.addWidget(self.stop)
        lay = QVBoxLayout(self)
        lay.addWidget(self.log)
        brow = self._brain_row()          # кто отвечает на письменные вопросы
        self._setup_input(self.input)
        # «живой» ответ: текст появляется по мере того, как модель его печатает
        self.live = QLabel(objectName="live", wordWrap=True)
        self.live.hide()
        self._live_text = ""
        self._thinking = 0
        lay.addWidget(self.live)
        lay.addLayout(self._quick_row())
        lay.addLayout(brow)
        lay.addLayout(row)

        self.input.returnPressed.connect(self._submit)
        self.send.clicked.connect(self._submit)
        self.stop.clicked.connect(self.stop_requested.emit)
        self.mic.clicked.connect(self.mic_clicked.emit)

    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.add_user(text)
            self.submitted.emit(text)

    def set_busy(self, busy: bool):
        self.input.setEnabled(not busy)
        self.send.setVisible(not busy)
        self.stop.setVisible(busy)
        if not busy:
            self.input.setFocus()

    def set_recording(self, on: bool):
        self.mic.setText("⏺ REC" if on else "🎤")
        self.mic.setStyleSheet("border-color:#ff0033; color:#ff0033;" if on else "")

    def stream(self, kind: str, piece: str):
        if kind == "reset":  # модель закончила рассуждать — показываем только сам ответ
            self._live_text = piece
            self.live.setText(self._live_text[-600:])
        elif kind == "thinking":
            self._thinking += len(piece)
            if not self._live_text:
                self.live.setText(f"🧠 размышляет… ({self._thinking} симв.)")
        else:
            self._live_text += piece
            self.live.setText(self._live_text[-600:])
        self.live.show()

    def end_stream(self):
        self._live_text, self._thinking = "", 0
        self.live.clear()
        self.live.hide()

    def add_user(self, text: str):
        self._append(f'<p style="color:#00f0ff"><b>&gt; {html.escape(text)}</b></p>')

    def add_capri(self, text: str, route: str = "local"):
        label = ROUTE_LABEL.get(route, "")
        self._append(f'<p style="color:#ff2a6d"><b>CAPRI</b> <span style="color:#666">{label}</span></p>')
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertMarkdown(text)  # ответы Claude приходят в Markdown
        cur.insertBlock()
        self.log.setTextCursor(cur)
        if text.strip() and route != "rules":
            self._append(self.copy_links(text))
        self.log.ensureCursorVisible()


    def add_system(self, text: str):
        self._append(f'<p style="color:#888"><i>{html.escape(text)}</i></p>')

    def _append(self, html_text: str):
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        if cur.block().text():
            cur.insertBlock()
        cur.insertHtml(html_text)
        cur.insertBlock()
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()
