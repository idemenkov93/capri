"""Общее для тестов."""
import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _gpu_by_default(monkeypatch):
    """По умолчанию тесты считают, что видеокарта есть (локальная модель быстрая) — как на основном ПК.
    Тесты про ноутбук без видеокарты подменяют mentor.has_gpu сами."""
    from capri import mentor
    monkeypatch.setitem(mentor._HW, "gpu", True)


_status = {"code": 0}


def pytest_sessionfinish(session, exitstatus):
    _status["code"] = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Windows CI: после итогов выходим сразу. Иначе при разборе Qt-объектов на выходе интерпретатора
    процесс иногда падает (0xC0000409 / access violation) — все тесты зелёные, а шаг красный."""
    if os.name == "nt" and os.environ.get("CI"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_status["code"])
