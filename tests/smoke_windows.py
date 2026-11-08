"""Проверка на настоящей Windows (CI): окно впереди, датчик видеокарты, снимок через DXGI два раза подряд."""
import gc
import sys

sys.path.insert(0, ".")
from capri import game, gpu, grab  # noqa: E402

print("foreground:", game.foreground())
print("gpu:", gpu.read())
print("dxgi available:", grab.dxgi_available())
print("screen:", grab.grab_all().size)
m = grab._monitor_at(10, 10)
print("monitor:", m)
for i in range(2):
    dx = grab.dxgi_grab(m[0]) if m else None
    print(f"dxgi frame {i}:", dx and dx.size, "black:", dx is not None and grab.is_black(dx))
    gc.collect()
    print(f"after gc {i}")
box = grab.grab_box((0, 0, 400, 300), prefer_dxgi=True)
print("box via dxgi:", box.size)
# волна 3: конвертер
from capri import convertui, media  # noqa: E402
print("explorer selection:", convertui.explorer_selection())
import pypdfium2, pillow_heif  # noqa: E402,F401
pillow_heif.register_heif_opener()
print("pdfium + heif: ok")
print("ffmpeg found:", media.find_ffmpeg())
# волна 3: админ-аптечка на настоящей Windows (PowerShell + разбор JSON)
from capri import admin  # noqa: E402
for name in ("my_ip", "check_internet", "wifi_password", "uptime", "hardware", "disk_health", "firewall", "defender", "errors_today", "why_reboot",
             "updates", "top_processes", "who_logged_in"):
    try:
        print(f"admin.{name}:", getattr(admin, name)().say[:150])
    except Exception as e:  # noqa: BLE001
        print(f"admin.{name}: FAIL {type(e).__name__}: {str(e)[:150]}")
try:
    print("admin.service_status:", admin.service_status("Spooler").say)
    print("admin.port_open:", admin.port_open("github.com", 443).say)
    print("admin.ping:", admin.ping("github.com", 2))
except Exception as e:  # noqa: BLE001
    print("admin misc: FAIL", e)
import kaldi_native_fbank  # noqa: E402,F401
from capri.live.speakers import fbank  # noqa: E402
import numpy as _np  # noqa: E402
print("fbank:", fbank(_np.random.default_rng(0).standard_normal(16000).astype("float32") * 0.1).shape)
# волна 4: звук компьютера для субтитров (WASAPI loopback) — на CI звуковой карты может не быть
try:
    import soundcard as sc
    spk = sc.default_speaker()
    print("speaker:", spk.name)
    lb = sc.get_microphone(id=str(spk.name), include_loopback=True)
    with lb.recorder(samplerate=48000) as rec:
        d = rec.record(numframes=4800)
    print("loopback frames:", d.shape)
except Exception as e:  # noqa: BLE001
    print("loopback: FAIL", type(e).__name__, str(e)[:150])
print("done")
