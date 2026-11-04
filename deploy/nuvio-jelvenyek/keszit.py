#!/usr/bin/env python3
"""A Nuvio-jelvények (Fusion-stílus) elkészítése: PNG-képek és a jelvenyek.json.

A Nuvio a forráskártyák szövegében (név, leírás, fájlnév) reguláris kifejezéssel
keres, és találatkor kirakja a jelvény képét – ugyanolyan fekete címkében, mint
a saját „MÉRET” jelzőjét. A minták az AIOStreams magyar formátumának soraira
illeszkednek („📡 Forrás: nCore”, „🔊 Hang: 🇭🇺 magyar”, „💬 Felirat: …”).

Újragenerálás (a repó gyökeréből):
    python3 deploy/nuvio-jelvenyek/keszit.py
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BASE_URL = (
    "https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/nuvio-jelvenyek"
)
FONT = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"

# A Nuvio 16 dp magas, legfeljebb 92 dp széles helyre teszi a képet: 4× felbontás.
HEIGHT = 64
MAX_WIDTH = 368
PADDING = 10
FONT_SIZE = 38
MIN_FONT_SIZE = 28

WHITE = "#FFFFFF"
GREEN = "#4ADE80"
ORANGE = "#FBBF24"
BLUE = "#60A5FA"
CHIP = "#0A0C0C"  # a Nuvio saját méretjelzőjének színe

GROUPS = [
    {"id": "forras", "name": "Forrás", "color": BLUE, "isExpanded": True},
    {"id": "hang", "name": "Hang", "color": GREEN, "isExpanded": True},
    {"id": "felirat", "name": "Felirat", "color": GREEN, "isExpanded": True},
]

# (azonosító, csoport, név, minta, [(szöveg, szín), ...])
# A kiegészítő neve a „Forrás: <indexelő> · <kiegészítő>” sor végén áll.
BADGES = [
    ("forras-ncore", "forras", "nCore", r"Forrás: nCore\b",
     [("FORRÁS: ", WHITE), ("NCORE", ORANGE)]),
    ("forras-torrentio", "forras", "Torrentio", r"Forrás: (?:[^:·]+· )?Torrentio",
     [("FORRÁS: ", WHITE), ("TORRENTIO", BLUE)]),
    ("forras-comet", "forras", "Comet", r"Forrás: (?:[^:·]+· )?Comet",
     [("FORRÁS: ", WHITE), ("COMET", BLUE)]),
    ("forras-mediafusion", "forras", "MediaFusion", r"Forrás: (?:[^:·]+· )?MediaFusion",
     [("FORRÁS: ", WHITE), ("MEDIAFUSION", BLUE)]),
    ("forras-stremthru", "forras", "StremThru", r"Forrás: (?:[^:·]+· )?StremThru Torz",
     [("FORRÁS: ", WHITE), ("STREMTHRU", BLUE)]),
    # A hang nyelve biztos (nCore-kategória vagy bemért fájl): „🔊 Hang: …”.
    ("hang-magyar", "hang", "Magyar hang", r"Hang:[^:]*🇭🇺 magyar",
     [("HANG: ", WHITE), ("MAGYAR", GREEN)]),
    ("hang-idegen", "hang", "Idegen hang", r"Hang:(?![^:]*(?:🇭🇺 magyar|nem tudni))",
     [("HANG: ", WHITE), ("IDEGEN", ORANGE)]),
    ("felirat-magyar", "felirat", "Magyar felirat", r"Felirat:[^:]*🇭🇺 magyar",
     [("FELIRAT: ", WHITE), ("MAGYAR", GREEN)]),
]


def render(parts: list[tuple[str, str]], path: Path) -> None:
    size = FONT_SIZE
    while True:
        font = ImageFont.truetype(FONT, size)
        widths = [font.getlength(text) for text, _ in parts]
        width = int(sum(widths)) + 2 * PADDING
        if width <= MAX_WIDTH or size <= MIN_FONT_SIZE:
            break
        size -= 1
    image = Image.new("RGBA", (min(width, MAX_WIDTH), HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = PADDING
    for (text, color), text_width in zip(parts, widths, strict=True):
        draw.text((x, HEIGHT / 2), text, font=font, fill=color, anchor="lm")
        x += text_width
    image.save(path, optimize=True)


def main() -> None:
    filters = []
    for badge_id, group, name, pattern, parts in BADGES:
        render(parts, HERE / f"{badge_id}.png")
        filters.append(
            {
                "id": badge_id,
                "groupId": group,
                "name": name,
                "pattern": pattern,
                "imageURL": f"{BASE_URL}/{badge_id}.png",
                "isEnabled": True,
                "tagColor": CHIP,
                "tagStyle": "filled",
                "borderColor": CHIP,
            }
        )
    payload = {"filters": filters, "groups": GROUPS}
    (HERE / "jelvenyek.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
