"""Конвертер на экране: Ctrl+Shift+Q → выделенные файлы → круговое меню → работа в фоне с прогрессом → «Готово».

Выделенные файлы берём из окна Проводника (Shell.Application), а если не вышло (рабочий стол, вкладки Windows 11,
другие программы) — через Ctrl+C и буфер; буфер потом возвращаем как был.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication

from . import convert as C
from . import convert_menu as M


class Job(QThread):
    progress = Signal(float, str)
    done = Signal(object, str, float)        # результат, ошибка, секунды

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        t0 = time.monotonic()
        try:
            self.done.emit(self.fn(), "", time.monotonic() - t0)
        except C.ConvertError as e:
            self.done.emit(None, str(e), time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, f"{type(e).__name__}: {str(e)[:250]}", time.monotonic() - t0)


def explorer_selection() -> list[str]:
    """Файлы, выделенные в активном окне Проводника. [] — не Проводник или не понятно, какая вкладка."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes

        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        fg = ctypes.windll.user32.GetForegroundWindow()
        wins = comtypes.client.CreateObject("Shell.Application", dynamic=True).Windows()
        found = []
        for i in range(wins.Count):
            try:
                w = wins.Item(i)
                if w is None or int(w.HWND) != fg:
                    continue
                sel = w.Document.SelectedItems()
                found.append([sel.Item(j).Path for j in range(sel.Count)])
            except Exception:  # noqa: BLE001
                continue
        if len(found) == 1:             # в Windows 11 у вкладок одно окно — тогда не гадаем, берём через Ctrl+C
            return found[0]
    except Exception:  # noqa: BLE001
        pass
    return []


def _copy_mime(md: QMimeData) -> QMimeData:
    out = QMimeData()
    for f in md.formats():
        out.setData(f, md.data(f))
    return out


class ConvertUI(QObject):
    def __init__(self, app):
        super().__init__()
        from .ui.radial import RadialMenu
        self.app = app
        self.popup = app.text.popup if getattr(app, "text", None) else None
        if self.popup is None:
            from .ui.popup import Popup
            self.popup = Popup()
        self.radial = RadialMenu()
        self.radial.picked.connect(self._picked)
        self.files: list[Path] = []
        self.items: list = []
        self.jobs: list[Job] = []
        self.cancel = threading.Event()

    # ------------------------------------------------------------------ какие файлы
    def from_hotkey(self):
        sel = explorer_selection()
        if sel:
            return self.open_for(sel)
        self._grab_by_copy()

    def from_clipboard(self):
        md = QGuiApplication.clipboard().mimeData()
        files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        if files:
            return self.open_for(files)
        self.popup.show_text("Конвертер", "Выдели файлы в Проводнике и нажми **Ctrl+Shift+Q** — или скопируй их (Ctrl+C) "
                             "и скажи «конвертер».", copy=False)

    def _grab_by_copy(self):
        from . import control
        cb = QGuiApplication.clipboard()
        saved = _copy_mime(cb.mimeData())
        cb.setText(f"⁣capri{time.time()}")

        def grab(tries=[0]):  # noqa: B006
            md = cb.mimeData()
            files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
            if not files and tries[0] < 8:
                tries[0] += 1
                QTimer.singleShot(70, grab)
                return
            cb.setMimeData(saved)
            if files:
                self.open_for(files)
            else:
                self.popup.show_text("Конвертер", "Не вижу выделенных файлов. Выдели их в Проводнике или на рабочем "
                                     "столе и нажми **Ctrl+Shift+Q** ещё раз.", copy=False)

        def press(wait=[0]):  # noqa: B006
            try:
                if control.keys_held() and wait[0] < 30:
                    wait[0] += 1
                    QTimer.singleShot(50, press)
                    return
                control.press_keys(0x11, 0x43)          # Ctrl+C
            except Exception:  # noqa: BLE001
                pass
            QTimer.singleShot(120, grab)
        QTimer.singleShot(40, press)

    def open_for(self, files: list[str | Path]):
        self.files = [Path(f) for f in files if Path(f).exists()]
        if not self.files:
            self.popup.show_text("Конвертер", "Файлы не найдены.", copy=False)
            return
        self.items = M.menu_for(self.files)
        n = len(self.files)
        title = self.files[0].name if n == 1 else f"{n} {_plural(n, 'файл', 'файла', 'файлов')}"
        if len(title) > 22:
            title = title[:20] + "…"
        self.radial.show_at(self.items, title)

    # ------------------------------------------------------------------ выбрали
    def _picked(self, code: str):
        it = M.find(self.items, code)
        if code == "img.ocr":
            return self.app.text.ocr_image(str(self.files[0]))
        if it is not None and it.ask:
            self.popup.show_text(it.label, it.hint or it.ask, copy=False)
            self.popup.ask(it.ask, "", lambda text: self.start(code, text))
            return
        self.start(code)

    def start(self, code: str, arg: str = ""):
        if M.needs_ffmpeg(code):
            from . import media
            ff = media.find_ffmpeg(self.app.cfg)
            if not ff:
                self.popup.show_text("Нужен ffmpeg", "Для видео и звука нужна бесплатная программа ffmpeg (~90 МБ). "
                                     "Скачаю один раз в папку Капри — потом всё работает без интернета.",
                                     [("⬇ Скачать", lambda: self._install_ffmpeg(code, arg))], copy=False)
                return
        it = M.find(self.items, code)
        title = (it.label if it else "Конвертер")
        self.cancel = threading.Event()
        files = list(self.files)

        def work():
            from .config import expand
            from . import media
            ctx = M.Ctx(models_dir=expand("models"), workdir=self.app.agent._workdir(), cancel=self.cancel, arg=arg,
                        progress=lambda f, s: job.progress.emit(f, s))
            if M.needs_ffmpeg(code):
                ctx.ffmpeg = media.find_ffmpeg(self.app.cfg)
            if M.needs_whisper(code) and getattr(self.app, "voice", None):
                ctx.whisper = self.app.voice.stt._load
            return M.run(code, files, ctx)

        job = Job(work)
        self._run(job, title, lambda res, secs: self._show_result(title, code, files, res, secs))

    def _run(self, job: Job, title: str, then):
        self.popup.show_busy(title, "Начинаю…")
        self.popup.add_button("■ Стоп", self.cancel.set)
        job.progress.connect(lambda f, s: self.popup.status.setText(f"{int(f * 100)} % — {s}"))
        job.done.connect(lambda res, err, secs: self.popup.show_text("Не получилось", err, copy=False) if err
                         else then(res, secs))
        self.jobs = [j for j in self.jobs if j.isRunning()] + [job]
        job.start()

    def _install_ffmpeg(self, code: str, arg: str):
        from . import media
        self.cancel = threading.Event()
        job = Job(lambda: media.install_ffmpeg(lambda f, s: job.progress.emit(f, s), self.cancel))
        self._run(job, "Скачиваю ffmpeg", lambda res, secs: self.start(code, arg))

    # ------------------------------------------------------------------ готово
    def _show_result(self, title: str, code: str, src: list[Path], res, secs: float):
        if res and isinstance(res[0], tuple):          # SHA256
            text = "\n\n".join(f"**{p.name}**\n`{h}`" for p, h in res)
            self.popup.show_text("SHA256", text, copy_text="\n".join(f"{h}  {p.name}" for p, h in res))
            return
        res = [Path(p) for p in res or []]
        if not res:
            self.popup.show_text(title, "Готово.", copy=False)
            return
        before = sum(f.stat().st_size for f in src if f.is_file())
        after = sum(_size(p) for p in res)
        lines = [f"✓ Готово за {_dur(secs)}"]
        for p in res[:8]:
            lines.append(f"• {p.name} — {C.human_size(_size(p))}")
        if len(res) > 8:
            lines.append(f"…и ещё {len(res) - 8}")
        if before and after and "compress" in code:
            lines.append(f"Было {C.human_size(before)} → стало {C.human_size(after)}")
        first = res[0]
        self.popup.show_text(title, "\n\n".join(lines), [
            ("📂 Показать", lambda: _reveal(first)),
            ("▶ Открыть", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(first)))),
        ], markdown=False, copy_text="\n".join(str(p) for p in res))


def _size(p: Path) -> int:
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return p.stat().st_size if p.exists() else 0


def _reveal(p: Path):
    if sys.platform == "win32":
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(p)], creationflags=0x08000000)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))


def _dur(s: float) -> str:
    return f"{s:.0f} с" if s < 90 else f"{s / 60:.0f} мин"


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
