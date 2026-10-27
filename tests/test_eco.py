import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

KEEP = []   # Qt-объекты Капри живут до конца тестов: сборка мусора посреди работы фоновых потоков роняла Windows


def quiet_app(monkeypatch):
    """CapriApp без фоновых потоков, которые на Windows трогают настоящую систему (COM, PowerShell, микрофон)."""
    import capri.app as appmod
    from capri.applist import AppIndex
    monkeypatch.setattr(appmod.CapriApp, "_init_voice", lambda self: None)
    monkeypatch.setattr(appmod.CapriApp, "start_dancing", lambda self: None)
    monkeypatch.setattr(AppIndex, "refresh", lambda self: len(self.apps))
    app = appmod.CapriApp()
    KEEP.append(app)
    return app


def test_avatar_draws_less_when_idle_in_eco():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capri.ui.avatar import Avatar
    a = Avatar(120, "", 12, wander=False)
    a.glitch = 0
    a._idle_glitch = ""
    a.eco = True
    a._animate()
    a.glitch = 0
    a._animate()
    assert a._tick.interval() == 100
    a.walk_to = a.x() + 300          # пошла гулять — снова плавно
    a._animate()
    assert a._tick.interval() == 33


def test_app_enters_and_leaves_eco(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import capri.app as appmod
    app = quiet_app(monkeypatch)
    app.warmup.wait(10_000)          # первый прогрев (Ollama тут нет — завершится быстро)
    unloaded = []
    app.agent.llm.unload = lambda: unloaded.append(True)
    started = []
    monkeypatch.setattr(appmod.Warmup, "start", lambda self: started.append(True))
    app.set_status("ONLINE")
    app._last_active = time.monotonic() - 4 * 60
    app._eco_check()
    assert app.eco and app.avatar.eco and not app.models_unloaded
    app._last_active = time.monotonic() - 11 * 60
    app._eco_check()
    time.sleep(0.05)
    assert app.models_unloaded and unloaded
    app.wake_up()                     # позвали — выходим и грузим модели обратно
    assert not app.eco and not app.avatar.eco and not app.models_unloaded and started


def test_every_quit_hook_exists():
    """Регрессия: при правке пропал stop_dancing — Капри падала при старте (run() подключает его к выходу)."""
    import capri.app as appmod
    for name in ("stop_wake", "stop_dancing", "_watch_hangs", "wake_up", "connect_google", "_apply_wake_mode"):
        assert hasattr(appmod.CapriApp, name), name


def test_app_runs_and_quits(monkeypatch):
    """Запуск целиком: run() → цикл событий → выход (ловит ошибки вроде пропавших методов)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    app = quiet_app(monkeypatch)
    app.warmup.wait(10_000)
    QTimer.singleShot(300, app.qt.quit)
    assert app.run() == 0
