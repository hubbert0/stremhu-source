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
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
# A Nuvio a képet URL szerint tárolja: ha a képek változnak, ezt kell emelni.
VERSION = 2

# A Nuvio 16 dp magas, legfeljebb 92 dp széles helyre teszi a képet: 4× felbontás.
HEIGHT = 64
MAX_WIDTH = 368
PADDING = 8
FONT_SIZE = 46
MIN_FONT_SIZE = 20

WHITE = "#FFFFFF"
GREEN = "#4ADE80"
ORANGE = "#FBBF24"
BLUE = "#60A5FA"
CHIP = "#0A0C0C"  # a Nuvio saját méretjelzőjének színe
READY = "#15803D"  # TorBox: azonnal megnézhető
WAIT = "#B45309"  # TorBox: előbb le kell tölteni

GROUPS = [
    {"id": "torbox", "name": "TorBox", "color": READY, "isExpanded": True},
    {"id": "forras", "name": "Forrás", "color": BLUE, "isExpanded": True},
    {"id": "hang", "name": "Hang", "color": GREEN, "isExpanded": True},
    {"id": "felirat", "name": "Felirat", "color": GREEN, "isExpanded": True},
]

# (azonosító, csoport, név, minta, [(szöveg, szín), ...], címke háttérszíne)
# A kiegészítő neve a „Forrás: <indexelő> · <kiegészítő>” sor végén áll.
# A „megnézhető / le kell tölteni” sor csak debrid-szolgáltatónál jelenik meg,
# és nálunk az egyetlen ilyen a TorBox.
BADGES = [
    ("torbox-azonnal", "torbox", "TorBox – azonnal", r"Azonnal megnézhető",
     [("TORBOX · AZONNAL", WHITE)], READY),
    ("torbox-letoltes", "torbox", "TorBox – letöltés", r"Előbb le kell tölteni",
     [("TORBOX · LETÖLTÉS", WHITE)], WAIT),
    ("forras-torbox", "forras", "TorBox", r"Forrás: TorBox",
     [("FORRÁS: ", WHITE), ("TORBOX", GREEN)], CHIP),
    ("forras-ncore", "forras", "nCore", r"Forrás: nCore\b",
     [("FORRÁS: ", WHITE), ("NCORE", ORANGE)], CHIP),
    ("forras-torrentio", "forras", "Torrentio", r"Forrás: (?:[^:·]+· )?Torrentio",
     [("FORRÁS: ", WHITE), ("TORRENTIO", BLUE)], CHIP),
    ("forras-comet", "forras", "Comet", r"Forrás: (?:[^:·]+· )?Comet",
     [("FORRÁS: ", WHITE), ("COMET", BLUE)], CHIP),
    ("forras-mediafusion", "forras", "MediaFusion", r"Forrás: (?:[^:·]+· )?MediaFusion",
     [("FORRÁS: ", WHITE), ("MEDIAFUSION", BLUE)], CHIP),
    ("forras-stremthru", "forras", "StremThru", r"Forrás: (?:[^:·]+· )?StremThru Torz",
     [("FORRÁS: ", WHITE), ("STREMTHRU", BLUE)], CHIP),
    # A hang nyelve biztos (nCore-kategória vagy bemért fájl): „🔊 Hang: …”.
    ("hang-magyar", "hang", "Magyar hang", r"Hang:[^:]*🇭🇺 magyar",
     [("HANG: ", WHITE), ("MAGYAR", GREEN)], CHIP),
    ("hang-idegen", "hang", "Idegen hang", r"Hang:(?![^:]*(?:🇭🇺 magyar|nem tudni))",
     [("HANG: ", WHITE), ("IDEGEN", ORANGE)], CHIP),
    ("felirat-magyar", "felirat", "Magyar felirat", r"Felirat:[^:]*🇭🇺 magyar",
     [("FELIRAT: ", WHITE), ("MAGYAR", GREEN)], CHIP),
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
    for old in HERE.glob("*.png"):
        old.unlink()
    for badge_id, group, name, pattern, parts, chip in BADGES:
        file_name = f"{badge_id}-v{VERSION}.png"
        render(parts, HERE / file_name)
        filters.append(
            {
                "id": badge_id,
                "groupId": group,
                "name": name,
                "pattern": pattern,
                "imageURL": f"{BASE_URL}/{file_name}",
                "isEnabled": True,
                "tagColor": chip,
                "tagStyle": "filled",
                "borderColor": chip,
            }
        )
    payload = {"filters": filters, "groups": GROUPS}
    (HERE / "jelvenyek.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
