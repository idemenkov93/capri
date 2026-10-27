"""Проверка обновлений Capri.exe: последняя версия в GitHub Releases."""
from __future__ import annotations

import re

import httpx

from . import __version__

REPO = "idemenkov93/capri"


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def latest() -> dict | None:
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10,
                      headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        d = r.json()
        return {"version": d.get("tag_name", "").lstrip("v"), "url": d.get("html_url", "")}
    except (httpx.HTTPError, ValueError):
        return None


def newer(version: str, current: str = __version__) -> bool:
    return bool(version) and _v(version) > _v(current)
