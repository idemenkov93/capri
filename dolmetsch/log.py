"""Журнал: %APPDATA%\\Dolmetsch\\dolmetsch.log. В журнал НЕ попадает текст встреч и имена — только числа и ошибки."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import app_dir

log = logging.getLogger("dolmetsch")


def setup() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(app_dir() / "dolmetsch.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())


def crash(where: str, exc: BaseException | None = None) -> None:
    """Записать сбой: тип ошибки и место в коде. Текст ошибки не пишем — в нём может оказаться текст встречи."""
    import sys
    import traceback
    exc = exc or sys.exc_info()[1]
    frames = traceback.extract_tb(exc.__traceback__) if exc is not None else []
    place = " <- ".join(f"{f.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{f.lineno}" for f in frames[-3:][::-1])
    log.error("сбой (%s): %s в %s", where, type(exc).__name__ if exc else "?", place or "?")
