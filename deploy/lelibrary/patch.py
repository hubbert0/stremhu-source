"""Magyarítás a LeLibraryhoz a build előtt.

- A metaadat-nyelvek közé felveszi a magyart (hu-HU): a címek, leírások és
  képek a TMDB-ből magyarul jönnek, ahol van.
- Új beállításnál alapból a magyar nyelv és a „Use Main Metadata” (IMDb-
  azonosítók) legyen kiválasztva, mert így működik a következő rész
  automatikus indítása, és az adatlapot a saját TMDB-addon adja.

Használat: python3 patch.py <a LeLibrary forrásának mappája>
Ha a forrás annyira megváltozott, hogy valamelyik csere nem illeszkedik,
hibával leáll, és a build nem készül el.
"""

import sys
from pathlib import Path

TARGET = Path("frontend/src/features/setup/full/MetadataExtras.tsx")


def replace_once(text: str, old: str, new: str, count: int = 1) -> str:
    found = text.count(old)
    if found != count:
        sys.exit(f"Nem illeszkedik ({found} találat, {count} kellett): {old!r}")
    return text.replace(old, new)


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    path = root / TARGET
    text = path.read_text(encoding="utf-8")

    if "'hu-HU'" not in text:
        text = replace_once(
            text,
            "  { id: 'ja-JP', name: '日本語' },\n",
            "  { id: 'ja-JP', name: '日本語' },\n  { id: 'hu-HU', name: 'Magyar' },\n",
        )
    text = replace_once(text, "  lang: 'en-US',\n", "  lang: 'hu-HU',\n", count=2)
    text = replace_once(text, "  mainMetadata: false,\n", "  mainMetadata: true,\n")

    path.write_text(text, encoding="utf-8")
    print(f"Kész: {path}")


if __name__ == "__main__":
    main()
