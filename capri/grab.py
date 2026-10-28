"""Снимок экрана, который видит и полноэкранные игры.

Обычный снимок (PIL ImageGrab, через GDI) у игры в эксклюзивном полноэкранном режиме даёт чёрный кадр.
Поэтому для такого случая берём кадр через видеокарту — DXGI Desktop Duplication (пакет dxcam): тот же
механизм, что у OBS «Захват экрана» и у Xbox Game Bar. Никаких хуков в игру: Windows сама отдаёт копию кадра.

Камера создаётся только на один снимок и сразу закрывается — когда ты ничего не просишь, она не работает
и FPS не трогает.
"""
from __future__ import annotations

import sys
import time


def _monitor_at(x: int, y: int) -> tuple[str, tuple[int, int, int, int]] | None:
    """(\\\\.\\DISPLAY2, рамка монитора) для точки на экране."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    u = ctypes.windll.user32
    u.MonitorFromPoint.restype = wintypes.HMONITOR
    u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    hmon = u.MonitorFromPoint(wintypes.POINT(x, y), 2)          # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFOEXW(cbSize=ctypes.sizeof(MONITORINFOEXW))
    if not u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return None
    r = mi.rcMonitor
    return mi.szDevice, (r.left, r.top, r.right, r.bottom)


def dxgi_available() -> bool:
    import importlib.util
    return sys.platform == "win32" and importlib.util.find_spec("dxcam") is not None


def dxgi_grab(device: str, region: tuple[int, int, int, int] | None = None, timeout: float = 1.5):
    """Кадр монитора device (\\\\.\\DISPLAY1) через видеокарту → PIL.Image или None.

    Снимаем в отдельном коротком процессе: dxcam сам освобождает COM-объекты, а потом их освобождает ещё раз
    comtypes — на Windows это ловится как «access violation». В своём процессе Капри такое не нужно:
    дочерний процесс делает один кадр, сохраняет PNG и завершается, весь мусор уходит вместе с ним."""
    if not dxgi_available():
        return None
    if getattr(sys, "frozen", False):
        return _dxgi_grab_here(device, region, timeout)
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    from PIL import Image
    fd, out = tempfile.mkstemp(suffix=".png", prefix="capri-dxgi-")
    os.close(fd)
    args = [sys.executable, "-m", "capri.grab", device, out, str(timeout)]
    if region:
        args += [str(v) for v in region]
    try:
        r = subprocess.run(args, cwd=str(Path(__file__).resolve().parent.parent), capture_output=True,
                           timeout=timeout + 12, creationflags=0x08000000 if sys.platform == "win32" else 0)
        if r.returncode != 0 or not os.path.getsize(out):
            from .log import log
            log.warning("dxgi: снимок не удался (код %s): %s", r.returncode, r.stderr.decode("utf-8", "ignore")[-300:])
            return None
        with Image.open(out) as im:
            return im.convert("RGB")
    except (OSError, subprocess.SubprocessError):
        from .log import log
        log.exception("dxgi: дочерний процесс")
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def _dxgi_grab_here(device: str, region, timeout: float):
    """Сам снимок через dxcam (в этом процессе)."""
    try:
        import dxcam
        from PIL import Image
        f = getattr(dxcam, "__factory", None) or dxcam.DXFactory()   # фабрика одна на процесс: мониторы и адаптеры
        for d, outs in enumerate(f.outputs):
            for o, out in enumerate(outs):
                if str(out.devicename).lower() != str(device).lower():
                    continue
                cam = dxcam.create(device_idx=d, output_idx=o, output_color="BGRA", processor_backend="numpy")
                try:
                    end = time.monotonic() + timeout
                    frame = None
                    while frame is None and time.monotonic() < end:
                        frame = cam.grab(region=region)       # None — кадр ещё не поменялся, ждём следующий
                        if frame is None:
                            time.sleep(0.03)
                finally:
                    cam.release()
                if frame is None:
                    return None
                return Image.fromarray(frame[:, :, [2, 1, 0]].copy())   # BGRA → RGB
    except Exception:  # noqa: BLE001 — нет DXGI (удалённый рабочий стол, старый драйвер) — вернёмся к обычному снимку
        from .log import log
        log.exception("dxgi: снимок не удался")
    return None


def is_black(img) -> bool:
    """Почти чёрный кадр — так GDI «видит» полноэкранную игру."""
    try:
        small = img.convert("L").resize((64, 36))
        return small.getextrema()[1] < 10
    except Exception:  # noqa: BLE001
        return False


def grab_box(box: tuple[int, int, int, int], prefer_dxgi: bool = False):
    """Снимок прямоугольника экрана (координаты рабочего стола). Сначала обычный, если он чёрный — через видеокарту."""
    from PIL import ImageGrab
    img = None
    if not prefer_dxgi:
        img = ImageGrab.grab(bbox=box, all_screens=True)
        if not is_black(img):
            return img
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    mon = _monitor_at(cx, cy)
    if mon:
        dev, (ml, mt, mr, mb) = mon
        l, t = max(box[0], ml) - ml, max(box[1], mt) - mt
        r, b = min(box[2], mr) - ml, min(box[3], mb) - mt
        region = (l, t, r, b) if r - l > 10 and b - t > 10 else None
        if (dx := dxgi_grab(dev, region)) is not None:
            return dx
    return img if img is not None else ImageGrab.grab(bbox=box, all_screens=True)


def grab_all(prefer_dxgi_at: tuple[int, int] | None = None):
    """Все экраны одной картинкой. Если на каком-то мониторе чёрный кадр (игра) — подставляем кадр через видеокарту."""
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    if sys.platform != "win32" or not dxgi_available():
        return img
    try:
        from . import control
        import ctypes
        x0 = ctypes.windll.user32.GetSystemMetrics(76)
        y0 = ctypes.windll.user32.GetSystemMetrics(77)
        seen = set()
        for (l, t, r, b, _p) in control.monitors():
            mon = _monitor_at((l + r) // 2, (t + b) // 2)
            if not mon or mon[0] in seen:
                continue
            seen.add(mon[0])
            ml, mt, mr, mb = mon[1]
            part = img.crop((ml - x0, mt - y0, mr - x0, mb - y0))
            forced = prefer_dxgi_at and ml <= prefer_dxgi_at[0] < mr and mt <= prefer_dxgi_at[1] < mb
            if forced or is_black(part):
                dx = dxgi_grab(mon[0])
                if dx is not None:
                    img.paste(dx.resize((mr - ml, mb - mt)) if dx.size != (mr - ml, mb - mt) else dx, (ml - x0, mt - y0))
    except Exception:  # noqa: BLE001
        from .log import log
        log.exception("dxgi: склейка экранов не удалась")
    return img


if __name__ == "__main__":       # python -m capri.grab <\\.\DISPLAY1> <out.png> <timeout> [l t r b]
    import os
    _dev, _out, _to = sys.argv[1], sys.argv[2], float(sys.argv[3])
    _reg = tuple(int(v) for v in sys.argv[4:8]) if len(sys.argv) >= 8 else None
    _img = _dxgi_grab_here(_dev, _reg, _to)
    if _img is not None:
        _img.save(_out)
    sys.stdout.flush()
    os._exit(0 if _img is not None else 2)     # без уборки COM при выходе — она и падает
