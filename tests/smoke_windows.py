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
print("done")
