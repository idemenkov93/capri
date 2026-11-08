"""Бэкап папок: ZIP с датой, ротация только своих копий, расписание, фразы."""
import datetime as dt
import zipfile

import pytest

from capri import backup
from capri.convert import ConvertError


def tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
    return root


def test_make_zip_with_two_same_named_folders(tmp_path):
    a = tree(tmp_path / "a" / "Фото", {"1.txt": "один", "sub/2.txt": "два", "node_modules/x.js": "мусор"})
    b = tree(tmp_path / "b" / "Фото", {"3.txt": "три"})
    dest = tmp_path / "флешка"
    dest.mkdir()
    out, count, size, skipped = backup.make([str(a), str(b)], str(dest), now=dt.datetime(2026, 11, 8, 4, 30))
    assert out.name == "Capri-бэкап 2026-11-08 0430.zip" and count == 3 and not skipped
    names = sorted(zipfile.ZipFile(out).namelist())
    assert names == ["Фото (2)/3.txt", "Фото/1.txt", "Фото/sub/2.txt"]
    assert "Бэкап готов: 3 файлов" in backup.summary(out, count, size, skipped)


def test_backup_inside_source_does_not_copy_itself(tmp_path):
    src = tree(tmp_path / "Документы", {"a.txt": "x"})
    dest = src / "Бэкапы"
    dest.mkdir()
    (dest / "старый.zip").write_bytes(b"1" * 100)
    out, count, *_ = backup.make([str(src)], str(dest))
    assert count == 1 and zipfile.ZipFile(out).namelist() == ["Документы/a.txt"]


def test_rotation_keeps_newest_and_only_ours(tmp_path):
    for d in ("2026-11-01 1000", "2026-11-02 1000", "2026-11-03 1000", "2026-11-04 1000"):
        (tmp_path / f"Capri-бэкап {d}.zip").write_bytes(b"x")
    (tmp_path / "моё важное.zip").write_bytes(b"x")
    gone = backup.rotate(tmp_path, 2)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(gone) == 2 and left == ["Capri-бэкап 2026-11-03 1000.zip", "Capri-бэкап 2026-11-04 1000.zip", "моё важное.zip"]


def test_missing_dest_is_clear(tmp_path):
    with pytest.raises(ConvertError, match="флешка"):
        backup.make([str(tmp_path)], str(tmp_path / "нет"))


def test_due():
    cfg = {**backup.defaults(), "folders": ["x"], "dest": "y", "every": "day", "last": 1000.0}
    assert backup.due(cfg, now=1000 + 86400) and not backup.due(cfg, now=1000 + 3600)
    assert not backup.due({**cfg, "every": "off"}, now=10**10)


def test_phrases():
    import copy
    from pathlib import Path

    from capri.agent import Agent
    from capri.applist import AppIndex
    from capri.config import DEFAULTS

    class Sys:
        def run(self, cmd):
            return {}
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    assert a.handle("сделай бэкап").actions[0].params == {"do": "now"}
    assert a.handle("настрой бэкап").actions[0].params == {"do": "setup"}


def test_app_menu_and_admin_actions(monkeypatch, tmp_path):
    """Меню трея строится с новыми пунктами (💾 Бэкап, конвертер), админ-окно и PowerShell-подсказка открываются."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from tests.test_eco import quiet_app
    from capri import actions as A
    app = quiet_app(monkeypatch)
    menu = app.build_menu()
    titles = [a.text() for a in menu.actions()]
    assert "💾 Бэкап" in titles
    app.prefs.set("backup", {"folders": [str(tmp_path)], "dest": str(tmp_path), "keep": 5, "every": "off", "last": 0})
    app.build_menu()
    app.run_action(A.Action("popup", "x", {"title": "Мой IP", "text": "**192.168.1.5**", "command": "Get-NetIPConfiguration"},
                            needs_confirm=False))
    assert "192.168.1.5" in app.text.popup.body.toPlainText()
    app.run_action(A.Action("ps_helper", "x", {"command": "Get-Service", "explain": "службы", "admin": True, "danger": ""},
                            needs_confirm=False))
    from PySide6.QtGui import QGuiApplication
    assert QGuiApplication.clipboard().text() == "Get-Service"
    app.prefs.set("backup", {})
