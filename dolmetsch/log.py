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
