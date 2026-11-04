"""Önteszt a szerveren: docker compose exec emby python -m app.selftest

Minden beállított Emby-szerverre belép, megszámolja a filmeket és a sorozatokat,
lekéri a Mesék sorokat a TMDB-ből, majd kiírja a Stremio-telepítési linket.
"""

import asyncio
import sys

from app.cache import Cache
from app.config import Settings, load_settings
from app.emby import EmbyClient, EmbyError
from app.mese import (
    OPTIONS,
    TMDB_KINDS,
    MeseCatalog,
    TmdbClient,
    TmdbError,
    discover_params,
)
from app.service import LIBRARY_TYPES, SKIPPED_LIBRARIES


async def check_mese(settings: Settings) -> bool:
    print("\n=== Mesék (TMDB)")
    if not settings.tmdb_api_key:
        print("  Nincs TMDB_API_KEY a .env-ben – a Mesék sorok nem jelennek meg.")
        return True
    client = TmdbClient(settings.tmdb_api_key, settings.timeout)
    mese = MeseCatalog(client, Cache(settings.data_dir / "cache.sqlite"))
    ok = True
    try:
        try:
            await client.get("/configuration")
        except TmdbError as error:
            print(f"  HIBA: a TMDB nem érhető el: {error}")
            return False
        for kind, label in (("movie", "Filmek"), ("series", "Sorozatok")):
            try:
                metas = await mese.catalog(kind, {})
                sample = ", ".join(meta["name"] for meta in metas[:5])
                print(f"  {label}: {len(metas)} cím az első oldalon, pl. {sample}")
                if not metas:
                    ok = False
                counts = []
                for option in OPTIONS[kind][1:]:
                    params = discover_params(kind, option, mese.today())
                    data = await client.get(
                        f"/discover/{TMDB_KINDS[kind]}", {**params, "page": "1"}
                    )
                    counts.append(f"{option}: {data.get('total_results', 0)}")
                print("    szűrők (találat a TMDB-ben): " + ", ".join(counts))
            except TmdbError as error:
                print(f"  HIBA ({label}): {error}")
                ok = False
    finally:
        await client.close()
    return ok


async def check() -> int:
    settings = load_settings()
    failures = 0
    if not settings.servers:
        print(
            "Nincs beállítva Emby-szerver. Töltsd ki a .env-ben: EMBY1_NEV, EMBY1_URL,"
        )
        print(
            "EMBY1_FELHASZNALO, EMBY1_JELSZO (és ugyanígy EMBY2_...), majd: sh install.sh"
        )
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
                try:
                    kind_type = LIBRARY_TYPES[
                        "series" if shows and not films else "movie"
                    ]
                    genres = f"{len(await client.genres(view_id, kind_type))} műfaj"
                except EmbyError as error:
                    genres = f"műfajlista nem tölthető be: {error}"
                print(
                    f"    – {view.get('Name')} ({kind}): {films} film, {shows} sorozat, {genres}"
                )
        except EmbyError as error:
            failures += 1
            print(f"  HIBA: {error}")
        finally:
            await client.close()
    if not await check_mese(settings):
        failures += 1
    print()
    if not settings.secret_ok:
        print("HIBA: hiányzik az EMBY_ADDON_SECRET – futtasd az install.sh-t.")
        return 1
    print("Telepítési link (az AIOStreamsbe vagy a Stremióba – titokként kezeld):")
    print(f"  {settings.addon_base}/manifest.json")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(check()))
