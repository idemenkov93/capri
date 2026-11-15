"""2.0: короткое меню, палитра команд, связка с Dolmetsch, общая папка."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app(monkeypatch, tmp_path):
    from capri import dolmetsch
    from tests.test_eco import quiet_app
    monkeypatch.setattr(dolmetsch, "candidates", lambda cfg=None: [tmp_path / "нет" / "Dolmetsch.exe"])
    return quiet_app(monkeypatch)


def test_tray_menu_has_six_items_and_everything_else_is_in_palette(app):
    menu = app.build_menu()                                  # ссылку держим: пункты живут вместе с меню
    titles = [a.text().split("\t")[0] for a in menu.actions()]
    assert titles == ["Открыть Capri", "Команды…", "Субтитры встречи", "Не беспокоить час", "Настройки…", titles[-1]]
    assert len(titles) == 6 and menu.defaultAction().text().startswith("Открыть Capri")
    commands = app.palette_commands()
    by_title = {c.title: c for c in commands}
    assert len(commands) > 150
    assert by_title["🎓 Спросить наставника"].hint == "Win+Alt+N"                 # действие с клавишей
    assert any(c.keywords == "импортируй бестиарий" for c in commands)             # команда-фраза
    old = [c for c in commands if c.settings]
    assert len(old) > 40 and any("Горячие клавиши" in c.title for c in old)       # всё старое меню — в настройках
    assert any(c.checked is not None for c in old)                                 # переключатели показывают состояние


def test_palette_search_and_run(app):
    from capri.ui.palette import Command, match
    ran = []
    cmds = [Command("Перевести буфер", "Текст", run=lambda: ran.append("t"), keywords="переведи буфер"),
            Command("Объяснить письмо", "Письмо"), Command("Микрофон по умолчанию", "Настройки › Микрофон", settings=True),
            Command("Буферная зона", "Прочее")]
    assert [c.title for c in match(cmds, "буф")] == ["Буферная зона", "Перевести буфер"]     # начало названия — выше
    assert [c.title for c in match(cmds, "микрофон настройки")] == ["Микрофон по умолчанию"]
    assert match(cmds, "нет такого") == []
    app.open_palette()
    p = app.palette
    p.provider = lambda: cmds
    p.open()
    assert p.titles() == ["Перевести буфер", "Объяснить письмо", "Буферная зона"]            # без запроса — без настроек
    p.search.setText("микр")
    assert p.titles() == ["Микрофон по умолчанию"]                                           # поиск идёт везде
    p.open(settings=True)
    assert p.titles() == ["Микрофон по умолчанию"]
    p.open()
    p.search.setText("перев")
    from capri.ui.palette import ROLE
    p.run(p.list.currentItem().data(ROLE))
    assert ran == ["t"] and not p.isVisible()
    assert app.prefs.get("palette_recent") == ["Перевести буфер"]


def test_subtitles_go_to_dolmetsch_when_installed(app, monkeypatch, tmp_path):
    from capri import actions as A
    from capri import dolmetsch
    said = []
    monkeypatch.setattr(app.chat, "add_system", said.append)
    monkeypatch.setattr(app.live, "start", lambda *a, **k: "встроенные субтитры включены")
    app.prefs.set("dolmetsch_hint", False)
    app.toggle_subtitles()                                   # Dolmetsch нет — подсказка и старые субтитры
    assert "Dolmetsch не найден" in said[0] and said[1] == "встроенные субтитры включены"

    exe = tmp_path / "Dolmetsch.exe"
    exe.write_text("x")
    calls = []
    monkeypatch.setattr(dolmetsch, "candidates", lambda cfg=None: [exe])
    monkeypatch.setattr(dolmetsch.subprocess, "Popen", lambda args, **kw: calls.append(args))
    app.toggle_subtitles()
    assert calls == [[str(exe), "--toggle"]] and "Dolmetsch" in said[-1]
    app.run_action(A.Action("live", "", {"do": "start", "lang": "de"}))      # «включи субтитры» в чате
    assert calls[-1] == [str(exe), "--start"]
    app.on_hotkey("subtitles")
    assert calls[-1] == [str(exe), "--toggle"]


def test_dolmetsch_is_found_by_its_path_file(monkeypatch, tmp_path):
    from capri import dolmetsch
    exe = tmp_path / "prog" / "Dolmetsch.exe"
    exe.parent.mkdir()
    exe.write_text("x")
    (tmp_path / "Dolmetsch").mkdir()
    (tmp_path / "Dolmetsch" / "path.txt").write_text(str(exe), encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert dolmetsch.find() == exe
    assert dolmetsch.run("show", popen=lambda args, **kw: None) == "🎧 Открываю Dolmetsch."
    monkeypatch.setenv("APPDATA", str(tmp_path / "пусто"))
    assert dolmetsch.find() is None and dolmetsch.run("toggle") is None


def test_bestiary_moves_to_shared_folder_once(monkeypatch, tmp_path):
    import capri.config as C
    from capri.live import glossary as G
    from capri.live import notes as N
    old = tmp_path / "app" / "data" / "glossary"
    old.mkdir(parents=True)
    (old / "it.csv").write_text("Deutsch;Русский\nFreigabe;общий доступ\n", encoding="utf-8")
    (old / "_пропущено.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(C, "expand", lambda p: tmp_path / "app" / p)
    monkeypatch.setattr(N, "lessons_dir", lambda: tmp_path / "Документы" / "Capri" / "Уроки")
    G._migrated.clear()
    new = G.csv_dir()
    assert new == tmp_path / "Документы" / "Capri" / "Бестиарий"
    assert (new / "it.csv").exists() and (new / "_пропущено.txt").exists() and (old / "it.csv").exists()
    (new / "it.csv").write_text("Deutsch;Русский\nServer;сервер\n", encoding="utf-8")
    G._migrated.clear()
    G.csv_dir()
    assert "Server" in (new / "it.csv").read_text(encoding="utf-8")      # повторно ничего не затирается


def test_latest_meeting_can_be_an_interview(monkeypatch, tmp_path):
    from capri.live import notes as N
    root = tmp_path / "Capri" / "Уроки"
    monkeypatch.setattr(N, "lessons_dir", lambda: root)
    lesson = root / "2026-11-16 0900 — AD"
    lesson.mkdir(parents=True)
    (lesson / "транскрипт.md").write_text("x", encoding="utf-8")
    assert N.latest() == lesson
    talk = tmp_path / "Capri" / "Собеседования" / "2026-11-17 1400 — Firma"
    talk.mkdir(parents=True)
    (talk / "транскрипт.md").write_text("x", encoding="utf-8")
    assert N.latest() == talk and N.latest(root) == lesson


def test_wake_word_is_off_by_default_without_gpu(monkeypatch):
    from capri import mentor
    from tests.test_eco import quiet_app
    monkeypatch.setattr(mentor, "has_gpu", lambda: False)
    app = quiet_app(monkeypatch)
    app.prefs.data.pop("v2_wake_checked", None)
    app.prefs.set("wake_word", True)
    import capri.app as appmod
    monkeypatch.setattr(appmod, "State", lambda path: app.prefs)
    second = quiet_app(monkeypatch)
    assert second.prefs.get("wake_word") is False and second.prefs.get("v2_wake_checked") is True
    second.prefs.set("wake_word", True)                      # включил сам — больше не трогаем
    third = quiet_app(monkeypatch)
    assert third.prefs.get("wake_word") is True
