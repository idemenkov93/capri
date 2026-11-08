"""Глобальные горячие клавиши (работают в любой программе), без хуков клавиатуры: опрос GetAsyncKeyState
раз в 30 мс в отдельном потоке — это почти ноль процессора.

Все сочетания Капри — на Win+Alt+буква: Windows занимает из них лишь несколько (B, D, G, H, K, M, R, T, цифры,
стрелки, PrtScn — их не берём), а программы Win-сочетания почти не используют. Ctrl+Shift+… занят везде
(Ctrl+Shift+T в браузере открывает закрытую вкладку, Ctrl+Shift+Q закрывает Firefox), а Ctrl+Alt на немецкой
клавиатуре — это AltGr (@, €, {, [). Опрос не «съедает» нажатие — поэтому пересечения особенно вредны:
сработало бы и у Капри, и в программе.

Таблица настройки: «горячие клавиши» в чате или меню → ⌨ (ui/hotkeys_dialog.py). Хранятся в config.yaml → hotkeys.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "rwin": 0x5C, "space": 0x20,
      **{chr(c).lower(): c for c in range(0x41, 0x5B)}, **{str(d): 0x30 + d for d in range(10)},
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}
MODS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}


@dataclass(frozen=True)
class Hotkey:
    id: str
    title: str
    default: str
    group: str
    hint: str = ""
    hold: bool = False          # держать (диктовка), а не нажать


ACTIONS: list[Hotkey] = [
    Hotkey("chat", "Открыть / спрятать чат", "Win+Alt+O", "Общее", "O — Open"),
    Hotkey("dictate", "Диктовка: держи и говори — текст туда, где курсор", "Ctrl+Win", "Общее", "держать", hold=True),
    Hotkey("translate", "Перевести выделенное (ничего не выделено — буфер)", "Win+Alt+X", "Текст",
           "X — как ⇄; язык — в окне перевода"),
    Hotkey("explain", "Объяснить выделенное: слово — перевод и смысл, текст — объяснение", "Win+Alt+E", "Текст",
           "E — Explain; слово из бестиария — сразу"),
    Hotkey("clipboard", "Что в буфере? (текст, скриншот, файл, ссылка)", "Win+Alt+C", "Текст", "C — Clipboard"),
    Hotkey("screen", "Переведи, что на экране", "Win+Alt+S", "Текст", "S — Screen"),
    Hotkey("hint", "💡 Что спросил учитель и как ответить", "Win+Alt+Q", "Урок", "Q — Question"),
    Hotkey("answer", "💬 Хочу ответить: по-русски → фраза по-немецки", "Win+Alt+A", "Урок", "A — Answer"),
    Hotkey("phrases", "🗣 Готовые фразы для урока (меню у мыши)", "Win+Alt+F", "Урок", "F — фразы"),
    Hotkey("subtitles", "🎧 Субтитры: включить / выключить", "Win+Alt+U", "Урок", "U — Untertitel"),
    Hotkey("mentor", "🎓 Спросить наставника", "Win+Alt+N", "Учёба", "N — Наставник"),
    Hotkey("convert", "🔄 Конвертер для выделенных файлов", "Win+Alt+V", "Файлы", "V — conVert"),
]
BY_ID = {a.id: a for a in ACTIONS}

# старые сочетания по умолчанию (до 1.9) — пересекались с браузерами/Excel; у кого так и осталось, переезжаем на новые
OLD_DEFAULTS = {"translate": "Ctrl+Shift+T", "chat": "Ctrl+Shift+Space", "convert": "Ctrl+Shift+Q",
                "answer": "Ctrl+Shift+1"}

# известные чужие сочетания: предупреждаем в таблице
KNOWN = {
    "ctrl+shift+t": "браузер: открыть закрытую вкладку", "ctrl+shift+n": "браузер: окно инкогнито",
    "ctrl+shift+w": "браузер: закрыть окно", "ctrl+shift+q": "Firefox: выйти из браузера",
    "ctrl+shift+delete": "браузер: очистить историю", "ctrl+shift+i": "браузер: инструменты разработчика",
    "ctrl+shift+j": "браузер: консоль", "ctrl+shift+b": "браузер: панель закладок",
    "ctrl+shift+esc": "диспетчер задач", "ctrl+shift+space": "Excel: выделить всё",
    "ctrl+shift+1": "Excel: числовой формат", "ctrl+shift+u": "Teams: субтитры", "ctrl+shift+m": "Teams: микрофон",
    "ctrl+shift+o": "Teams: камера", "ctrl+shift+e": "Teams: показать экран", "ctrl+shift+k": "Teams: поднять руку",
    "ctrl+shift+h": "Teams: завершить звонок", "ctrl+shift+a": "Teams: принять звонок",
    "ctrl+shift+d": "Teams: отклонить звонок", "ctrl+shift+c": "браузер: выбрать элемент",
    "ctrl+shift+v": "вставить без форматирования", "ctrl+shift+s": "сохранить как",
    "ctrl+shift+p": "VS Code / браузер: команды, приватное окно", "ctrl+shift+f": "поиск по файлам",
    "ctrl+alt+delete": "Windows", "ctrl+alt+q": "немецкая клавиатура: @", "ctrl+alt+e": "немецкая клавиатура: €",
    "ctrl+alt+m": "немецкая клавиатура: µ", "ctrl+alt+7": "немецкая клавиатура: {", "ctrl+alt+8": "немецкая клавиатура: [",
    "ctrl+alt+9": "немецкая клавиатура: ]", "ctrl+alt+0": "немецкая клавиатура: }", "ctrl+alt+2": "немецкая клавиатура: ²",
    "ctrl+alt+3": "немецкая клавиатура: ³",
    "win+alt+b": "Windows: HDR", "win+alt+d": "Windows: дата и время", "win+alt+g": "Xbox Game Bar: запись",
    "win+alt+h": "Windows: голосовой ввод", "win+alt+k": "Windows: микрофон в звонке", "win+alt+m": "Game Bar: микрофон",
    "win+alt+r": "Game Bar: запись экрана", "win+alt+t": "Game Bar: таймер записи", "win+alt+space": "PowerToys: Command Palette",
    "win+shift+s": "Windows: снимок экрана", "win+shift+m": "Windows: вернуть окна", "win+shift+c": "Windows",
    "win+shift+v": "Windows: уведомления / PowerToys", "win+shift+t": "PowerToys: текст с экрана",
    "win+ctrl+d": "Windows: новый рабочий стол", "win+ctrl+q": "Windows: быстрая помощь", "win+ctrl+c": "Windows: цветофильтры",
    "alt+f4": "закрыть окно", "alt+tab": "переключение окон", "alt+space": "PowerToys Run / меню окна",
    "ctrl+space": "автодополнение / смена языка ввода",
    **{f"win+{c}": "Windows" for c in "abcdefghiklmnopqrstuvwxz0123456789"},
    **{f"win+alt+{d}": "Windows: список переходов" for d in "0123456789"},
    **{f"ctrl+{c}": "стандартное сочетание программ" for c in "abcdefghijklmnopqrstuvwxyz"},
    **{f"alt+{c}": "меню программ (Alt+буква)" for c in "abcdefghijklmnopqrstuvwxyz"},
}


def norm(combo: str) -> str:
    """«win + ALT + x» → «Win+Alt+X» (модификаторы — в одном порядке)."""
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    mods = [m for m in ("Ctrl", "Win", "Alt", "Shift") if m.lower() in [p.lower() for p in parts]]
    keys = [p for p in parts if p.lower() not in MODS]
    keys = [k.upper() if len(k) == 1 else k.capitalize() for k in keys]
    return "+".join(mods + keys)


def parse(combo: str) -> list[int]:
    """«Ctrl+Shift+T» → [0x11, 0x10, 0x54]. Неизвестная клавиша → []."""
    out = []
    for part in combo.lower().replace(" ", "").split("+"):
        if part not in VK:
            return []
        out.append(VK[part])
    return out


def resolve(cfg_hotkeys: dict | None) -> dict[str, str]:
    """Сочетания из config.yaml → итоговые (пустое — выключено; старое по умолчанию — новое по умолчанию)."""
    hk = cfg_hotkeys or {}
    chosen = int(hk.get("v") or 0) >= 2            # сохранено из таблицы — что выбрал, то и оставляем
    out = {}
    for a in ACTIONS:
        v = hk.get(a.id)
        if v is None or (not chosen and OLD_DEFAULTS.get(a.id) and norm(str(v)) == norm(OLD_DEFAULTS[a.id])):
            v = a.default
        out[a.id] = norm(str(v)) if v else ""
    return out


def problems(keys: dict[str, str]) -> dict[str, str]:
    """{id: предупреждение}: повтор внутри Капри, известное чужое сочетание, непонятная клавиша, без модификатора."""
    out = {}
    seen: dict[str, str] = {}
    for a in ACTIONS:
        k = keys.get(a.id, "")
        if not k:
            continue
        low = k.lower()
        if not parse(k):
            out[a.id] = "непонятная клавиша"
            continue
        if not a.hold and not any(m in low.split("+") for m in ("ctrl", "alt", "win")):
            out[a.id] = "нужен Ctrl, Alt или Win — иначе будет срабатывать при обычном наборе"
        elif low in seen:
            out[a.id] = f"уже занято: «{BY_ID[seen[low]].title}»"
        elif low in KNOWN:
            out[a.id] = f"пересекается: {KNOWN[low]}"
        seen.setdefault(low, a.id)
    return out


def label(cfg_hotkeys: dict | None, action: str) -> str:
    return resolve(cfg_hotkeys).get(action, "") or "не назначено"


class HotkeyPoller(QObject):
    """Сигналы приходят в главный поток (Qt сам ставит их в очередь); опрос — в фоновом daemon-потоке."""
    fired = Signal(str)             # нажали сочетание действия (id из ACTIONS)
    dictate = Signal(bool)          # True — начали держать сочетание диктовки, False — отпустили
    dictate_cancel = Signal()       # это было Ctrl+Win+стрелка и т.п. — записанное выбросить

    def __init__(self, keys: dict[str, str] | None = None):
        super().__init__()
        self.set_keys(keys or resolve(None))
        self._stop = False
        self.paused = False         # открыта таблица настройки — нажатия ловит она, а не Капри
        self._thread = None

    def set_keys(self, keys: dict[str, str]):
        """Можно менять на ходу (таблица настройки) — поток подхватит со следующего опроса."""
        self.keys = dict(keys)
        self._codes = {k: parse(v) for k, v in keys.items() if v and k != "dictate"}
        self.dict_keys = parse(keys.get("dictate", ""))

    def start(self):
        import threading
        self._thread = threading.Thread(target=self.run, name="hotkeys", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def wait(self, ms: int = 500):
        if self._thread:
            self._thread.join(ms / 1000)

    @staticmethod
    def exact(vks: list[int], pressed) -> bool:
        """Нажато именно это сочетание: все его клавиши зажаты, а лишних модификаторов нет
        (Win+Alt+Shift+X — уже не Win+Alt+X)."""
        if not vks or not all(pressed(v) for v in vks):
            return False
        return not any(pressed(m) for m in MODS.values() if m not in vks)

    def run(self):
        if sys.platform != "win32":
            return
        import ctypes
        get = ctypes.windll.user32.GetAsyncKeyState

        def pressed(v):
            return bool(get(v) & 0x8000) or (v == 0x5B and bool(get(0x5C) & 0x8000))

        import time
        was: dict[str, bool] = {}
        held_since = 0.0            # когда зажали сочетание диктовки
        recording = cancelled = False
        others = (0x25, 0x26, 0x27, 0x28, 0x44, 0x73, 0x09)   # стрелки, D, F4, Tab — это Ctrl+Win+… Windows, не диктовка
        while not self._stop:
            if self.paused:
                time.sleep(0.1)
                continue
            for aid, vks in list(self._codes.items()):
                on = self.exact(vks, pressed)
                if on and not was.get(aid):
                    self.fired.emit(aid)
                was[aid] = on
            dk = bool(self.dict_keys) and all(pressed(v) for v in self.dict_keys) and \
                not pressed(0x10) and not pressed(0x12)
            now = time.monotonic()
            if dk:
                if not held_since:
                    held_since, cancelled = now, False
                if any(pressed(v) for v in others) or any(pressed(v) for v in range(0x41, 0x5B) if v not in self.dict_keys):
                    cancelled = True                      # Ctrl+Win+буква — это сочетание, а не диктовка
                    if recording:
                        recording = False
                        self.dictate_cancel.emit()
                elif not recording and not cancelled and now - held_since >= self.HOLD:
                    recording = True              # держит дольше 0,35 с — значит, диктует
                    self.dictate.emit(True)
            else:
                if recording:
                    self.dictate.emit(False)
                held_since, recording = 0.0, False
            time.sleep(0.03)

    HOLD = 0.35
