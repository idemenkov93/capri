"""Журнал Капри: data/capri.log — что услышала, куда отправила, какие были ошибки. Помогает чинить «через раз»."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import expand

log = logging.getLogger("capri")


def setup() -> None:
    if log.handlers:
        return
    path = expand("data/capri.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(threadName)s: %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
