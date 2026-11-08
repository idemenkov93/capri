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
for name in ("my_ip", "uptime", "hardware", "disk_health", "firewall", "defender", "errors_today", "why_reboot",
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
print("done")
