"""Kipróbálja az nCore-keresést és a TorBox-kapcsolatot a szerveren.

docker compose exec stremhu python -m app.modules.torbox_play.selftest "X-Faktor S14"
"""

import asyncio
import sys

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
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or "X-Faktor")))
