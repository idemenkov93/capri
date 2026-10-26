"""«🎛 Голос Капри» — окно, где вручную подбирается голос: какой голос Edge, скорость, высота и фильтры."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from ..voice.fx import FX_KEYS, PRESETS

# Женские голоса Edge. «Multilingual» умеют читать и по-русски — другой тембр для Капри.
VOICES = {
    "ru": ["ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural", "en-US-AvaMultilingualNeural",
           "en-US-EmmaMultilingualNeural", "de-DE-SeraphinaMultilingualNeural", "fr-FR-VivienneMultilingualNeural"],
    "de": ["de-DE-AmalaNeural", "de-DE-KatjaNeural", "de-DE-SeraphinaMultilingualNeural", "de-AT-IngridNeural",
           "de-CH-LeniNeural"],
    "en": ["en-US-AvaNeural", "en-US-JennyNeural", "en-US-AriaNeural", "en-US-EmmaNeural", "en-US-MichelleNeural",
           "en-GB-SoniaNeural", "en-GB-LibbyNeural", "en-US-AvaMultilingualNeural"],
    "he": ["he-IL-HilaNeural"],
}
SAMPLES = {
    "ru": "Ну привет. Опять ты? Ладно, говори, что надо, — у меня процессы скучают.",
    "de": "Na super, du schon wieder. Sag schnell, was du brauchst.",
    "en": "Oh great, you again. Fine — what do you need this time?",
    "he": "שוב אתה? טוב, מה אתה צריך?",
}

T = {
    "title": {"ru": "Голос Капри", "en": "Capri's voice"},
    "lang": {"ru": "Язык", "en": "Language"},
    "voice": {"ru": "Голос", "en": "Voice"},
    "rate": {"ru": "Скорость", "en": "Speed"},
    "pitch": {"ru": "Высота", "en": "Pitch"},
    "filters": {"ru": "Фильтры", "en": "Filters"},
    "preset": {"ru": "Пресет", "en": "Preset"},
    "robot": {"ru": "Робот (металл)", "en": "Robot (metal)"},
    "digital": {"ru": "Цифра (зерно)", "en": "Digital (grain)"},
    "echo": {"ru": "Эхо", "en": "Echo"},
    "double": {"ru": "Двойник (слои)", "en": "Double (layers)"},
    "radio": {"ru": "Рация", "en": "Radio"},
    "glitch": {"ru": "Глитч (заикание)", "en": "Glitch (stutter)"},
    "test": {"ru": "▶ Прослушать", "en": "▶ Listen"},
    "save": {"ru": "Сохранить", "en": "Save"},
    "cancel": {"ru": "Отмена", "en": "Cancel"},
    "phrase": {"ru": "Фраза для проверки", "en": "Test phrase"},
}
PRESET_NAMES = {"clean": ("Чистый", "Clean"), "cyber": ("Киберпанк", "Cyberpunk"), "hologram": ("Голограмма", "Hologram"),
                "radio": ("Рация", "Radio"), "glitchy": ("Глитч", "Glitchy"), "demon": ("Демон", "Demon")}


def _num(s: str, suffix: str) -> int:
    try:
        return int(str(s).replace(suffix, "").replace("+", ""))
    except ValueError:
        return 0


class VoiceLab(QDialog):
    """Меняет голос «вживую»: кнопка «Прослушать» сразу говорит с текущими настройками."""
    saved = Signal(dict)

    def __init__(self, tune: dict, ui_lang: str, play, parent=None):
        super().__init__(parent)
        self.L = "ru" if ui_lang == "ru" else "en"
        self.original = {**tune, "voices": dict(tune["voices"]), "fx": dict(tune.get("fx") or {})}
        self.tune = {**tune, "voices": dict(tune["voices"]), "fx": dict(tune.get("fx") or {})}
        self.play = play       # play(tune, text, lang) — проговорить с этими настройками
        self.setWindowTitle(self.t("title"))
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.lang = QComboBox()
        for code, name in (("ru", "Русский"), ("de", "Deutsch"), ("en", "English"), ("he", "עברית")):
            self.lang.addItem(name, code)
        self.lang.currentIndexChanged.connect(self._lang_changed)
        form.addRow(self.t("lang"), self.lang)
        self.voice = QComboBox(editable=True)   # можно вписать любой голос Edge
        self.voice.currentTextChanged.connect(self._voice_changed)
        form.addRow(self.t("voice"), self.voice)
        self.rate, rate_row = self._slider(-50, 50, _num(self.tune.get("rate", "+0%"), "%"), "%")
        self.pitch, pitch_row = self._slider(-50, 50, _num(self.tune.get("pitch", "+0Hz"), "Hz"), " Hz")
        form.addRow(self.t("rate"), rate_row)
        form.addRow(self.t("pitch"), pitch_row)
        root.addLayout(form)

        box = QGroupBox(self.t("filters"))
        ff = QFormLayout(box)
        self.preset = QComboBox()
        self.preset.addItem("—", "")
        for key, (ru, en) in PRESET_NAMES.items():
            self.preset.addItem(ru if self.L == "ru" else en, key)
        self.preset.currentIndexChanged.connect(self._preset)
        ff.addRow(self.t("preset"), self.preset)
        self.fx_sliders: dict[str, QSlider] = {}
        for k in FX_KEYS:
            sl, row = self._slider(0, 100, int(self.tune["fx"].get(k, 0)), "")
            self.fx_sliders[k] = sl
            ff.addRow(self.t(k), row)
        root.addWidget(box)

        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText(self.t("phrase"))
        root.addWidget(self.phrase)
        buttons = QHBoxLayout()
        test = QPushButton(self.t("test"))
        test.clicked.connect(self._test)
        save = QPushButton(self.t("save"))
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = QPushButton(self.t("cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(test)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self._lang_changed()

    def t(self, key: str) -> str:
        return T[key][self.L]

    def _slider(self, lo: int, hi: int, value: int, unit: str):
        sl = QSlider(Qt.Horizontal, minimum=lo, maximum=hi, value=max(lo, min(hi, value)))
        lab = QLabel()
        lab.setMinimumWidth(52)

        def show(v):
            lab.setText(f"{v:+d}{unit}" if lo < 0 else f"{v}{unit}")
        sl.valueChanged.connect(show)
        show(sl.value())
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(sl, 1)
        h.addWidget(lab)
        return sl, row

    def _lang_changed(self):
        code = self.lang.currentData()
        self.voice.blockSignals(True)
        self.voice.clear()
        self.voice.addItems(VOICES[code])
        cur = self.tune["voices"].get(code, VOICES[code][0])
        if self.voice.findText(cur) < 0:
            self.voice.addItem(cur)
        self.voice.setCurrentText(cur)
        self.voice.blockSignals(False)
        self.phrase.setText(SAMPLES[code])

    def _voice_changed(self, text: str):
        if text.strip():
            self.tune["voices"][self.lang.currentData()] = text.strip()

    def _preset(self):
        key = self.preset.currentData()
        if key:
            for k, v in PRESETS[key].items():
                self.fx_sliders[k].setValue(v)

    def current(self) -> dict:
        self.tune["rate"] = f"{self.rate.value():+d}%"
        self.tune["pitch"] = f"{self.pitch.value():+d}Hz"
        self.tune["fx"] = {k: s.value() for k, s in self.fx_sliders.items()}
        return {**self.tune, "voices": dict(self.tune["voices"]), "fx": dict(self.tune["fx"])}

    def _test(self):
        self.play(self.current(), self.phrase.text().strip() or SAMPLES[self.lang.currentData()], self.lang.currentData())

    def _save(self):
        self.saved.emit(self.current())
        self.accept()
