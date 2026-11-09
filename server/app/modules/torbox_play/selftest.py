"""Kipróbálja az nCore-keresést és a TorBox-kapcsolatot a szerveren.

docker compose exec stremhu python -m app.modules.torbox_play.selftest "X-Faktor S14"
"""

import asyncio
import sys
from types import SimpleNamespace

# Az összes adatbázis-modell betöltése (a szerver ugyanígy indul).
import app.main  # noqa: F401  # pyright: ignore[reportUnusedImport]
from app.modules.torbox_play import stremio
from app.modules.torbox_play.service import kind_of
from app.modules.torbox_play.stremio import get_torbox_play_service


async def main(query: str) -> int:
    service = get_torbox_play_service()
    print(f"TorBox-kulcs beállítva: {'igen' if service.enabled else 'NEM'}")
    definition = service._definitions.get_by_id("ncore")  # pyright: ignore[reportPrivateUsage]
    hits = await definition.search_by_name(query)
    print(f"nCore-találatok erre: {query!r} – {len(hits)} db")
    for hit in hits[:10]:
        kind = kind_of(hit.category) or "kihagyva"
        print(f"  [{kind:8}] {hit.name}  ({hit.category}, {hit.seeders} seeder)")
    if hits and service.enabled:
        first = next((h for h in hits if kind_of(h.category)), None)
        if first:
            torrent = await service.torrent(first.torrent_id)
            print(
                f"Első találat .torrent: {torrent.info.name}, {len(torrent.info.files)} fájl"
            )
    if hits and service.enabled:
        await show_tiles(query)
    return 0 if hits else 1


async def show_tiles(query: str) -> None:
    """Amit a Nuvio lát: összevont csempék, és az első csempe forráslistája.
    Semmit nem tölt fel a TorBoxra."""
    service = get_torbox_play_service()
    user = SimpleNamespace(api_key="<api-kulcs>")
    for kind in ("series", "movie"):
        previews = await stremio.catalog(service, kind, query)
        print(f"\n{kind}: {len(previews)} csempe")
        for preview in previews[:8]:
            print(f"  {preview.name}  –  {preview.description}")
        if not previews:
            continue
        target = previews[0].id
        detail = await stremio.meta(service, kind, target)
        if detail and detail.videos:
            target = detail.videos[0].id
        print(f"\n  Források ehhez: {target}")
        found = await stremio.streams(service, target, user, "")  # pyright: ignore[reportArgumentType]
        for stream in found:
            name = (stream.name or "").replace("\n", " ")
            print(f"  - {name}")
            for line in (stream.description or "").splitlines():
                print(f"      {line}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or "X-Faktor")))
