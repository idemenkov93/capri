"""Запись выбора из мастера настройки в config.yaml — не ломая комментарии и твои правки.

yaml.dump выкинул бы все пояснения из файла, поэтому меняем только нужные строки:
`set_value(text, ["ollama", "model"], "qwen3:4b")` находит строку `model:` внутри `ollama:` и меняет
значение (комментарий в конце строки остаётся). Закомментированный ключ (`# num_gpu: 99`)
раскомментируется; если ключа нет совсем — он добавляется в конец своего раздела.
"""
from __future__ import annotations

import re

import yaml


REMOVE = object()   # set_value(…, REMOVE) — закомментировать ключ (вернётся к значению по умолчанию)


def _scalar(value) -> str:
    out = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).strip()
    return out[:-3].strip() if out.endswith("...") else out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_key(line: str, key: str, indent: int, commented: bool = False) -> re.Match | None:
    body = line[indent:] if _indent(line) == indent else None
    if body is None:
        return None
    if commented:
        m = re.match(r"#\s?(" + re.escape(key) + r"):(.*)$", body)
    else:
        m = re.match(r"(" + re.escape(key) + r"):(.*)$", body)
    return m


def _section_end(lines: list[str], start: int, indent: int) -> int:
    """Индекс первой строки после раздела, начатого строкой start (у раздела отступ indent)."""
    i = start + 1
    last = start
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and not ln.lstrip().startswith("#") and _indent(ln) <= indent:
            break
        if ln.strip() and _indent(ln) > indent:
            last = i
        i += 1
    return last + 1


def set_value(text: str, path: list[str], value) -> str:
    lines = text.splitlines()
    lo, hi, indent = 0, len(lines), 0
    for depth, key in enumerate(path):
        last = depth == len(path) - 1
        found = None
        for i in range(lo, hi):
            if _is_key(lines[i], key, indent):
                found = i
                break
        if found is None and last:
            for i in range(lo, hi):
                if _is_key(lines[i], key, indent, commented=True):
                    found = i
                    break
        if found is None and value is REMOVE:
            return text
        if found is None:
            # ключа нет — дописываем в конец раздела (и сам раздел, если его тоже нет)
            new = [" " * (indent + 2 * k) + f"{p}:" for k, p in enumerate(path[depth:-1])]
            new.append(" " * (indent + 2 * (len(path) - 1 - depth)) + f"{path[-1]}: {_scalar(value)}")
            if depth == 0:
                if lines and lines[-1].strip():
                    new.insert(0, "")
                lines[hi:hi] = new
            else:
                lines[hi:hi] = new
            return "\n".join(lines) + "\n"
        if last and value is REMOVE:
            if _is_key(lines[found], key, indent):
                lines[found] = " " * indent + "# " + lines[found][indent:]
            return "\n".join(lines) + "\n"
        if last:
            m = _is_key(lines[found], key, indent) or _is_key(lines[found], key, indent, commented=True)
            rest = m.group(2)
            comment = ""
            cm = re.search(r"\s+#.*$", rest)
            if cm and not rest.strip().startswith(("'", '"')):
                comment = cm.group(0)
            lines[found] = " " * indent + f"{key}: {_scalar(value)}{comment}"
            return "\n".join(lines) + "\n"
        lo, hi = found + 1, _section_end(lines, found, indent)
        # отступ детей берём из первой настоящей строки раздела
        child = next((_indent(lines[i]) for i in range(lo, hi) if lines[i].strip() and not lines[i].lstrip().startswith("#")), indent + 2)
        indent = child
    return text


def apply(text: str, values: dict[tuple[str, ...], object]) -> str:
    for path, value in values.items():
        text = set_value(text, list(path), value)
    return text
