"""A Nuvio-gyűjtemények saját csempeképei (960×540).

Színátmenetes háttér, jobb oldalt egy nagy 3D Noto-emoji, bal alul magyar
felirat Nunito betűvel. Futtatás (internet kell az első alkalommal, a betűt és
az emojikat letölti a forras/ mappába):

    python3 keszit.py
"""

import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 960, 540
HERE = Path(__file__).resolve().parent
SRC = HERE / "forras"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/nunito/Nunito%5Bwght%5D.ttf"
EMOJI_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/3D/png/512/emoji_u{}.png"

# fájlnév: (cím, alcím, emoji kódpont, két szín: bal felső → jobb alsó)
TILES = {
    # Mesék
    "mese-filmek": ("Mesefilmek", "Népszerűek és klasszikusok", "1f984", ((255, 111, 173), (110, 52, 214))),
    "mese-sorozatok": ("Mesesorozatok", "Kicsiknek és nagyobbaknak", "1f9f8", ((255, 179, 71), (229, 57, 53))),
    "mese-magyar": ("Magyar mesék", "Magyar rajzfilmek és sorozatok", "1f98a", ((129, 212, 98), (0, 121, 107))),
    "mese-disney": ("Disney és Pixar", "Rajzfilmek és Disney-csatornák", "1f3f0", ((90, 160, 255), (40, 30, 140))),
    "mese-dreamworks": ("DreamWorks", "Animációs filmek és sorozatok", "1f409", ((38, 198, 218), (21, 72, 160))),
    "mese-ghibli": ("Studio Ghibli", "Japán animációs klasszikusok", "1f343", ((174, 213, 129), (51, 105, 30))),
    "mese-csatornak": ("Gyerekcsatornák", "Disney Junior, Nick Jr., Cartoon Network…", "1f4fa", ((255, 213, 79), (245, 124, 0))),
    # Ismeretterjesztő
    "termeszet": ("Természet", "Állatok, tájak, bolygónk", "1f981", ((139, 195, 74), (0, 105, 92))),
    "tudomany": ("Tudomány és űr", "Csillagok, bolygók, felfedezések", "1fa90", ((92, 107, 192), (20, 16, 70))),
    "tortenelem": ("Történelem", "Korok, háborúk, civilizációk", "1f3db", ((240, 180, 90), (110, 52, 26))),
    "dokumentum": ("Dokumentumfilmek", "A legnépszerűbbek és a legjobbak", "1f3a5", ((84, 110, 122), (0, 77, 64))),
    "natgeo": ("National Geographic", "Sorozatok", "1f9ed", ((255, 202, 40), (191, 54, 12))),
    "curiosity": ("CuriosityStream", "Filmek és sorozatok", "1f4a1", ((0, 188, 212), (40, 53, 147))),
    "magellan": ("MagellanTV", "Filmek és sorozatok", "1f52d", ((255, 112, 67), (106, 27, 154))),
    # Streamingszolgáltatók sor – a kijelölt (színes) változat
    "szinhaz-szines": ("Színház és musical", "Broadway, felvett előadások", "1f3ad", ((236, 64, 122), (74, 20, 140))),
}

# A Streamingszolgáltatók sor nyugalmi kártyái a közösségi csomagban átlátszó
# hátterű, fehér logós képek (a Nuvio sötétszürke kártyáján). Ugyanilyen stílusú
# saját kártya: fehér sziluett + fehér felirat, átlátszó háttérrel.
PLAIN_TILES = {
    "szinhaz": ("Színház és musical", "1f3ad"),
}


def fetch(url: str, target: Path) -> Path:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, target)
    return target


def font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(fetch(FONT_URL, SRC / "Nunito.ttf")), size)
    f.set_variation_by_axes([weight])
    return f


def background(c1: tuple, c2: tuple) -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = min(1.0, max(0.0, x / W * 0.6 + y / H * 0.4))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    light = Image.new("L", (W, H), 0)
    ImageDraw.Draw(light).ellipse((520, -60, 1000, 420), fill=120)
    light = light.filter(ImageFilter.GaussianBlur(110))
    img = Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), img, light)
    shade = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shade).ellipse((-300, 260, 640, 900), fill=120)
    shade = shade.filter(ImageFilter.GaussianBlur(120))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade)


def tile(title: str, subtitle: str, code: str, colors: tuple) -> Image.Image:
    img = background(*colors).convert("RGBA")
    art = Image.open(fetch(EMOJI_URL.format(code), SRC / f"{code}.png")).convert("RGBA")
    art = art.resize((340, 340), Image.LANCZOS).rotate(-8, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", art.size, (0, 0, 0, 0))
    shadow.putalpha(art.getchannel("A").point(lambda a: a * 0.45))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    x, y = W - art.width - 34, 8
    img.alpha_composite(shadow, (x + 14, y + 22))
    img.alpha_composite(art, (x, y))

    draw = ImageDraw.Draw(img)
    size = 96
    while size > 44:
        big = font(size, 900)
        if draw.textlength(title, font=big) <= W - 110:
            break
        size -= 4
    small = font(34, 700)
    title_y = H - 58 - 34 - 16 - size
    for dx, dy, alpha in ((0, 3, 90), (0, 0, 255)):
        fill = (0, 0, 0, alpha) if alpha < 255 else (255, 255, 255, 255)
        draw.text((56 + dx, title_y + dy), title, font=big, fill=fill)
    draw.text((60, H - 58 - 34), subtitle, font=small, fill=(255, 255, 255, 235))
    return img.convert("RGB")


def plain_tile(title: str, code: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    art = Image.open(fetch(EMOJI_URL.format(code), SRC / f"{code}.png")).convert("RGBA")
    art = art.resize((230, 230), Image.LANCZOS)
    # fehér sziluett; a sötét részletek (szem, száj) kivágva maradnak
    lum = art.convert("L").point(lambda v: 255 if v > 95 else 0).filter(ImageFilter.MinFilter(3))
    alpha = Image.composite(art.getchannel("A").point(lambda a: 255 if a > 128 else a * 2),
                            Image.new("L", art.size, 0), lum)
    white = Image.new("RGBA", art.size, (255, 255, 255, 0))
    white.putalpha(alpha)
    big = font(70, 900)
    draw = ImageDraw.Draw(img)
    text_w = draw.textlength(title, font=big)
    total = art.height + 24 + 70
    top = (H - total) // 2
    img.alpha_composite(white, ((W - art.width) // 2, top))
    draw.text(((W - text_w) / 2, top + art.height + 14), title, font=big, fill=(255, 255, 255, 255))
    return img


# Kijelöléskor lejátszott animáció (focusGifUrl). A windowsos Nuvio csak
# többkockás GIF-et játszik le egérrel fölé állva, ezért nem elég egy PNG:
# a nyugalmi képből rövid áttűnéssel vált a színesre, és azon marad.
KIT = "https://raw.githubusercontent.com/itsrenoria/fusion-starter-kit/main/resources/widgets/streaming/square/orangebyte/"
CARD_GRAY = (51, 51, 51, 255)  # a Nuvio sötétszürke kártyája
FOCUS_GIFS = {
    # fájlnév: (nyugalmi kép, színes kép, méret)
    "szinhaz-fokusz.gif": (HERE / "szinhaz.png", HERE / "szinhaz-szines.png", (640, 360)),
    "skyshowtime-fokusz.gif": (
        (KIT + "fusion/skyshowtime-square-fusion.png", "skyshowtime-szurke.png"),
        (KIT + "original/skyshowtime-square.png", "skyshowtime-szines.png"),
        (480, 480),
    ),
}


def load(source, size: tuple) -> Image.Image:
    if isinstance(source, tuple):
        source = fetch(source[0], SRC / source[1])
    img = Image.open(source).convert("RGBA").resize(size, Image.LANCZOS)
    card = Image.new("RGBA", size, CARD_GRAY)
    card.alpha_composite(img)
    return card.convert("RGB")


def focus_gif(still, color, size: tuple, target: Path) -> None:
    a, b = load(still, size), load(color, size)
    steps = 8
    frames = [Image.blend(a, b, i / (steps - 1)) for i in range(steps)]
    frames = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG) for f in frames]
    # utolsó kocka percekig marad, így a kártya színes marad, amíg kijelölt
    durations = [45] * (steps - 1) + [600000]
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)


if __name__ == "__main__":
    for name, (title, subtitle, code, colors) in TILES.items():
        tile(title, subtitle, code, colors).save(HERE / f"{name}.png", optimize=True)
        print(f"{name}.png")
    for name, (title, code) in PLAIN_TILES.items():
        plain_tile(title, code).save(HERE / f"{name}.png", optimize=True)
        print(f"{name}.png")
    for name, (still, color, size) in FOCUS_GIFS.items():
        focus_gif(still, color, size, HERE / name)
        print(name)
