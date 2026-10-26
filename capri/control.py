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


# --------------------------------------------------------------------- мониторы
APP_EXE = {
    "хром": "chrome.exe", "гугл хром": "chrome.exe", "chrome": "chrome.exe", "браузер": None, "browser": None,
    "эдж": "msedge.exe", "edge": "msedge.exe", "файрфокс": "firefox.exe", "фаерфокс": "firefox.exe", "firefox": "firefox.exe",
    "опера": "opera.exe", "opera": "opera.exe", "яндекс": "browser.exe", "телеграм": "telegram.exe", "telegram": "telegram.exe",
    "дискорд": "discord.exe", "discord": "discord.exe", "спотифай": "spotify.exe", "spotify": "spotify.exe",
    "проводник": "explorer.exe", "explorer": "explorer.exe", "блокнот": "notepad.exe", "notepad": "notepad.exe",
    "ворд": "winword.exe", "word": "winword.exe", "эксель": "excel.exe", "excel": "excel.exe", "стим": "steam.exe",
    "steam": "steam.exe", "вс код": "code.exe", "вскод": "code.exe", "vs code": "code.exe", "код": "code.exe",
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


def find_app_window(name: str | None) -> int:
    """Главное окно программы по названию («хром», «телеграм»); None — текущее окно."""
    if not name:
        return target_window()
    key = re.sub(r"^(?:окно|программу|приложение|window)\s+", "", name.lower().strip())
    exe = APP_EXE.get(key) or (key if key.endswith(".exe") else None)
    if not exe and key not in ("браузер", "browser"):
        # «хрома», «телеграмм», «проводника» — по началу слова
        exe = next((v for k, v in APP_EXE.items() if v and len(k) >= 4 and len(key) >= 3 and (key.startswith(k[:-1]) or k.startswith(key))), None)
    want = (exe,) if exe else BROWSERS if key in ("браузер", "browser") else ()
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
        found = [h for h in found if "youtube music" in window_title(h).lower()] or found
    return found[0] if found else 0


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
