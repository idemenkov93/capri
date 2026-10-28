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


def focus(h: int) -> int:
    """Вывести окно на передний план (с Alt-трюком, иначе Windows не даёт)."""
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)
    user32.SetForegroundWindow(h)
    time.sleep(0.25)
    return h


def browser_window() -> int:
    """Окно браузера, с которым работаешь: текущее, если это браузер, иначе верхнее окно браузера."""
    h = target_window()
    if process_name(h) in BROWSERS:
        return h
    b = find_app_window("браузер")
    return focus(b) if b else h


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


def process_name(h: int) -> str:
    """chrome.exe, spotify.exe… — по окну."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    k32 = ctypes.windll.kernel32
    hp = k32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if k32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(hp)


BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe", "browser.exe")


def context_window() -> tuple[str, str]:
    """(заголовок, программа) окна, с которым ты работаешь, — без переключения окон."""
    if not IS_WIN:
        return "", ""
    h = user32.GetForegroundWindow()
    if not h or own_pid_window(h):
        h = last_external_hwnd
    if not h or not user32.IsWindow(h):
        return "", ""
    return window_title(h), process_name(h)


def media_context() -> str | None:
    """Что сейчас перед тобой: music (YouTube Music и т.п.) | spotify | browser | None."""
    title, exe = context_window()
    t = title.lower()
    if "youtube music" in t or "ютуб мюзик" in t:
        return "music"
    if exe == "spotify.exe" or "spotify" in t:
        return "spotify"
    if exe in BROWSERS:
        return "browser"
    return None


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


def window(action: str, name: str | None = None) -> str:
    """minimize | maximize | restore | close | left | right — с активным окном (не Капри) или с окном программы name."""
    if name:
        h = find_app_window(name)
        if not h:
            raise RuntimeError(f"не нашла окно «{name}»")
        title = window_title(h)
        if action == "close" and process_name(h) in BROWSERS:
            # во вкладке: «закрой YouTube» — закрыть вкладку, а не весь браузер со всеми вкладками
            focus(h)
            _hotkey(0x11, 0x57)                     # Ctrl+W
            return title
        if action in ("restore", "maximize"):
            user32.ShowWindow(h, 9 if action == "restore" else 3)
            focus(h)
            return title
    else:
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


# --------------------------------------------------------------------- мониторы
APP_EXE = {
    "хром": "chrome.exe", "гугл хром": "chrome.exe", "chrome": "chrome.exe", "браузер": None, "browser": None,
    "эдж": "msedge.exe", "edge": "msedge.exe", "файрфокс": "firefox.exe", "фаерфокс": "firefox.exe", "firefox": "firefox.exe",
    "опера": "opera.exe", "opera": "opera.exe", "яндекс": "browser.exe", "телеграм": "telegram.exe", "telegram": "telegram.exe",
    "дискорд": "discord.exe", "discord": "discord.exe", "спотифай": "spotify.exe", "spotify": "spotify.exe",
    "проводник": "explorer.exe", "explorer": "explorer.exe", "блокнот": "notepad.exe", "notepad": "notepad.exe",
    "ворд": "winword.exe", "word": "winword.exe", "эксель": "excel.exe", "excel": "excel.exe", "стим": ("steam.exe", "steamwebhelper.exe"),
    "steam": ("steam.exe", "steamwebhelper.exe"), "вс код": "code.exe", "вскод": "code.exe", "vs code": "code.exe", "код": "code.exe",
    "терминал": "windowsterminal.exe", "павершел": "powershell.exe", "powershell": "powershell.exe",
    "ютуб мюзик": "chrome.exe", "youtube music": "chrome.exe", "обс": "obs64.exe", "obs": "obs64.exe",
}


def monitors() -> list[tuple[int, int, int, int, bool]]:
    """Мониторы: (left, top, right, bottom рабочей области, главный?). Главный — первым, остальные слева направо."""
    out = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w = mi.rcWork
        out.append((w.left, w.top, w.right, w.bottom, bool(mi.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return sorted(out, key=lambda m: (not m[4], m[0], m[1]))


def monitor_of(h: int, mons: list) -> int:
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    for i, (l, t, rr, b, _p) in enumerate(mons):
        if l <= cx < rr and t <= cy < b:
            return i
    return 0


def find_app_window(name: str | None, strict: bool = False) -> int:
    """Главное окно программы по названию («хром», «телеграм»); None — текущее окно."""
    if not name:
        return target_window()
    key = re.sub(r"^(?:окно|программу|приложение|window)\s+", "", name.lower().strip())
    exe = APP_EXE.get(key) or (key if key.endswith(".exe") else None)
    if not exe and key not in ("браузер", "browser") and " " not in key:
        # «хрома», «телеграмм», «проводника» — по началу слова
        exe = next((v for k, v in APP_EXE.items() if v and len(k) >= 4 and len(key) >= 3 and (key.startswith(k[:-1]) or k.startswith(key))), None)
    want = (exe if isinstance(exe, tuple) else (exe,)) if exe else BROWSERS if key in ("браузер", "browser") else ()
    found: list[int] = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _lp):
        if not user32.IsWindowVisible(h) or own_pid_window(h) or user32.GetWindow(h, 4):   # 4 = GW_OWNER: не диалоги
            return True
        title = window_title(h)
        if not title:
            return True
        if want and process_name(h) in want:
            found.append(h)
        elif not want and difflib.SequenceMatcher(None, key, title.lower()).ratio() > 0.6 or (not want and key in title.lower()):
            found.append(h)
        return True

    user32.EnumWindows(PROC(cb), 0)          # окна идут сверху вниз — первое и есть последнее активное
    if key in ("ютуб мюзик", "youtube music"):
        music = [h for h in found if "youtube music" in window_title(h).lower()]
        found = music if (music or strict) else found
    return found[0] if found else 0


def connected_displays() -> int:
    """Сколько мониторов подключено к видеокартам — даже выключенных через Win+P (monitors() видит только включённые)."""
    if not IS_WIN:
        return 0

    class DISPLAY_DEVICE(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                    ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]
    n, i = 0, 0
    while True:
        dev = DISPLAY_DEVICE(cb=ctypes.sizeof(DISPLAY_DEVICE))
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
            break
        i += 1
        if dev.StateFlags & 0x8:                    # DISPLAY_DEVICE_MIRRORING_DRIVER — не настоящий выход
            continue
        mon = DISPLAY_DEVICE(cb=ctypes.sizeof(DISPLAY_DEVICE))
        if user32.EnumDisplayDevicesW(dev.DeviceName, 0, ctypes.byref(mon), 0) and mon.DeviceID:
            n += 1                                  # к этому выходу подключён монитор
    return n


def switch_display(mode: str, wait: float = 6.0) -> int:
    """Как Win+P: extend (оба), internal (только основной), external (только второй), clone (дублировать).
    Ждём, пока Windows перестроит экраны, и возвращаем, сколько их теперь включено."""
    import subprocess
    want = {"extend": 2, "clone": 1, "internal": 1, "external": 1}[mode]
    subprocess.Popen(["DisplaySwitch.exe", f"/{mode}"], creationflags=0x08000000)
    t0 = time.monotonic()
    time.sleep(1.0)
    while time.monotonic() - t0 < wait:
        if (len(monitors()) >= 2) == (want >= 2):
            time.sleep(1.0)                         # окна ещё переезжают
            break
        time.sleep(0.4)
    return len(monitors())


def wait_window(name: str, timeout: float = 20.0, ignore: set | None = None) -> int:
    """Дождаться окна программы после запуска (Steam и Discord открываются по 5–15 секунд)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        h = find_app_window(name, strict=True)
        if h and (not ignore or h not in ignore):
            return h
        time.sleep(0.5)
    return find_app_window(name, strict=True)


def _frame_margins(h: int) -> tuple[int, int, int, int]:
    """Невидимые рамки окна Windows 10/11 (тени по 7 px): без поправки половинки экрана получаются с щелями."""
    r, f = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 9, ctypes.byref(f), ctypes.sizeof(f)) != 0:
        return 0, 0, 0, 0
    return f.left - r.left, f.top - r.top, r.right - f.right, r.bottom - f.bottom


def place_window(h: int, screen: int = 1, place: str = "max") -> str:
    """Поставить окно на экран screen (1 — основной, 2 — второй…): max | left | right | top | bottom | center."""
    mons = monitors()
    l, t, r, b, _ = mons[min(max(screen, 1), len(mons)) - 1]
    if user32.IsIconic(h) or user32.IsZoomed(h):
        user32.ShowWindow(h, 9)                  # SW_RESTORE
        time.sleep(0.15)
    w, hh = r - l, b - t
    box = {"left": (l, t, w // 2, hh), "right": (l + w // 2, t, w - w // 2, hh), "top": (l, t, w, hh // 2),
           "bottom": (l, t + hh // 2, w, hh - hh // 2), "center": (l + w // 6, t + hh // 8, w * 2 // 3, hh * 3 // 4)}.get(place)
    if box is None:                                # max: сначала на нужный экран, потом развернуть
        user32.SetWindowPos(h, 0, l + 40, t + 40, w * 2 // 3, hh * 2 // 3, 0x0004 | 0x0040)
        user32.ShowWindow(h, 3)
    else:
        ml, mt, mr, mb = _frame_margins(h)
        x, y, cw, ch = box
        user32.SetWindowPos(h, 0, x - ml, y - mt, cw + ml + mr, ch + mt + mb, 0x0004 | 0x0040)
    return window_title(h)


def placed_ok(h: int, screen: int = 1, place: str = "max", tol: int = 24) -> bool:
    """Окно стоит там, куда мы его ставили? (Discord после загрузки сам разворачивается во весь экран.)"""
    mons = monitors()
    l, t, r, b, _ = mons[min(max(screen, 1), len(mons)) - 1]
    if place == "max":
        return bool(user32.IsZoomed(h))
    if user32.IsZoomed(h) or user32.IsIconic(h):
        return False
    w, hh = r - l, b - t
    want = {"left": (l, t, w // 2, hh), "right": (l + w // 2, t, w - w // 2, hh), "top": (l, t, w, hh // 2),
            "bottom": (l, t + hh // 2, w, hh - hh // 2), "center": (l + w // 6, t + hh // 8, w * 2 // 3, hh * 3 // 4)}.get(place)
    if want is None:
        return True
    rect = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(h, ctypes.byref(rect))
    x, y, cw, ch = want
    return abs(rect.left - x) <= tol and abs(rect.top - y) <= tol and abs(rect.right - (x + cw)) <= tol


def idle_seconds() -> float:
    """Сколько секунд к компьютеру не прикасались (мышь/клавиатура)."""
    if not IS_WIN:
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    li = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return max(0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


NIGHT_ON = re.compile(r"включить сейчас|turn on now|jetzt einschalten|jetzt aktivieren", re.I)
NIGHT_OFF = re.compile(r"выключить сейчас|turn off now|jetzt ausschalten|jetzt deaktivieren", re.I)
NIGHT_TOGGLE = re.compile(r"ночн\w* свет|night ?light|nachtmodus|nachtlicht", re.I)


def night_light(on: bool = True, timeout: float = 8.0) -> bool:
    """Стандартный «Ночной свет» Windows: программам его включать не дают, поэтому открываем его страницу
    в «Параметрах» и нажимаем кнопку «Включить сейчас» (или переключатель) так же, как нажал бы ты."""
    from . import nightlight
    if nightlight.set_on(on):                         # быстрый путь: сразу в настройку Windows, без окна
        return True
    import uiautomation as auto
    os.startfile("ms-settings:nightlight")          # noqa: S606
    with auto.UIAutomationInitializerInThread():
        t0 = time.monotonic()
        win = 0
        while time.monotonic() - t0 < timeout and not win:
            time.sleep(0.5)
            win = find_app_window("systemsettings.exe", strict=True)
        if not win:
            return False
        time.sleep(1.0)                               # страница дорисовывается
        root = auto.ControlFromHandle(win)
        want = NIGHT_ON if on else NIGHT_OFF
        done = False
        for ctrl, _d in auto.WalkControl(root, includeTop=False, maxDepth=30):
            try:
                name = ctrl.Name or ""
                kind = ctrl.ControlTypeName
            except Exception:  # noqa: BLE001
                continue
            if kind == "ButtonControl" and want.search(name):
                click_element(ctrl)
                done = True
                break
            if kind in ("ButtonControl", "CheckBoxControl") and NIGHT_TOGGLE.search(name):
                try:
                    tp = ctrl.GetTogglePattern()
                    if tp and bool(tp.ToggleState) != on:
                        tp.Toggle()
                    done = True
                    break
                except Exception:  # noqa: BLE001
                    continue
        if done:
            time.sleep(0.5)
            user32.PostMessageW(win, 0x0010, 0, 0)   # закрыть «Параметры»
        return done


def move_to_monitor(name: str | None, where: str) -> dict:
    """where: номер «1»/«2»…, «next» (другой), «left», «right». Окно переезжает с тем же размером; развёрнутое — снова развёрнуто."""
    h = find_app_window(name)
    if not h:
        return {"error": f"не нашла окно «{name}»"}
    mons = monitors()
    if len(mons) < 2:
        return {"error": "у тебя подключён только один монитор"}
    cur = monitor_of(h, mons)
    if where == "next":
        dst = (cur + 1) % len(mons)
    elif where in ("left", "right"):
        by_x = sorted(range(len(mons)), key=lambda i: mons[i][0])
        pos = by_x.index(cur)
        dst = by_x[max(0, pos - 1)] if where == "left" else by_x[min(len(by_x) - 1, pos + 1)]
    else:
        n = int(where)
        if not 1 <= n <= len(mons):
            return {"error": f"монитора номер {n} нет — их у тебя {len(mons)}"}
        dst = n - 1
    title = window_title(h)
    if dst == cur:
        return {"title": title, "same": True, "monitor": dst + 1}
    was_max = bool(user32.IsZoomed(h))
    if was_max or user32.IsIconic(h):
        user32.ShowWindow(h, 9)                  # SW_RESTORE — иначе развёрнутое окно не сдвинуть
        time.sleep(0.1)
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    sl, st, sr, sb, _ = mons[cur]
    dl, dt_, dr, db, _ = mons[dst]
    w, hh = min(r.right - r.left, dr - dl), min(r.bottom - r.top, db - dt_)
    x = dl + max(0, min(r.left - sl, dr - dl - w))
    y = dt_ + max(0, min(r.top - st, db - dt_ - hh))
    user32.SetWindowPos(h, 0, x, y, w, hh, 0x0004 | 0x0040)   # SWP_NOZORDER | SWP_SHOWWINDOW
    if was_max:
        user32.ShowWindow(h, 3)
    user32.SetForegroundWindow(h)
    return {"title": title, "monitor": dst + 1}


def _hotkey(*vks: int) -> None:
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


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


def _tok_sim(a: str, b: str) -> float:
    if a.isdigit() or b.isdigit():
        return 1.0 if a.lstrip("0") == b.lstrip("#0") or a == b.lstrip("#") else 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def name_score(want: str, name: str) -> float:
    """Похожесть названия: точно / начинается с / содержит / по словам («подземелье чикен карри 54» ≈
    «Подземелья Чикен Карри #54 Свиток Апокалипсиса») / почти так же (ошибки Whisper)."""
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
        # по словам: каждое слово запроса нашлось в названии (с опечатками), лишние слова в названии не мешают
        ot = [x for x in o.split() if x not in ("выпуск", "серия", "серию", "видео", "ролик", "часть", "эпизод")]
        nt = n.replace("#", " ").split()
        if len(ot) >= 2 and nt:
            sims = [max(_tok_sim(x, y) for y in nt) for x in ot]
            if min(sims) >= 0.7:
                best = max(best, 0.75 + 0.2 * sum(sims) / len(sims))
    return best


def find_element(name: str, hwnd: int | None = None, limit: int = 4000, timeout: float = 4.0):
    """Ищет кнопку/пункт меню/ссылку с таким названием в окне. Возвращает (элемент, найденное название, счёт)."""
    import uiautomation as auto
    root = auto.ControlFromHandle(hwnd or target_window())
    best, best_s, best_name = None, 0.0, ""
    t0, seen = time.monotonic(), 0
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=40):
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


ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8, "девят": 9, "десят": 10,
            "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "erst": 1, "zweit": 2, "dritt": 3, "viert": 4, "fünft": 5}


def ordinal(word: str) -> int | None:
    """«вторую» → 2, «третье» → 3, «последнюю» → -1, «2-ю» → 2."""
    w = word.lower()
    if re.match(r"последн|last|letzt", w):
        return -1
    if m := re.match(r"(\d+)", w):
        return int(m.group(1))
    return next((v for k, v in ORDINALS.items() if w.startswith(k)), None)


def page_links(hwnd: int | None = None, min_len: int = 12, limit: int = 6000, timeout: float = 5.0) -> list:
    """Ссылки на странице браузера сверху вниз (заголовки видео на YouTube, результаты в Google…)."""
    import uiautomation as auto
    h = hwnd or browser_window()
    root = auto.ControlFromHandle(h)
    win = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(win))
    out, seen, t0, n = [], set(), time.monotonic(), 0
    for ctrl, _d in auto.WalkControl(root, includeTop=False, maxDepth=40):
        n += 1
        if n > limit or time.monotonic() - t0 > timeout:
            break
        try:
            if ctrl.ControlTypeName != "HyperlinkControl" or ctrl.IsOffscreen:
                continue
            name = (ctrl.Name or "").strip()
            r = ctrl.BoundingRectangle
        except Exception:  # noqa: BLE001
            continue
        if len(name) < min_len or name in seen or r.width() < 30 or r.top < win.top + 110:
            continue
        seen.add(name)
        out.append((round(r.top / 40), r.left, name, ctrl))
    out.sort(key=lambda x: (x[0], x[1]))
    return [(name, ctrl) for _, _, name, ctrl in out]


PLAYER_BUTTONS = {"play": ("воспроизвести", "play", "wiedergabe"), "pause": ("пауза", "pause"),
                  "next": ("следующий трек", "next", "следующее", "nächster titel"),
                  "previous": ("предыдущий трек", "previous", "vorheriger titel")}
# точные подписи кнопок плеера YouTube Music (ищутся быстрым поиском UI Automation, без обхода всей страницы)
PLAYER_EXACT = {"next": ("Следующий трек", "Следующая композиция", "Далее", "Next", "Next song", "Nächster Titel", "Weiter"),
                "previous": ("Предыдущий трек", "Предыдущая композиция", "Назад", "Previous", "Previous song", "Vorheriger Titel"),
                "play": ("Воспроизвести", "Play", "Wiedergabe"), "pause": ("Пауза", "Приостановить", "Pause")}
# горячие клавиши YouTube Music: Shift+N — следующая песня, Shift+P — предыдущая, пробел — пауза/играть
YTM_KEYS = {"next": (0x10, 0x4E), "previous": (0x10, 0x50), "play": (0x20,), "pause": (0x20,)}


def find_exact(hwnd: int, names) -> object | None:
    """Кнопка с точно таким названием — поиском внутри UI Automation (быстро даже на огромной странице)."""
    import uiautomation as auto
    try:
        from uiautomation import uiautomation as core     # _AutomationClient не экспортируется наружу
        uia = core._AutomationClient.instance().IUIAutomation
        root = auto.ControlFromHandle(hwnd)
    except Exception:  # noqa: BLE001 — другая версия пакета: обойдёмся обычным поиском
        return None
    for name in names:
        try:
            cond = uia.CreatePropertyCondition(30005, name)          # UIA_NamePropertyId
            el = root.Element.FindFirst(4, cond)                     # TreeScope_Descendants
        except Exception:  # noqa: BLE001
            continue
        if el:
            ctrl = auto.Control.CreateControlFromElement(el)
            if ctrl is not None:
                return ctrl
    return None


def ytm_window() -> int:
    for cand in (user32.GetForegroundWindow(), last_external_hwnd):
        if cand and "youtube music" in window_title(cand).lower():
            return cand
    h = find_app_window("youtube music")
    return h if h and "youtube music" in window_title(h).lower() else 0


BAR_NAMES = {"play": ("Воспроизвести", "Play", "Wiedergabe"), "pause": ("Пауза", "Приостановить", "Pause")}


def ytm_bar_button(h: int, action: str) -> str:
    """Кнопка ▶/⏸ в нижней панели плеера YouTube Music. Не переключатель: «играй» при уже играющей музыке ничего
    не делает (раньше пробел ставил её на паузу). → "clicked" | "already" | "" (панель не нашли)."""
    import uiautomation as auto
    try:
        from uiautomation import uiautomation as core
        uia = core._AutomationClient.instance().IUIAutomation
        root = auto.ControlFromHandle(h)
    except Exception:  # noqa: BLE001
        return ""
    win = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(win))
    bar_top = win.bottom - 170

    def in_bar(kind: str):
        for name in BAR_NAMES[kind]:
            try:
                arr = root.Element.FindAll(4, uia.CreatePropertyCondition(30005, name))
            except Exception:  # noqa: BLE001
                continue
            for i in range(arr.Length if arr else 0):
                ctrl = auto.Control.CreateControlFromElement(arr.GetElement(i))
                try:
                    if ctrl is not None and ctrl.BoundingRectangle.top >= bar_top:
                        return ctrl
                except Exception:  # noqa: BLE001
                    continue
        return None
    want, other = (action, "pause" if action == "play" else "play")
    if (btn := in_bar(want)) is not None:
        click_element(btn)
        return "clicked"
    if in_bar(other) is not None:
        return "already"
    return ""


def ytm_play_page(h: int) -> str | None:
    """На открытой странице плейлиста/микса YouTube Music нажать большую кнопку ▶ в шапке."""
    try:
        btn = find_exact(h, ("Воспроизвести", "Воспроизвести все", "Play", "Play all", "Wiedergabe", "Alle abspielen"))
    except Exception:  # noqa: BLE001
        btn = None
    if btn is None:
        return None
    click_element(btn)
    return btn.Name or "▶"


def ytm_tracks(h: int, limit: int = 8000, timeout: float = 6.0) -> list:
    """Треки на странице плейлиста YouTube Music сверху вниз: [(название, ссылка)].
    Берём ссылки справа от боковой панели; под названием трека — ссылка на артиста (на ~27 px ниже), её пропускаем."""
    import uiautomation as auto
    root = auto.ControlFromHandle(h)
    win = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(win))
    left_edge = win.left + (win.right - win.left) * 0.35
    found, t0, n = [], time.monotonic(), 0
    for ctrl, _d in auto.WalkControl(root, includeTop=False, maxDepth=45):
        n += 1
        if n > limit or time.monotonic() - t0 > timeout:
            break
        try:
            if ctrl.ControlTypeName != "HyperlinkControl" or ctrl.IsOffscreen:
                continue
            name = (ctrl.Name or "").strip()
            r = ctrl.BoundingRectangle
        except Exception:  # noqa: BLE001
            continue
        if name and r.left >= left_edge and r.top > win.top + 100 and r.width() > 20:
            found.append((r.top, r.left, name, ctrl))
    found.sort(key=lambda x: (x[0], x[1]))
    out, last_top = [], -10_000
    for top, _l, name, ctrl in found:
        if top - last_top < 45:          # та же строка или подпись под названием
            continue
        out.append((name, ctrl))
        last_top = top
    return out


def ytm_search(h: int, query: str, wait: float = 3.0) -> list:
    """Поиск в самом YouTube Music: поле «Поиск» → текст → Enter. → результаты сверху вниз [(название, ссылка)]."""
    focus(h)
    box = None
    try:
        box = find_exact(h, ("Поиск", "Search", "Suchen"))
    except Exception:  # noqa: BLE001
        box = None
    if box is not None:
        click_element(box)
    else:
        # горячая клавиша «/» (с английской раскладкой — с русской это «.»)
        _with_en_layout(h, lambda: (user32.keybd_event(0xBF, 0, 0, 0), user32.keybd_event(0xBF, 0, KEYEVENTF_KEYUP, 0)))
    time.sleep(0.3)
    _hotkey(0x11, 0x41)                                # Ctrl+A — стереть прошлый запрос
    type_text(query + "\n")
    time.sleep(wait)
    found = ytm_tracks(h)
    if not found:
        time.sleep(1.5)
        found = ytm_tracks(h)
    return found


def in_content_area(h: int, ctrl) -> bool:
    """Элемент в правой, «рабочей» части окна YouTube Music (не в боковом меню и не в шапке плейлиста)."""
    try:
        win = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(win))
        return ctrl.BoundingRectangle.left >= win.left + (win.right - win.left) * 0.35
    except Exception:  # noqa: BLE001
        return False


def ytm_click(h: int, name: str) -> bool:
    """Нажать на странице YouTube Music ссылку с таким названием (трек из результатов или плейлиста)."""
    focus(h)
    for title, ctrl in ytm_tracks(h):
        if title == name or name_score(name, title) >= 0.9:
            click_element(ctrl)
            return True
    return False


def _with_en_layout(h: int, press) -> None:
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
    tid = user32.GetWindowThreadProcessId(h, None)
    before = user32.GetKeyboardLayout(tid) or 0
    en = user32.LoadKeyboardLayoutW("00000409", 1) or 0
    switched = bool(en) and (before & 0xFFFF) != 0x0409
    if switched:
        user32.PostMessageW(h, 0x0050, 0, ctypes.c_void_p(en))
        time.sleep(0.15)
    press()
    if switched:
        time.sleep(0.1)
        user32.PostMessageW(h, 0x0050, 0, ctypes.c_void_p(before))


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                 "f", "h", "ts", "ch", "sh", "sch", "", "i", "", "e", "yu", "ya"]))


def phonetic(s: str) -> str:
    """«он репит» ≈ «On repeat», «классик» ≈ «Classic», «битс» ≈ «Beatz»: латиница по звучанию, без двойных букв."""
    t = "".join(_CYR.get(ch, ch) for ch in s.lower())
    for a, b in (("ph", "f"), ("ck", "k"), ("ch", "ch"), ("qu", "kv"), ("x", "ks"), ("c", "k"), ("q", "k"), ("w", "v"),
                 ("ee", "i"), ("ea", "i"), ("ie", "i"), ("y", "i"), ("oo", "u"), ("ou", "u"), ("z", "s"), ("th", "t"),
                 ("&", " and "), ("ei", "i"), ("ai", "ei")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"(.)\1+", r"\1", t)
    return " ".join(t.split())


def title_score(want: str, name: str) -> float:
    """Похоже ли сказанное на название — и по буквам, и по звучанию (Whisper пишет английские названия по-русски)."""
    return max(name_score(want, name), name_score(phonetic(want), phonetic(name)))


def ytm_sidebar(h: int, limit: int = 4000, timeout: float = 4.0) -> list:
    """Плейлисты в левом меню YouTube Music: [(название, элемент)] сверху вниз."""
    import uiautomation as auto
    root = auto.ControlFromHandle(h)
    win = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(win))
    right_edge = win.left + min(360, (win.right - win.left) * 0.3)
    out, seen, t0, n = [], set(), time.monotonic(), 0
    for ctrl, _d in auto.WalkControl(root, includeTop=False, maxDepth=45):
        n += 1
        if n > limit or time.monotonic() - t0 > timeout:
            break
        try:
            if ctrl.ControlTypeName not in CLICKABLE or ctrl.IsOffscreen:
                continue
            name = (ctrl.Name or "").strip()
            r = ctrl.BoundingRectangle
        except Exception:  # noqa: BLE001
            continue
        if not name or name in seen or r.left > right_edge or r.top < win.top + 80 or r.width() < 20:
            continue
        seen.add(name)
        out.append((r.top, name, ctrl))
    out.sort(key=lambda x: x[0])
    return [(name, ctrl) for _t, name, ctrl in out]


def ytm_hotkey(action: str) -> bool:
    """Горячая клавиша прямо в окне YouTube Music. На время нажатия — английская раскладка:
    с русской браузер видит «Т» вместо N, и YouTube Music клавишу не узнаёт."""
    h = ytm_window()
    if not h or action not in YTM_KEYS:
        return False
    focus(h)
    user32.GetKeyboardLayout.restype = ctypes.c_void_p             # HKL — 64-битное значение
    user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
    tid = user32.GetWindowThreadProcessId(h, None)
    before = user32.GetKeyboardLayout(tid) or 0
    en = user32.LoadKeyboardLayoutW("00000409", 1) or 0
    switched = bool(en) and (before & 0xFFFF) != 0x0409
    if switched:
        user32.PostMessageW(h, 0x0050, 0, ctypes.c_void_p(en))        # WM_INPUTLANGCHANGEREQUEST
        time.sleep(0.15)
    keys = YTM_KEYS[action]
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, KEYEVENTF_KEYUP, 0)
    if switched:
        time.sleep(0.1)
        user32.PostMessageW(h, 0x0050, 0, ctypes.c_void_p(before))
    return True


def press_player_button(action: str, title_has: str = "youtube music") -> str | None:
    """Плеер во вкладке браузера (YouTube Music рядом с YouTube): у них общий «медиа-сеанс» Windows, поэтому
    находим окно/вкладку по заголовку и жмём настоящую кнопку ▶/⏸ на странице. → название кнопки или None."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        h = 0
        fg = user32.GetForegroundWindow()
        for cand in (fg, last_external_hwnd):
            if cand and title_has in window_title(cand).lower():
                h = cand
                break
        if not h:
            h = find_app_window(title_has)
            if not h or title_has not in window_title(h).lower():
                return None
        focus(h)
        try:
            exact = find_exact(h, PLAYER_EXACT.get(action, ()))
        except Exception:  # noqa: BLE001
            exact = None
        if exact is not None:
            click_element(exact)
            return exact.Name or action
        best, best_s, best_name = None, 0.0, ""
        for name in PLAYER_BUTTONS[action]:
            ctrl, found, sc = find_element(name, hwnd=h, limit=6000, timeout=4.0)
            if ctrl is not None and sc > best_s and (found or "").strip().lower() not in ("", "youtube music"):
                best, best_s, best_name = ctrl, sc, found
            if best_s >= 0.95:
                break
        if best is None or best_s < 0.8:
            return None
        click_element(best)
        return best_name


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
