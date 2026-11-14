"""Тонкая обёртка над UI Automation (пакет uiautomation). Только чтение, ничего не нажимаем.

Здесь всё, что зависит от Windows. Остальной код получает объект «auto» и работает с ним,
поэтому в тестах его легко подменить поддельным деревом.
"""
from __future__ import annotations

import ctypes
import os

# номера из документации Windows (UIAutomationClient.h)
UIA_AUTOMATION_ID = 30011
TREE_SCOPE_DESCENDANTS = 4


def load():
    """Подключить uiautomation. Вызывать в том потоке, который будет читать окна."""
    import uiautomation as auto
    return auto


def safe(getter, default=None):
    """UI Automation бросает ошибки на исчезнувших элементах — тогда возвращаем default."""
    try:
        value = getter()
        return default if value is None else value
    except Exception:  # noqa: BLE001
        return default


def proc_name(pid: int) -> str:
    """Имя процесса по номеру, без сторонних библиотек."""
    if os.name != "nt" or not pid:
        return ""
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, int(pid))        # 0x1000 = только узнать имя
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        ok = k32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def top_windows(auto, procs: set[str], get_proc=None) -> list:
    """Окна верхнего уровня, принадлежащие процессам из procs."""
    get_proc = get_proc or proc_name
    out = []
    for w in safe(lambda: auto.GetRootControl().GetChildren(), []):
        if get_proc(safe(lambda: w.ProcessId, 0)) in procs:
            out.append(w)
    return out


def find_by_ids_fast(auto, root, ids: list[str]):
    """Быстрый поиск по id: один запрос к Windows, дерево обходит сам Teams.
    Возвращает (элемент или None, получилось ли вообще спросить)."""
    try:
        ia = auto._AutomationClient.instance().IUIAutomation
        cond = None
        for i in ids:
            one = ia.CreatePropertyCondition(UIA_AUTOMATION_ID, i)
            cond = one if cond is None else ia.CreateOrCondition(cond, one)
        element = root.Element.FindFirst(TREE_SCOPE_DESCENDANTS, cond)
        return (auto.Control.CreateControlFromElement(element) if element else None), True
    except Exception:  # noqa: BLE001
        return None, False


def find_by_ids_slow(auto, root, ids: list[str], limit: int = 4000):
    """Медленный поиск: обходим дерево сами и читаем только id."""
    wanted, n = set(ids), 0
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=60):
        n += 1
        if n > limit:
            break
        if safe(lambda: ctrl.AutomationId, "") in wanted:
            return ctrl
    return None


def find_by_ids(auto, root, ids: list[str], allow_slow: bool = True):
    """Элемент с одним из id внутри root. Сначала быстрый путь, при неудаче — медленный."""
    found, asked = find_by_ids_fast(auto, root, ids)
    if found is not None:
        return found
    if allow_slow or not asked:
        return find_by_ids_slow(auto, root, ids)
    return None


def first_with_class(auto, root, prefix: str, limit: int = 600):
    """Первый элемент внутри root, чей класс начинается с prefix."""
    n = 0
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=12):
        n += 1
        if n > limit:
            break
        if str(safe(lambda: ctrl.ClassName, "")).startswith(prefix):
            return ctrl
    return None
