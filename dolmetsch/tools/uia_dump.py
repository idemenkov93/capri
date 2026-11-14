"""uia_dump.py — разведка: как Teams отдаёт субтитры другим программам (UI Automation).

Что делает: во время звонка несколько раз «фотографирует» дерево элементов окон Teams
и записывает его в текстовый файл рядом со скриптом. Ничего не нажимает, звук и картинку не пишет.

Что попадает в файл:
  - типы элементов, их id, классы, ARIA-роли, координаты;
  - какие элементы МЕНЯЛИСЬ между снимками (субтитры меняются постоянно — так мы их и найдём);
  - сколько миллисекунд занимает повторное чтение блока субтитров (важно для слабого ноутбука).
Что НЕ попадает: текст чата, субтитров и имена людей — вместо них «<скрыто: 34 симв., 6 сл.>».
Исключение — ключ --show-text: тогда текст менявшихся элементов пишется как есть
(включай только в тестовом звонке с самим собой).

Запуск (в PowerShell, во время звонка с включёнными субтитрами):
    py -m pip install uiautomation
    py uia_dump.py
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import platform
import re
import sys
import time
from datetime import datetime
from pathlib import Path

VERSION = "1.0"

TEAMS_PROCS = {"ms-teams.exe", "teams.exe", "msteams.exe"}
LIVE_CAPTIONS_PROCS = {"livecaptions.exe"}          # субтитры Windows 11 (Win+Ctrl+L) — запасной источник
BROWSER_PROCS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"}

# слова, по которым узнаём кнопки и блоки субтитров; такие названия показываем как есть
KEY_RX = re.compile(r"untertitel|caption|subtitle|transkript|transcript|субтитр|расшифров", re.I)
TIME_RX = re.compile(r"\d{1,2}:\d{2}(:\d{2})?")
MARK_KEY, MARK_CHANGED, MARK_NEW = "★", "Δ", "+"

# номера свойств UI Automation (из документации Windows): ARIA-роль и ARIA-свойства веб-элемента
UIA_ARIA_ROLE, UIA_ARIA_PROPS = 30101, 30102


# ---------------------------------------------------------------- мелкие помощники
def proc_name(pid: int) -> str:
    """Имя процесса по его номеру (без сторонних библиотек). Не Windows — пустая строка."""
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


def safe(getter, default=""):
    """UI Automation часто бросает ошибки на исчезнувших элементах — тогда берём значение по умолчанию."""
    try:
        value = getter()
        return default if value is None else value
    except Exception:  # noqa: BLE001
        return default


def looks_like_name(s: str) -> bool:
    words = s.split()
    return 1 <= len(words) <= 5 and len(s) <= 60 and s[-1] not in ".!?…" and \
        all(w[:1].isupper() or not w[:1].isalpha() for w in words)


def shape(s: str) -> str:
    """Вместо текста — его «форма»: длина, число слов, похоже ли на имя. Сам текст в файл не идёт."""
    s = s.strip()
    if not s:
        return "''"
    tags = [f"{len(s)} симв.", f"{len(s.split())} сл."]
    if TIME_RX.fullmatch(s):
        tags.append("время")
    elif looks_like_name(s):
        tags.append("похоже на имя")
    if s[-1] in ".!?…":
        tags.append("конец предложения")
    return "<скрыто: " + ", ".join(tags) + ">"


def hide_title(title: str) -> str:
    """Заголовок окна: название встречи скрываем, хвост «| Microsoft Teams» оставляем."""
    parts = [p.strip() for p in title.split("|")]
    if len(parts) >= 2 and "teams" in parts[-1].lower():
        return " | ".join([shape(" | ".join(parts[:-1])), parts[-1]])
    return shape(title)


def text_hash(s: str) -> str:
    return hashlib.blake2s(s.encode("utf-8", "replace"), digest_size=6).hexdigest()


# ---------------------------------------------------------------- поиск окон
def find_windows(auto, extra_procs: set[str]) -> list[dict]:
    """Окна верхнего уровня: Teams, Teams в браузере, субтитры Windows, плюс процессы из --also."""
    found = []
    for w in safe(lambda: auto.GetRootControl().GetChildren(), []):
        title = safe(lambda: w.Name)
        proc = proc_name(safe(lambda: w.ProcessId, 0))
        kind = ""
        if proc in TEAMS_PROCS:
            kind = "Teams"
        elif proc in LIVE_CAPTIONS_PROCS:
            kind = "Субтитры Windows"
        elif proc in BROWSER_PROCS and "teams" in title.lower():
            kind = "Teams в браузере"
        elif proc in extra_procs:
            kind = "по --also"
        elif not proc and "microsoft teams" in title.lower():
            kind = "Teams (по заголовку)"
        if kind:
            found.append({"ctrl": w, "kind": kind, "proc": proc, "title": title,
                          "cls": safe(lambda: w.ClassName)})
    return found


# ---------------------------------------------------------------- снимок дерева
def node_key(ctrl, parent_key: str, ctype: str, aid: str, index: int, use_runtime_id: bool) -> str:
    """Постоянный «номер» элемента, чтобы узнать его в следующем снимке.
    Лучше всего — RuntimeId от самой Windows; если его нет — путь в дереве."""
    if use_runtime_id:
        rid = safe(lambda: ctrl.GetRuntimeId(), None)
        return "r" + ".".join(str(x) for x in rid) if rid else f"{parent_key}/#{index}"
    return f"{parent_key}/{ctype}:{aid}:{index}"


def snapshot(auto, win, max_nodes: int, max_depth: int, full: bool, use_runtime_id: bool):
    """Обходит окно. full=False — быстрый снимок (только текст и ключ), full=True — все свойства.
    Возвращает (узлы, секунд, обрезано ли)."""
    nodes, stack, counters = [], [], {}
    started, truncated = time.perf_counter(), False
    for ctrl, depth in auto.WalkControl(win, includeTop=True, maxDepth=max_depth):
        if len(nodes) >= max_nodes:
            truncated = True
            break
        del stack[depth:]
        parent = stack[-1] if stack else ""
        need_type = full or not use_runtime_id
        ctype = safe(lambda: ctrl.ControlTypeName) if need_type else ""
        aid = safe(lambda: ctrl.AutomationId) if need_type else ""
        counters[parent] = counters.get(parent, 0) + 1
        key = node_key(ctrl, parent, ctype, aid, counters[parent], use_runtime_id)
        stack.append(key)
        name = str(safe(lambda: ctrl.Name))
        node = {"key": key, "parent": parent, "depth": depth, "name": name}
        if full:
            rect = safe(lambda: ctrl.BoundingRectangle, None)
            node.update({
                "ctrl": ctrl, "type": ctype, "aid": aid,
                "cls": safe(lambda: ctrl.ClassName),
                "role": str(safe(lambda: ctrl.GetPropertyValue(UIA_ARIA_ROLE))),
                "aria": str(safe(lambda: ctrl.GetPropertyValue(UIA_ARIA_PROPS))),
                "rect": safe(lambda: (rect.left, rect.top, rect.right, rect.bottom), None) if rect else None,
            })
        nodes.append(node)
    return nodes, time.perf_counter() - started, truncated


# ---------------------------------------------------------------- что менялось
def mark_changes(history: list[dict], final_nodes: list[dict]) -> None:
    """history — по одному словарю {ключ: хэш текста} на каждый снимок. Ставит узлам метки «Δn» и «+»."""
    for node in final_nodes:
        hashes = [snap[node["key"]] for snap in history if node["key"] in snap]
        node["changes"] = sum(1 for a, b in zip(hashes, hashes[1:]) if a != b)
        node["new"] = len(history) > 1 and node["key"] not in history[0]


def likely_container(nodes: list[dict]):
    """Самый глубокий элемент, внутри которого лежит ≥ 60 % менявшихся текстов (короткие — таймер — не в счёт)."""
    by_key = {n["key"]: n for n in nodes}
    moving = [n for n in nodes if (n["changes"] or n["new"]) and len(n["name"].strip()) >= 12]
    if len(moving) < 2:
        return None, moving
    votes: dict[str, int] = {}
    for n in moving:
        key = n["parent"]
        while key in by_key:
            votes[key] = votes.get(key, 0) + 1
            key = by_key[key]["parent"]
    good = [by_key[k] for k, v in votes.items() if v >= 0.6 * len(moving)]
    return (max(good, key=lambda n: n["depth"]) if good else None), moving


def time_reread(auto, node, times: int = 5, limit: int = 1500) -> tuple[list[int], int]:
    """Сколько миллисекунд занимает повторное чтение текста внутри блока (так будет работать приложение)."""
    result, count = [], 0
    for _ in range(times):
        started, count = time.perf_counter(), 0
        for ctrl, _depth in auto.WalkControl(node["ctrl"], includeTop=True, maxDepth=12):
            safe(lambda: ctrl.Name)
            count += 1
            if count >= limit:
                break
        result.append(round((time.perf_counter() - started) * 1000))
    return result, count


# ---------------------------------------------------------------- вывод
def chain(node, by_key) -> str:
    """Цепочка предков: окно → … → элемент. По ней потом пишется «адрес» блока субтитров."""
    parts, cur = [], node
    while cur is not None:
        parts.append(f"{cur['type']}[id={cur['aid'][:40]!r} cls={cur['cls'][:40]!r} role={cur['role'][:20]!r}]")
        cur = by_key.get(cur["parent"])
    return "\n      ← ".join(parts[:14])


def node_line(node, show_text: bool, inside_key_block: bool) -> str:
    name, marks = node["name"].strip(), []
    is_key = bool(KEY_RX.search(f"{name} {node['aid']} {node['cls']}"))
    if is_key:
        marks.append(MARK_KEY)
    if node["changes"]:
        marks.append(f"{MARK_CHANGED}{node['changes']}")
    if node["new"]:
        marks.append(MARK_NEW)
    if is_key and len(name) <= 60 and node["type"] != "TextControl":
        shown = repr(name)                                   # «Live-Untertitel» и т. п. — это надписи Teams
    elif show_text and name and (node["changes"] or node["new"] or inside_key_block):
        shown = repr(name[:200])
    else:
        shown = shape(name)
    extra = ""
    if node["role"]:
        extra += f" role={node['role'][:30]!r}"
    if node["aria"]:
        extra += f" aria={node['aria'][:80]!r}"
    if node["rect"]:
        extra += f" rect={node['rect']}"
    mark = (" " + " ".join(marks)) if marks else ""
    return (f"{'  ' * min(node['depth'], 40)}{node['type']} name={shown} id={node['aid'][:60]!r} "
            f"cls={node['cls'][:70]!r}{extra}{mark}")


def tree_lines(nodes: list[dict], show_text: bool) -> list[str]:
    lines, key_depth = [], None
    for n in nodes:
        if key_depth is not None and n["depth"] <= key_depth:
            key_depth = None
        lines.append(node_line(n, show_text, key_depth is not None))
        container_like = n["type"] not in ("ButtonControl", "MenuItemControl", "CheckBoxControl", "TextControl")
        if key_depth is None and container_like and KEY_RX.search(f"{n['name']} {n['aid']} {n['cls']}"):
            key_depth = n["depth"]
    return lines


# ---------------------------------------------------------------- главная часть
def run(auto, args, out_dir: Path, log=print) -> Path | None:
    extra = {p.lower() for p in args.also}
    windows = find_windows(auto, extra)
    if not windows:
        log("Не нашёл ни одного окна Teams. Проверь: звонок открыт, окно не свёрнуто.")
        log("Если Teams открыт в браузере — в заголовке вкладки должно быть слово «Teams».")
        return None
    use_runtime_id = bool(safe(lambda: windows[0]["ctrl"].GetRuntimeId(), None))
    log(f"Окон найдено: {len(windows)}. Снимков: {args.snapshots}, пауза {args.pause} с. Говори или включи немецкую речь.")

    history: list[dict] = []
    final: list[tuple[dict, list[dict], float, bool]] = []
    for i in range(args.snapshots):
        last = i == args.snapshots - 1
        if i:
            time.sleep(args.pause)
        snap: dict[str, str] = {}
        windows = find_windows(auto, extra) or windows       # окно субтитров могло появиться позже
        for w in windows:
            nodes, secs, cut = snapshot(auto, w["ctrl"], args.max_nodes, args.max_depth, last, use_runtime_id)
            snap.update({n["key"]: text_hash(n["name"]) for n in nodes})
            log(f"  снимок {i + 1}/{args.snapshots}: {w['kind']} ({w['proc'] or '?'}) — узлов {len(nodes)}, {secs:.1f} с")
            if last:
                final.append((w, nodes, secs, cut))
        history.append(snap)

    all_nodes = [n for _w, nodes, _s, _c in final for n in nodes]
    mark_changes(history, all_nodes)
    by_key = {n["key"]: n for n in all_nodes}
    container, moving = likely_container(all_nodes)
    key_hits = [n for n in all_nodes if KEY_RX.search(f"{n['name']} {n['aid']} {n['cls']}")]

    uia_version = safe(lambda: __import__("importlib.metadata").metadata.version("uiautomation"), "?")
    out = [
        f"# uia_dump {VERSION} — {datetime.now():%Y-%m-%d %H:%M}",
        f"# {platform.platform()}, Python {platform.python_version()}, uiautomation {uia_version}",
        f"# снимков {args.snapshots}, пауза {args.pause} с, текст {'ПОКАЗАН у менявшихся элементов' if args.show_text else 'скрыт'}, "
        f"ключи элементов: {'RuntimeId' if use_runtime_id else 'путь в дереве'}",
        "", "## Окна",
    ]
    for i, (w, nodes, secs, cut) in enumerate(final, 1):
        out.append(f"[{i}] {w['kind']}: proc={w['proc']!r} cls={w['cls']!r} title={hide_title(w['title'])} — "
                   f"узлов {len(nodes)}{' (ОБРЕЗАНО)' if cut else ''}, обход {secs:.1f} с")
    out += ["", "## Сводка",
            f"- надписи про субтитры ({MARK_KEY}): {len(key_hits)}",
            f"- менявшиеся или новые тексты ({MARK_CHANGED}/{MARK_NEW}, длиннее 12 симв.): {len(moving)}"]
    if container is not None:
        out.append("- вероятный блок субтитров (внутри него ≥ 60 % менявшихся текстов):")
        out.append("      " + chain(container, by_key))
        ms, count = time_reread(auto, container)
        out.append(f"- повторное чтение этого блока, 5 раз: {ms} мс (элементов {count})")
    else:
        out.append("- блок субтитров по изменениям НЕ определён (мало менявшихся текстов — шли ли субтитры?)")
    out += ["", f"## Надписи про субтитры ({MARK_KEY}) — цепочки предков"]
    out += [f"  • {node_line(n, False, False).strip()}\n      {chain(n, by_key)}" for n in key_hits[:25]] or ["  нет"]
    out += ["", f"## Менявшиеся тексты ({MARK_CHANGED}/{MARK_NEW}) — цепочки предков"]
    out += [f"  • {node_line(n, args.show_text, False).strip()}\n      {chain(n, by_key)}" for n in moving[:40]] or ["  нет"]
    for i, (_w, nodes, _secs, _cut) in enumerate(final, 1):
        out += ["", f"## Дерево окна [{i}]"] + tree_lines(nodes, args.show_text)

    path = out_dir / f"teams_uia_dump_{datetime.now():%Y%m%d-%H%M}.txt"
    path.write_text("\n".join(out), encoding="utf-8")
    log("")
    log(f"Готово: {path}")
    log(f"Надписей про субтитры: {len(key_hits)}, менявшихся текстов: {len(moving)}, "
        f"блок субтитров {'найден' if container is not None else 'НЕ найден'}.")
    log("Открой файл и просмотри его перед отправкой: текста разговора и имён в нём быть не должно"
        + (" (кроме менявшихся строк — ты включил --show-text)." if args.show_text else "."))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Снимок дерева UI Automation окон Teams (разведка субтитров).")
    parser.add_argument("--snapshots", type=int, default=3, help="сколько снимков сделать (по умолчанию 3)")
    parser.add_argument("--pause", type=float, default=15, help="пауза между снимками, секунд (по умолчанию 15)")
    parser.add_argument("--max-nodes", type=int, default=8000, help="предел элементов на одно окно")
    parser.add_argument("--max-depth", type=int, default=70, help="предел глубины дерева")
    parser.add_argument("--show-text", action="store_true",
                        help="показать текст менявшихся элементов (только для тестового звонка с самим собой)")
    parser.add_argument("--also", action="append", default=[], metavar="PROC.EXE",
                        help="дополнительно снять окна этого процесса, например --also zoom.exe")
    args = parser.parse_args(argv)
    args.snapshots = max(1, args.snapshots)
    for stream in (sys.stdout, sys.stderr):
        safe(lambda: stream.reconfigure(errors="replace"))
    try:
        import uiautomation as auto
    except ImportError:
        print("Не установлен пакет uiautomation. Выполни:  py -m pip install uiautomation")
        return 2
    out_dir = Path(__file__).resolve().parent
    return 0 if run(auto, args, out_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
