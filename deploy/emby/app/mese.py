"""Mesék: gyerekbarát filmek és sorozatok a TMDB-ből, magyar címmel és plakáttal.

Két kezdőlapi sor („Mesék” – film és sorozat). Az elemek IMDb-azonosítót
kapnak, így az adatlapot a saját TMDB-addon adja (magyarul), a lejátszást pedig
a megszokott források (AIOStreams: StremHU/TorBox, és az Embyk, ha megvan bennük).

Szűrés:
- filmek: animációs ÉS családi, amerikai korhatár-besorolás legfeljebb PG
  („Kicsiknek”: csak G), horror, thriller, bűnügyi és háborús kizárva;
- sorozatok: animációs ÉS gyerek (a TMDB „Kids” műfaja), bűnügyi, háborús,
  hír-, valóság- és talkshow kizárva;
- „Magyar”: magyar gyártású animáció (a régi magyar meséknek többnyire nincs
  amerikai besorolása, ezért itt az nem szűrő).
"""

import asyncio
import datetime
import logging
import re
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from app.cache import Cache

logger = logging.getLogger("emby")

TMDB_API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"

CATALOG_NAME = "Mesék"
MESE_CATALOGS = {"mese-movie": "movie", "mese-series": "series"}
TMDB_KINDS = {"movie": "movie", "series": "tv"}

DEFAULT_OPTION = "Népszerű"
OPTIONS = {
    "movie": ["Népszerű", "Kicsiknek", "Legújabb", "Legjobbak", "Magyar"],
    "series": ["Népszerű", "Legújabb", "Legjobbak", "Magyar"],
}

# A Stremio egy oldalon 100 elemet vár; ha kevesebb jön, nem lapoz tovább.
PAGE_SIZE = 100
MAX_TMDB_PAGES = 50  # legfeljebb ~1000 cím soronként
BATCH = 5  # egyszerre ennyi TMDB-oldalt kér le
CONCURRENCY = 5  # egyszerre ennyi TMDB-kérés (a TMDB kb. 40-50 kérés/mp-et enged)

PAGE_TTL = 6 * 3600
PAGE_RETRY_TTL = 600
LAST_TTL = 14 * 24 * 3600
IMDB_TTL = 30 * 24 * 3600
IMDB_MISS_TTL = 3 * 24 * 3600
# A gyorsítótár kulcsainak változata: ha a tárolt adat formája változik, emelni kell.
CACHE_VERSION = 2

IMDB_RE = re.compile(r"^tt\d{1,10}$")
IMAGE_PATH_RE = re.compile(r"^/[A-Za-z0-9_.-]{1,100}$")


class TmdbError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def discover_params(kind: str, option: str, today: datetime.date) -> dict[str, str]:
    """A TMDB „discover” lekérdezés paraméterei egy sorhoz és szűrőhöz."""
    if kind == "movie":
        params = {
            # 16 animációs, 10751 családi (vesszővel: mindkettő kell)
            "with_genres": "16,10751",
            # 27 horror, 53 thriller, 80 bűnügyi, 10752 háborús
            "without_genres": "27,53,80,10752",
            # G vagy PG (a „gte” kizárja a „Not Rated” besorolást)
            "certification_country": "US",
            "certification.gte": "G",
            "certification.lte": "PG",
            "sort_by": "popularity.desc",
            "vote_count.gte": "50",
        }
        if option == "Kicsiknek":
            params["certification.lte"] = "G"
        elif option == "Legújabb":
            params.update(
                {
                    "sort_by": "primary_release_date.desc",
                    "primary_release_date.lte": today.isoformat(),
                    "vote_count.gte": "10",
                }
            )
        elif option == "Legjobbak":
            params.update({"sort_by": "vote_average.desc", "vote_count.gte": "300"})
        elif option == "Magyar":
            for key in (
                "certification_country",
                "certification.gte",
                "certification.lte",
                "vote_count.gte",
            ):
                del params[key]
            params.update({"with_genres": "16", "with_origin_country": "HU"})
    else:
        params = {
            # 16 animációs, 10762 gyerek
            "with_genres": "16,10762",
            # 80 bűnügyi, 10763 hírek, 10764 valóságshow, 10767 talkshow,
            # 10768 háborús és politikai
            "without_genres": "80,10763,10764,10767,10768",
            "sort_by": "popularity.desc",
            "vote_count.gte": "10",
        }
        if option == "Legújabb":
            params.update(
                {
                    "sort_by": "first_air_date.desc",
                    "first_air_date.lte": today.isoformat(),
                    "vote_count.gte": "3",
                }
            )
        elif option == "Legjobbak":
            params.update({"sort_by": "vote_average.desc", "vote_count.gte": "100"})
        elif option == "Magyar":
            del params["vote_count.gte"]
            params.update({"with_genres": "16", "with_origin_country": "HU"})
    params.update({"include_adult": "false", "language": "hu-HU"})
    return params


class TmdbClient:
    """A TMDB API elérése. A kulcs lehet v3-as API-kulcs vagy v4-es token."""

    def __init__(
        self,
        api_key: str,
        timeout: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        self._params: dict[str, str] = {}
        if api_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            self._params["api_key"] = api_key
        self._http = httpx.AsyncClient(
            base_url=TMDB_API, timeout=timeout, headers=headers, transport=transport
        )
        self._semaphore = asyncio.Semaphore(CONCURRENCY)

    async def close(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = {**self._params, **(params or {})}
        async with self._semaphore:
            for attempt in (1, 2, 3):
                try:
                    response = await self._http.get(path, params=query)
                except httpx.HTTPError as error:
                    # A hibaüzenetbe nem kerül az URL (benne lehet a kulcs).
                    raise TmdbError(
                        f"nem érhető el ({type(error).__name__})"
                    ) from error
                if response.status_code == 429 and attempt < 3:
                    try:
                        wait = float(response.headers.get("Retry-After") or 1)
                    except ValueError:
                        wait = 1.0
                    await asyncio.sleep(min(max(wait, 0.5), 5.0))
                    continue
                if response.status_code == 401:
                    raise TmdbError("a TMDB-kulcs érvénytelen (HTTP 401)", 401)
                if response.status_code >= 400:
                    raise TmdbError(
                        f"HTTP {response.status_code}", response.status_code
                    )
                try:
                    return response.json()
                except ValueError as error:
                    raise TmdbError("érvénytelen válasz") from error
        raise TmdbError("túl sok kérés (HTTP 429)")


def image_url(path: object, size: str) -> str | None:
    text = str(path or "")
    if not IMAGE_PATH_RE.match(text):
        return None
    return f"{IMAGE_BASE}/{size}{text}"


def preview(
    kind: str, result: dict[str, Any], imdb: str, english: str = ""
) -> dict[str, Any] | None:
    poster = image_url(result.get("poster_path"), "w342")
    if poster is None:
        return None
    if kind == "movie":
        name, original = result.get("title"), result.get("original_title")
        date = str(result.get("release_date") or "")
    else:
        name, original = result.get("name"), result.get("original_name")
        date = str(result.get("first_air_date") or "")
    # Magyar fordítás híján a TMDB az eredeti címet adja (pl. japánul):
    # akkor inkább az angol cím.
    if (
        english
        and result.get("original_language") != "hu"
        and name in (None, "", original)
    ):
        name = english
    meta: dict[str, Any] = {
        "id": imdb,
        "type": kind,
        "name": str(name or original or "?"),
        "poster": poster,
        "posterShape": "poster",
    }
    background = image_url(result.get("backdrop_path"), "w1280")
    if background:
        meta["background"] = background
    if date[:4].isdigit():
        meta["releaseInfo"] = date[:4]
    if result.get("overview"):
        meta["description"] = str(result["overview"])
    return meta


class MeseCatalog:
    def __init__(
        self,
        client: TmdbClient,
        cache: Cache,
        today: Callable[[], datetime.date] = datetime.date.today,
    ) -> None:
        self.client = client
        self.cache = cache
        self.today = today

    @staticmethod
    def manifest_catalogs() -> list[dict[str, Any]]:
        return [
            {
                "type": kind,
                "id": catalog_id,
                "name": CATALOG_NAME,
                "extra": [
                    {"name": "genre", "isRequired": False, "options": OPTIONS[kind]},
                    {"name": "skip", "isRequired": False},
                ],
                "extraSupported": ["genre", "skip"],
            }
            for catalog_id, kind in MESE_CATALOGS.items()
        ]

    async def catalog(self, kind: str, extra: dict[str, str]) -> list[dict[str, Any]]:
        if kind not in TMDB_KINDS:
            return []
        option = extra.get("genre") or DEFAULT_OPTION
        if option not in OPTIONS[kind]:
            option = DEFAULT_OPTION
        try:
            skip = max(0, int(extra.get("skip", "0")))
        except ValueError:
            skip = 0
        last_key = f"mese-last{CACHE_VERSION}:{kind}:{option}:{skip}"
        params = discover_params(kind, option, self.today())
        try:
            metas = await self._collect(kind, params, skip + PAGE_SIZE)
        except TmdbError as error:
            logger.warning("Mesék: a TMDB nem érhető el: %s", error)
            last = self.cache.get_json(last_key)
            return list(last) if last is not None else []
        result = metas[skip : skip + PAGE_SIZE]
        if result:
            self.cache.set_json(last_key, result, LAST_TTL)
        return result

    async def _collect(
        self, kind: str, params: dict[str, str], needed: int
    ) -> list[dict[str, Any]]:
        """A sor elemei sorban, amíg `needed` darab össze nem jön."""
        key_base = f"mese{CACHE_VERSION}:{kind}:" + urllib.parse.urlencode(
            sorted(params.items())
        )
        metas: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(found: list[dict[str, Any]]) -> None:
            for meta in found:
                if meta["id"] not in seen:
                    seen.add(meta["id"])
                    metas.append(meta)

        total, found = await self._page(kind, params, key_base, 1)
        add(found)
        last_page = min(total, MAX_TMDB_PAGES)
        page = 2
        while len(metas) < needed and page <= last_page:
            pages = range(page, min(page + BATCH, last_page + 1))
            for _, found in await asyncio.gather(
                *(self._page(kind, params, key_base, number) for number in pages)
            ):
                add(found)
            page = pages.stop
        return metas

    async def _page(
        self, kind: str, params: dict[str, str], key_base: str, page: int
    ) -> tuple[int, list[dict[str, Any]]]:
        key = f"{key_base}&page={page}"
        cached = self.cache.get_json(key)
        if cached is not None:
            return int(cached["total"]), list(cached["metas"])
        data = await self.client.get(
            f"/discover/{TMDB_KINDS[kind]}", {**params, "page": str(page)}
        )
        results = [
            result
            for result in (data.get("results") or [])
            if isinstance(result, dict)
            and isinstance(result.get("id"), int)
            and not result.get("adult")
            and image_url(result.get("poster_path"), "w342")
        ]
        lookups = await asyncio.gather(
            *(self._details(kind, result["id"]) for result in results)
        )
        metas: list[dict[str, Any]] = []
        for result, (info, _) in zip(results, lookups, strict=True):
            if info:
                meta = preview(kind, result, info["imdb"], info["en"])
                if meta:
                    metas.append(meta)
        try:
            total = int(data.get("total_pages") or 0)
        except (TypeError, ValueError):
            total = 0
        # Ha egy azonosító lekérése nem sikerült, az oldalt csak rövid ideig tároljuk.
        complete = all(ok for _, ok in lookups)
        self.cache.set_json(
            key,
            {"total": total, "metas": metas},
            PAGE_TTL if complete else PAGE_RETRY_TTL,
        )
        return total, metas

    async def _details(
        self, kind: str, tmdb_id: int
    ) -> tuple[dict[str, str] | None, bool]:
        """({"imdb": IMDb-azonosító, "en": angol cím} vagy None, ha nincs
        IMDb-azonosító; és hogy sikerült-e a lekérdezés)."""
        key = f"mese-info:{kind}:{tmdb_id}"
        cached = self.cache.get_json(key)
        if isinstance(cached, dict):
            return (cached if cached.get("imdb") else None), True
        params = {"language": "en-US"}
        if kind == "series":
            params["append_to_response"] = "external_ids"
        try:
            data = await self.client.get(f"/{TMDB_KINDS[kind]}/{tmdb_id}", params)
        except TmdbError as error:
            if error.status != 404:
                logger.info(
                    "Mesék: az adatok nem kérhetők le (%s %s): %s",
                    kind,
                    tmdb_id,
                    error,
                )
                return None, False
            data = {}
        if not isinstance(data, dict):
            data = {}
        if kind == "movie":
            imdb, english = data.get("imdb_id"), data.get("title")
        else:
            external = data.get("external_ids")
            imdb = external.get("imdb_id") if isinstance(external, dict) else None
            english = data.get("name")
        info = {"imdb": str(imdb or ""), "en": str(english or "")}
        if not IMDB_RE.match(info["imdb"]):
            info["imdb"] = ""
        self.cache.set_json(key, info, IMDB_TTL if info["imdb"] else IMDB_MISS_TTL)
        return (info if info["imdb"] else None), True
