"""Önteszt a szerveren: docker compose exec emby python -m app.selftest

Minden beállított Emby-szerverre belép, megszámolja a filmeket és a sorozatokat,
majd kiírja a Stremio-telepítési linket.
"""

import asyncio
import sys

from app.config import load_settings
from app.emby import EmbyClient, EmbyError
from app.service import LIBRARY_TYPES, SKIPPED_LIBRARIES


async def check() -> int:
    settings = load_settings()
    if not settings.servers:
        print(
            "Nincs beállítva Emby-szerver. Töltsd ki a .env-ben: EMBY1_NEV, EMBY1_URL,"
        )
        print(
            "EMBY1_FELHASZNALO, EMBY1_JELSZO (és ugyanígy EMBY2_...), majd: sh install.sh"
        )
        return 1
    failures = 0
    for server in settings.servers:
        client = EmbyClient(server, settings.timeout)
        print(f"\n=== {server.name} ({server.url})")
        try:
            await client.login()
            movies = await client.count("Movie")
            series = await client.count("Series")
            print(
                f"  Belépés rendben ({server.username}). Filmek: {movies}, sorozatok: {series}"
            )
            print("  Könyvtárak (ezek lesznek a sorok a Stremio kezdőlapján):")
            for view in await client.views():
                view_id = str(view.get("Id") or "")
                kind = str(view.get("CollectionType") or "vegyes")
                if kind.lower() in SKIPPED_LIBRARIES:
                    print(f"    – {view.get('Name')} ({kind}): kimarad")
                    continue
                films = await client.count(LIBRARY_TYPES["movie"], view_id)
                shows = await client.count(LIBRARY_TYPES["series"], view_id)
                print(
                    f"    – {view.get('Name')} ({kind}): {films} film, {shows} sorozat"
                )
        except EmbyError as error:
            failures += 1
            print(f"  HIBA: {error}")
        finally:
            await client.close()
    print()
    if not settings.secret_ok:
        print("HIBA: hiányzik az EMBY_ADDON_SECRET – futtasd az install.sh-t.")
        return 1
    print("Telepítési link (az AIOStreamsbe vagy a Stremióba – titokként kezeld):")
    print(f"  {settings.addon_base}/manifest.json")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(check()))
