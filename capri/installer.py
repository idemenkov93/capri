"""Шаги установки для мастера настройки: Ollama, модель, голосовые модели, ускорение на видеокарте, Claude Code.

Каждый шаг — обычная функция с колбэком progress(текст, доля 0…1 или None). GUI (ui/setup_wizard.py)
просто вызывает их по очереди в фоновом потоке. Ничего не требует прав администратора:
Ollama и Claude Code ставятся в папку пользователя.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from .config import FROZEN, PROJECT_ROOT

Progress = Callable[[str, "float | None"], None]
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
OLLAMA_SETUP = "https://ollama.com/download/OllamaSetup.exe"
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
CUDA_WHEELS = (("nvidia-cublas-cu12", None), ("nvidia-cudnn-cu12", "10"))   # (пакет, версия «меньше чем»)


def _noop(_t, _f=None):
    pass


def download(url: str, dest: Path, progress: Progress = _noop, label: str = "") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last = 0.0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.monotonic() - last > 0.3:
                    last = time.monotonic()
                    mb = f"{done / 1e6:.0f}" + (f" из {total / 1e6:.0f} МБ" if total else " МБ")
                    progress(f"{label} {mb}", done / total if total else None)
    tmp.replace(dest)
    return dest


# ------------------------------------------------------------------ Ollama
def ollama_exe() -> str | None:
    if exe := shutil.which("ollama"):
        return exe
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(p) if p.exists() else None


def ollama_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def start_ollama(url: str, wait: float = 40) -> bool:
    if ollama_running(url):
        return True
    exe = ollama_exe()
    if not exe:
        return False
    app = Path(exe).with_name("ollama app.exe")      # значок в трее; сам запускает сервер
    cmd = [str(app)] if app.exists() else [exe, "serve"]
    subprocess.Popen(cmd, creationflags=NO_WINDOW | 0x00000008, close_fds=True)   # DETACHED_PROCESS
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait:
        if ollama_running(url):
            return True
        time.sleep(1)
    return False


def install_ollama(url: str, progress: Progress = _noop) -> None:
    if ollama_exe():
        progress("Ollama уже установлена", 1.0)
    else:
        setup = download(OLLAMA_SETUP, Path(tempfile.gettempdir()) / "OllamaSetup.exe", progress, "Скачиваю Ollama:")
        progress("Устанавливаю Ollama (около минуты)…", None)
        subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], check=False)
        if not ollama_exe():
            raise RuntimeError("Ollama не установилась. Скачай её вручную с ollama.com и запусти мастер ещё раз.")
    progress("Запускаю Ollama…", None)
    if not start_ollama(url):
        raise RuntimeError("Ollama установлена, но не отвечает. Запусти её из меню «Пуск» и повтори.")


def installed_models(url: str) -> set[str]:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=5)
        return {m["name"] for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError):
        return set()


def has_model(url: str, name: str) -> bool:
    names = installed_models(url)
    return name in names or (":" not in name and f"{name}:latest" in names)


def pull_model(url: str, name: str, progress: Progress = _noop) -> None:
    """ollama pull с полоской прогресса (Ollama отдаёт её построчно в JSON)."""
    import json
    if has_model(url, name):
        progress(f"Модель {name} уже скачана", 1.0)
        return
    with httpx.stream("POST", f"{url}/api/pull", json={"model": name, "stream": True}, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(f"Ollama: {d['error']}")
            total, done = d.get("total"), d.get("completed")
            if total and done is not None:
                progress(f"Скачиваю {name}: {done / 1e9:.1f} из {total / 1e9:.1f} ГБ", done / total)
            else:
                progress(f"{name}: {d.get('status', '')}", None)
    progress(f"Модель {name} готова", 1.0)


# ------------------------------------------------------------------ голос
def install_vosk(models_dir: Path, progress: Progress = _noop) -> None:
    from .voice.wake import MODEL_NAME
    target = Path(models_dir) / MODEL_NAME
    if (target / "am").is_dir():
        progress("Модель для «Капри» уже есть", 1.0)
        return
    z = download(VOSK_URL, Path(models_dir) / "vosk-ru.zip", progress, "Модель для «Капри»:")
    progress("Распаковываю…", None)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(models_dir)
    z.unlink(missing_ok=True)


def install_whisper(model: str, models_dir: Path, progress: Progress = _noop) -> None:
    """Модель распознавания речи — заранее, чтобы первая фраза не ждала скачивания."""
    progress(f"Скачиваю распознавание речи ({model})…", None)
    from faster_whisper.utils import download_model
    download_model(model, cache_dir=str(Path(models_dir) / "whisper"))
    progress("Распознавание речи готово", 1.0)


def cuda_dir() -> Path:
    return PROJECT_ROOT / "cuda"


def install_cuda(progress: Progress = _noop) -> None:
    """Для Capri.exe: библиотеки CUDA (cuBLAS, cuDNN) прямо из колёс PyPI, ~1.3 ГБ — только DLL.
    Из исходников их ставит run_capri.bat через pip."""
    if not FROZEN:
        progress("Библиотеки CUDA ставит run_capri.bat", 1.0)
        return
    out = cuda_dir()
    out.mkdir(parents=True, exist_ok=True)
    for pkg, below in CUDA_WHEELS:
        mark = out / f"{pkg}.ok"
        if mark.exists():
            continue
        url = wheel_url(pkg, below)
        whl = download(url, out / f"{pkg}.whl", progress, f"{pkg}:")
        progress(f"Распаковываю {pkg}…", None)
        with zipfile.ZipFile(whl) as zf:
            for m in zf.namelist():
                if m.lower().endswith(".dll") and "/bin/" in m:
                    (out / Path(m).name).write_bytes(zf.read(m))
        whl.unlink(missing_ok=True)
        mark.write_text("ok")
    progress("Ускорение на видеокарте готово", 1.0)


def wheel_url(pkg: str, below: str | None = None) -> str:
    """Ссылка на последнее колесо win_amd64 этого пакета (с ограничением версии «меньше чем»)."""
    data = httpx.get(f"https://pypi.org/pypi/{pkg}/json", timeout=30).json()

    def key(v: str):
        return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))

    versions = sorted(data["releases"], key=key, reverse=True)
    for v in versions:
        if below and key(v) >= key(below):
            continue
        for f in data["releases"][v]:
            if f["filename"].endswith("win_amd64.whl") and not f.get("yanked"):
                return f["url"]
    raise RuntimeError(f"не нашла {pkg} для Windows")


# ------------------------------------------------------------------ Claude Code
def claude_exe() -> str | None:
    from .llm.claude_code import find_claude
    return find_claude("claude")


def install_claude(progress: Progress = _noop) -> None:
    """Официальный установщик Claude Code (без прав администратора): irm https://claude.ai/install.ps1 | iex"""
    if claude_exe():
        progress("Claude Code уже установлен", 1.0)
        return
    progress("Устанавливаю Claude Code…", None)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    "irm https://claude.ai/install.ps1 | iex"], check=False, creationflags=NO_WINDOW, timeout=600)
    if not claude_exe():
        raise RuntimeError("Claude Code не установился. Можно поставить позже: irm https://claude.ai/install.ps1 | iex")
    progress("Claude Code установлен — осталось войти в аккаунт", 1.0)


def open_claude_login() -> None:
    """Окно терминала с claude — там вход через браузер (нужна подписка Pro/Max)."""
    exe = claude_exe() or "claude"
    subprocess.Popen(["cmd", "/c", "start", "Claude Code", "cmd", "/k", exe], close_fds=True)
