"""A Nuvio-gyűjtemények saját csempeképei (960×540, színátmenet + felirat).

Futtatás: python3 keszit.py  – a képek ebbe a mappába kerülnek.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 960, 540
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# fájlnév: (cím, alcím, három szín a bal felső saroktól a jobb alsóig)
TILES = {
    "mesek": ("Mesék", "Filmek és sorozatok", ((255, 176, 59), (255, 94, 140), (124, 77, 255))),
    "termeszet": ("Természet", "Állatok, tájak, bolygónk", ((126, 214, 96), (22, 160, 133), (12, 74, 110))),
    "tudomany": ("Tudomány és űr", "Csillagok, bolygók, felfedezések", ((72, 52, 212), (36, 59, 160), (8, 14, 60))),
    "tortenelem": ("Történelem", "Korok, háborúk, civilizációk", ((240, 180, 90), (178, 98, 44), (70, 34, 20))),
    "dokumentum": ("Dokumentumfilmek", "A legnépszerűbbek és a legjobbak", ((96, 125, 139), (38, 166, 154), (0, 77, 64))),
    "natgeo": ("National Geographic", "Sorozatok", ((255, 214, 0), (255, 160, 0), (196, 98, 0))),
    "discovery": ("Discovery", "Sorozatok", ((79, 172, 254), (0, 112, 192), (0, 40, 104))),
    "curiosity": ("CuriosityStream", "Filmek és sorozatok", ((0, 200, 200), (33, 120, 190), (40, 40, 120))),
    "magellan": ("MagellanTV", "Filmek és sorozatok", ((255, 99, 99), (200, 40, 110), (90, 20, 90))),
}


def gradient(colors: tuple) -> Image.Image:
    c1, c2, c3 = colors
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = x / W * 0.65 + y / H * 0.35
            a, b, f = (c1, c2, t / 0.5) if t < 0.5 else (c2, c3, (t - 0.5) / 0.5)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))
    glow = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(glow)
    draw.ellipse((560, -120, 1060, 330), fill=90)
    draw.ellipse((-150, 300, 350, 760), fill=55)
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    return Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), img, glow)


def tile(title: str, subtitle: str, colors: tuple) -> Image.Image:
    img = gradient(colors)
    draw = ImageDraw.Draw(img)
    size = 118
    while size > 40:
        font = ImageFont.truetype(BOLD, size)
        if draw.textlength(title, font=font) <= W - 116:
            break
        size -= 4
    small = ImageFont.truetype(REGULAR, 40)
    draw.text((58, H - 60 - size - 56), title, font=font, fill=(255, 255, 255))
    draw.text((62, H - 60 - 40), subtitle, font=small, fill=(255, 255, 255))
    return img


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for name, (title, subtitle, colors) in TILES.items():
        tile(title, subtitle, colors).save(here / f"{name}.png", optimize=True)
        print(f"{name}.png")
