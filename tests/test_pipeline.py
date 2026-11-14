"""Конвейер, текст встречи, клавиши, окно, журнал."""
import datetime as dt

from dolmetsch.capture.base import Event
from dolmetsch.hotkeys import parse
from dolmetsch.pipeline import STALE, Pipeline
from dolmetsch.transcript import Transcript, lessons_root
from dolmetsch.translate.base import Result


class Router:
    def __init__(self):
        self.calls, self.last_error = [], ""
        self.offline = self

    def translate(self, texts, context=""):
        self.calls.append((list(texts), context))
        if texts and isinstance(texts[0], str) and context == "draft":
            return [f"ru:{t}" for t in texts]
        return [Result(f"ru:{t}", "офлайн", 5) for t in texts]


def pipe(**kw):
    out = {"lines": [], "partials": [], "drafts": []}
    clock = [100.0]
    p = Pipeline(kw.pop("router", Router()), on_line=out["lines"].append,
                 on_partial=lambda s, t: out["partials"].append((s, t)), on_draft=out["drafts"].append,
                 clock=lambda: clock[0], **kw)
    return p, out, clock


def test_finals_are_batched_and_partials_keep_only_the_latest():
    p, out, clock = pipe()
    for i in range(3):
        p.push(Event("final", "Anna", f"Satz {i}.", 100.0))
        p.push(Event("partial", "Anna", f"teil {i}", 100.0))
    clock[0] = 100.4
    p.step(0)
    assert p.router.calls == [(["Satz 0.", "Satz 1.", "Satz 2."], "")]
    assert [ln.translation for ln in out["lines"]] == ["ru:Satz 0.", "ru:Satz 1.", "ru:Satz 2."]
    assert out["lines"][0].ms == 400 and out["partials"] == [("Anna", "teil 2")]
    p.push(Event("final", "Anna", "Noch einer.", 100.4))
    p.step(0)
    assert p.router.calls[-1] == (["Noch einer."], "Satz 2.")       # предыдущая фраза — контекст


def test_stale_phrases_are_shown_without_translation():
    p, out, clock = pipe()
    p.push(Event("final", "Anna", "Zu alt.", 100.0))
    p.push(Event("final", "Anna", "Frisch.", 100.0 + STALE))
    clock[0] = 100.0 + STALE + 1
    p.step(0)
    assert [(ln.text, ln.translation) for ln in out["lines"]] == [("Zu alt.", ""), ("Frisch.", "ru:Frisch.")]
    assert p.stats["skipped"] == 1 and "пропущено 1" in p.summary()


def test_draft_translation_only_when_enabled():
    p, out, clock = pipe()
    p.push(Event("partial", "Anna", "wir fangen heute an", 100.0))
    p.step(0)
    assert out["drafts"] == []                       # ноутбук: недописанное не переводим
    p, out, clock = pipe(live_interval=1.5)
    p.push(Event("partial", "Anna", "wir fangen heute an", 100.0))
    p.step(0)
    assert len(out["drafts"]) == 1
    p.step(0)
    assert len(out["drafts"]) == 1                   # тот же текст второй раз не переводится


# ---------- текст встречи
def test_transcript_is_written_line_by_line_in_capri_format(tmp_path):
    times = iter([dt.datetime(2026, 11, 16, 9, 0, 0), dt.datetime(2026, 11, 16, 9, 0, 5),
                  dt.datetime(2026, 11, 16, 9, 1, 15)])
    t = Transcript(root=tmp_path, now=lambda: next(times))
    t.set_title('Netzwerk: DNS/DHCP?')
    t.add("Hans Müller", "Guten Morgen.", "Доброе утро.")
    path = tmp_path / "2026-11-16 0900 — Netzwerk DNS DHCP" / "транскрипт.md"
    assert path.exists() and "**[00:00:05] Hans Müller:** Guten Morgen.\n> Доброе утро.\n" in path.read_text(encoding="utf-8")
    t.add("", "Ohne Übersetzung.", "")
    text = path.read_text(encoding="utf-8")
    assert "**[00:01:15] Говорящий:** Ohne Übersetzung.\n\n" in text and text.startswith("# Транскрипт 16.11.2026 09:00")
    assert t.close() == path and t.path is None


def test_transcript_goes_to_capri_folder_if_it_exists_and_survives_write_errors(tmp_path):
    assert lessons_root(tmp_path) == tmp_path / "Dolmetsch"
    (tmp_path / "Capri").mkdir()
    assert lessons_root(tmp_path) == tmp_path / "Capri" / "Уроки"
    blocked = tmp_path / "файл"
    blocked.write_text("x")
    t = Transcript(root=blocked)                     # папку создать нельзя — перевод не должен упасть
    t.add("Anna", "Hallo.", "Привет.")
    assert t.error and t.lines == 0


def test_pipeline_writes_transcript(tmp_path):
    t = Transcript(root=tmp_path)
    p, out, clock = pipe(transcript=t)
    p.push(Event("final", "Anna", "Hallo zusammen.", 100.0))
    p.step(0)
    assert "ru:Hallo zusammen." in t.path.read_text(encoding="utf-8")


# ---------- клавиши
def test_hotkey_parsing():
    assert parse("Win+Alt+J") == (0x0008 | 0x0001, ord("J"))
    assert parse("ctrl+shift+f5") == (0x0002 | 0x0004, 0x74)
    assert parse("J") is None and parse("Win+Alt+Foo") is None and parse("") is None


# ---------- окно и строка состояния
def test_overlay_renders_translation_original_and_partial(qapp):
    from dolmetsch.pipeline import Line
    from dolmetsch.ui.overlay import Overlay
    o = Overlay(lines=2)
    for i in range(3):
        o.add_line(Line("Anna <b>", f"Satz {i}.", f"Фраза {i}.", "офлайн", 300))
    o.add_line(Line("Ben", "Nicht übersetzt.", "", "", 0, "ошибка"))
    o.set_partial("Ben", "und jetzt")
    text = o.text.toPlainText()
    assert "Фраза 0." not in text and "Фраза 2." in text and "Satz 2." in text      # держим последние 2
    assert "Nicht übersetzt." in text and "und jetzt …" in text and "<b>" in text   # имя не ломает разметку
    o.show_original = False
    o.render()
    assert "Satz 2." not in o.text.toPlainText()
    o.flash("Сообщение")
    assert "Сообщение" in o.text.toPlainText()


def test_status_text():
    from dolmetsch.app import status_text
    assert status_text(False, "", "ok", "lesson", "", 0, "")[0] == "Пауза"
    assert status_text(True, "", "no_panel", "lesson", "", 0, "") == ("Teams найден, субтитры не включены", "warn")
    text, level = status_text(True, "Teams", "ok", "lesson", "офлайн", 420, "")
    assert text == "Teams · профиль «Урок» · офлайн, 0,4 с" and level == "ok"
    assert status_text(True, "Teams", "ok", "important", "офлайн", 900, "DeepL не ответил вовремя")[1] == "warn"


def test_controller_starts_and_stops_without_windows(qapp, tmp_path, monkeypatch):
    """Вся сборка целиком: создаётся, стартует, останавливается. Захвата нет (не Windows) — и это не ошибка."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import dolmetsch.transcript as T
    monkeypatch.setattr(T, "documents_dir", lambda: tmp_path)
    from dolmetsch.app import make_controller
    from dolmetsch.pipeline import Line
    c = make_controller()(use_hotkeys=False)
    c.start()
    c.on_state("Teams", "ok")
    c.on_line(Line("Anna", "Guten Morgen.", "Доброе утро.", "офлайн", 350))
    assert "Teams" in c.overlay.status.text() and "0,3 с" in c.overlay.status.text()
    c.on_hotkey("profile")
    c.on_hotkey("window")
    for act in c.menu.actions() + [a for sub in c.menu.findChildren(type(c.menu)) for a in sub.actions()]:
        if act.text() in ("Показывать немецкий оригинал", "Урок — только офлайн", "Крупнее"):
            act.trigger()                            # пункты меню срабатывают с правильным значением
    assert c.cfg["show_original"] is False and c.cfg["profile"] == "lesson" and c.cfg["font_size"] == 16
    c.on_ask()
    assert c.askbar.isVisible()
    c.on_answer(False)
    c.stop()
    assert not c.running and "Пауза" in c.overlay.status.text()
    assert (tmp_path / "Dolmetsch" / "config.json").exists()


def test_log_never_contains_meeting_text(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import dolmetsch.log as L
    for h in list(L.log.handlers):
        L.log.removeHandler(h)
    L.setup()
    secret = "GEHEIMER SATZ AUS DEM UNTERRICHT"
    p, out, clock = pipe()
    p.push(Event("final", "Frau Geheim", secret + ".", 100.0))
    p.step(0)
    L.log.info("стоп: %s", p.summary())
    for h in list(L.log.handlers):               # закрываем файл журнала, чтобы прочитать его целиком
        h.close()
        L.log.removeHandler(h)
    text = (tmp_path / "Dolmetsch" / "dolmetsch.log").read_text(encoding="utf-8")
    assert "фраз 1" in text and secret not in text and "Geheim" not in text
