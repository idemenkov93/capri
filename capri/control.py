"""Мышь, клавиатура и окна «по-настоящему»: печать текста, прокрутка, клики, окна,
и клик по кнопке по её названию (Windows UI Automation — без нейросети и без видеокарты).

Капри действует в окне, с которым ты работал последним (не в своём чате): app.py раз в секунду
запоминает последнее «чужое» активное окно, и перед действием Капри возвращает его на передний план.
"""
from __future__ import annotations

import ctypes
import difflib
import os
import re
import sys
import time

IS_WIN = sys.platform == "win32"
last_external_hwnd = 0           # последнее активное окно не Капри (обновляет app.py)

# --------------------------------------------------------------------- Win32
if IS_WIN:
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
MOUSE = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
MOUSEEVENTF_WHEEL = 0x0800


def _send(*inputs) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def own_pid_window(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def remember_foreground() -> None:
    """Вызывается раз в секунду: запоминаем последнее окно, которое не Капри."""
    global last_external_hwnd
    if not IS_WIN:
        return
    h = user32.GetForegroundWindow()
    if h and not own_pid_window(h):
        last_external_hwnd = h


def target_window() -> int:
    """Окно, в котором действовать: активное, а если активна сама Капри — последнее «чужое», и выводим его вперёд."""
    h = user32.GetForegroundWindow()
    if h and not own_pid_window(h):
        return h
    h = last_external_hwnd
    if h and user32.IsWindow(h):
        user32.keybd_event(0x12, 0, 0, 0)          # Alt: Windows разрешает сменить активное окно
        user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)                 # SW_RESTORE
        user32.SetForegroundWindow(h)
        time.sleep(0.25)
        return h
    raise RuntimeError("не знаю, в каком окне — щёлкни по нужному окну и повтори")


def window_title(h: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, buf, 256)
    return buf.value


# --------------------------------------------------------------------- действия
def type_text(text: str) -> None:
    """Печатает любым языком (Unicode), не зависит от раскладки."""
    target_window()
    for ch in text:
        if ch == "\n":
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, KEYEVENTF_KEYUP, 0)
            continue
        code = ord(ch)
        down = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)))
        up = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        _send(down, up)
        time.sleep(0.008)


def scroll(clicks: int) -> None:
    """clicks > 0 — вверх, < 0 — вниз (один «щелчок» колёсика = 120)."""
    h = target_window()
    # колёсико крутит то окно, над которым мышь: если мышь не над целевым окном — ставим её в его центр
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    if not (r.left <= pt.x <= r.right and r.top <= pt.y <= r.bottom):
        user32.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    for _ in range(abs(clicks)):
        _send(INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 120 if clicks > 0 else -120, MOUSEEVENTF_WHEEL, 0, 0))))
        time.sleep(0.03)


def click(button: str = "left", double: bool = False, at: tuple[int, int] | None = None) -> None:
    if at:
        user32.SetCursorPos(int(at[0]), int(at[1]))
        time.sleep(0.05)
    dn, up = MOUSE[button]
    for _ in range(2 if double else 1):
        _send(INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, dn, 0, 0))),
              INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, up, 0, 0))))
        time.sleep(0.06)


def window(action: str) -> str:
    """minimize | maximize | restore | close | left | right — с активным окном (не Капри)."""
    h = target_window()
    title = window_title(h)
    if action == "minimize":
        user32.ShowWindow(h, 6)
    elif action == "maximize":
        user32.ShowWindow(h, 3)
    elif action == "restore":
        user32.ShowWindow(h, 9)
    elif action == "close":
        user32.PostMessageW(h, 0x0010, 0, 0)      # WM_CLOSE — как крестик: программа сама спросит про несохранённое
    elif action in ("left", "right"):
        vk = 0x25 if action == "left" else 0x27   # Win+стрелка — «прилепить» к половине экрана
        user32.keybd_event(0x5B, 0, 1, 0)
        user32.keybd_event(vk, 0, 1, 0)
        user32.keybd_event(vk, 0, 1 | KEYEVENTF_KEYUP, 0)
        user32.keybd_event(0x5B, 0, 1 | KEYEVENTF_KEYUP, 0)
    return title


def mouse_center() -> None:
    h = target_window()
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    user32.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)


# --------------------------------------------------------------------- клик по названию (UI Automation)
SYNONYMS = {
    "сохранить": ["save"], "отправить": ["send", "submit"], "отмена": ["cancel"], "отменить": ["cancel"],
    "закрыть": ["close"], "файл": ["file"], "правка": ["edit"], "вид": ["view"], "настройки": ["settings", "preferences", "options"],
    "параметры": ["settings", "options"], "далее": ["next", "weiter"], "назад": ["back", "zurück"], "войти": ["sign in", "log in", "login", "anmelden"],
    "поиск": ["search", "suche"], "удалить": ["delete", "remove", "löschen"], "открыть": ["open", "öffnen"], "готово": ["done", "fertig"],
    "да": ["yes", "ja"], "нет": ["no", "nein"], "ок": ["ok"], "окей": ["ok"], "принять": ["accept", "akzeptieren"],
    "согласен": ["agree", "accept"], "продолжить": ["continue", "fortfahren"], "обновить": ["refresh", "reload", "update"],
    "скачать": ["download", "herunterladen"], "загрузить": ["upload", "hochladen"], "копировать": ["copy", "kopieren"],
    "вставить": ["paste", "einfügen"], "справка": ["help", "hilfe"], "новый": ["new", "neu"], "создать": ["create", "new"],
    "выйти": ["sign out", "log out", "exit", "abmelden"], "подписаться": ["subscribe"], "лайк": ["like"], "нравится": ["like"],
    "воспроизвести": ["play"], "пауза": ["pause"], "следующее": ["next"], "полный экран": ["full screen", "fullscreen"],
}
RISKY = re.compile(r"удал|delete|remove|lösch|отправ|send|submit|оплат|pay|купить|buy|kaufen|подтвер|confirm|выйти|sign out|log ?out|"
                   r"установ|install|format|форматир|сброс|reset", re.I)
CLICKABLE = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "TabItemControl", "ListItemControl", "CheckBoxControl",
             "RadioButtonControl", "SplitButtonControl", "TreeItemControl", "ComboBoxControl", "TextControl", "ImageControl",
             "EditControl", "DataItemControl"}


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w ]+", " ", (s or "").lower().replace("ё", "е")).split())


def name_score(want: str, name: str) -> float:
    """Похожесть названия: точно / начинается с / содержит / почти так же (ошибки Whisper)."""
    w, n = _norm(want), _norm(name)
    if not w or not n:
        return 0.0
    options = [w] + SYNONYMS.get(w, [])
    best = 0.0
    for o in options:
        if n == o:
            return 1.0
        if n.startswith(o + " ") or n.endswith(" " + o):
            best = max(best, 0.9)
        elif len(o) >= 4 and o in n:
            best = max(best, 0.8)
        best = max(best, difflib.SequenceMatcher(None, o, n).ratio() * 0.95)
    return best


def find_element(name: str, hwnd: int | None = None, limit: int = 4000, timeout: float = 4.0):
    """Ищет кнопку/пункт меню/ссылку с таким названием в окне. Возвращает (элемент, найденное название, счёт)."""
    import uiautomation as auto
    root = auto.ControlFromHandle(hwnd or target_window())
    best, best_s, best_name = None, 0.0, ""
    t0, seen = time.monotonic(), 0
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=25):
        seen += 1
        if seen > limit or time.monotonic() - t0 > timeout:
            break
        try:
            if ctrl.ControlTypeName not in CLICKABLE or ctrl.IsOffscreen:
                continue
            nm = ctrl.Name
        except Exception:  # noqa: BLE001 — элементы исчезают на ходу
            continue
        s = name_score(name, nm)
        # настоящие кнопки важнее подписей с тем же текстом
        if ctrl.ControlTypeName in ("TextControl", "ImageControl"):
            s -= 0.05
        if s > best_s:
            best, best_s, best_name = ctrl, s, nm
            if s >= 1.0 and ctrl.ControlTypeName != "TextControl":
                break
    return best, best_name, best_s


def click_element(ctrl) -> None:
    """Кликаем настоящей мышью в центр элемента; если координат нет — нажимаем «программно» (Invoke)."""
    r = ctrl.BoundingRectangle
    if r.width() > 0 and r.height() > 0:
        click(at=((r.left + r.right) // 2, (r.top + r.bottom) // 2))
        return
    pattern = ctrl.GetInvokePattern() if hasattr(ctrl, "GetInvokePattern") else None
    if pattern:
        pattern.Invoke()
    else:
        raise RuntimeError("не получилось нажать — у элемента нет координат")
