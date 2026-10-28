"""«Ночной свет» Windows (тёплый жёлтый экран) — напрямую, без окна «Параметры».

Windows хранит его состояние в реестре (HKCU\\…\\CloudStore\\…bluelightreductionstate, значение Data) в формате
Microsoft Bond CompactBinary. Раньше Капри открывала «Параметры» и искала там кнопку — на этом ПК кнопка не
находилась. Теперь меняем одно поле «включено» в этих данных, и Windows сразу применяет его, как будто ты
щёлкнул переключатель. Если формат окажется не таким, как мы ждём, ничего не пишем и возвращаемся к старому
способу через «Параметры».

Формат описан тут: github.com/CasperWollesen/win-nightlight-cli/blob/main/docs/nightlight-registry-format.md
"""
from __future__ import annotations

import sys
import time

KEY = (r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
       r"\default$windows.data.bluelightreduction.bluelightreductionstate"
       r"\windows.data.bluelightreduction.bluelightreductionstate")
HEADER = b"\x43\x42\x01\x00"            # Bond CompactBinary v1
ENABLED_FIELD = b"\x10\x00"            # поле 0 (int32) — само его наличие значит «включено»


def _varint(data: bytes, i: int) -> tuple[int, int]:
    v, shift = 0, 0
    while True:
        b = data[i]
        v |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return v, i
        shift += 7
        if shift > 63:
            raise ValueError("varint")


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def parse(data: bytes) -> tuple[bool, bytes, bytes]:
    """→ (включён ли, внутренние данные без поля «включено», хвост обёртки). ValueError — формат не тот."""
    if not data.startswith(HEADER + b"\x0a\x02\x01\x00\x2a\x06"):
        raise ValueError("не тот заголовок")
    _ts, i = _varint(data, 10)
    if data[i:i + 3] != b"\x2a\x2b\x0e":
        raise ValueError("нет списка с данными")
    count, i = _varint(data, i + 3)
    inner, tail = data[i:i + count], data[i + count:]
    if len(inner) != count or not inner.startswith(HEADER) or not tail.startswith(b"\x00"):
        raise ValueError("внутренние данные не те")
    body = inner[4:]
    on = body.startswith(ENABLED_FIELD)
    if on:
        body = body[2:]
    if b"\xd0\x0a" not in body[:4]:           # поле 10 «initialized» должно идти следом
        raise ValueError("неизвестные поля")
    return on, body, tail


def build(on: bool, body: bytes, tail: bytes, now: float | None = None) -> bytes:
    now = time.time() if now is None else now
    filetime = int((now + 11_644_473_600) * 10_000_000)
    # поле 20 (uint64) — когда состояние менялось: ставим «сейчас»
    j = body.find(b"\xc6\x14")
    if j >= 0:
        _old, end = _varint(body, j + 2)
        body = body[:j + 2] + _enc_varint(filetime) + body[end:]
    inner = HEADER + (ENABLED_FIELD if on else b"") + body
    return (HEADER + b"\x0a\x02\x01\x00\x2a\x06" + _enc_varint(int(now)) + b"\x2a\x2b\x0e"
            + _enc_varint(len(inner)) + inner + tail)


def is_on() -> bool | None:
    try:
        return parse(_read())[0]
    except Exception:  # noqa: BLE001
        return None


def _read() -> bytes:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ) as k:
        return bytes(winreg.QueryValueEx(k, "Data")[0])


def set_on(on: bool) -> bool:
    """Включить/выключить ночной свет. False — не вышло (нет ключа, другой формат)."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        cur, body, tail = parse(_read())
        if cur == on:
            return True
        new = build(on, body, tail)
        parse(new)                                 # проверяем себя, прежде чем писать
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Data", 0, winreg.REG_BINARY, new)
        return True
    except Exception:  # noqa: BLE001
        from .log import log
        log.warning("ночной свет через реестр не вышел", exc_info=True)
        return False
