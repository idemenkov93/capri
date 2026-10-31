"""Рисует значок Capri.exe (неоновая звезда в духе Капри) → packaging/capri.ico. Нужен только Pillow."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(7, 11, 22, 255), outline=(29, 53, 80, 255), width=4)


def star(cx, cy, r_out, r_in, n=4, rot=-math.pi / 2):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
g.polygon(star(S / 2, S / 2, 96, 26), fill=(63, 243, 255, 200))
glow = glow.filter(ImageFilter.GaussianBlur(14))
img.alpha_composite(glow)
d = ImageDraw.Draw(img)
d.polygon(star(S / 2 + 4, S / 2 + 3, 92, 24), fill=(255, 42, 109, 255))      # розовая «тень» (глитч)
d.polygon(star(S / 2, S / 2, 92, 24), fill=(63, 243, 255, 255))
d.polygon(star(S / 2, S / 2, 40, 11), fill=(232, 250, 255, 255))
out = Path(__file__).with_name("capri.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
