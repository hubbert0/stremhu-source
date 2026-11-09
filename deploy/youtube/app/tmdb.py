"""TMDB-lekérdezések a címekhez (magyar és eredeti cím, epizódcímek)."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.cache import Cache

TMDB_API = "https://api.themoviedb.org/3"
CACHE_TTL = 7 * 24 * 3600
MISS_TTL = 6 * 3600


class TmdbError(Exception):
    pass


@dataclass(frozen=True)
class Title:
    kind: str  # "movie" vagy "series"
    tmdb_id: int
    names: tuple[str, ...]
    runtime_minutes: int = 0
    episodes: dict[str, str] = field(default_factory=dict)  # "évad:rész" -> cím


class TmdbClient:
    """A kulcs lehet v3-as API-kulcs vagy v4-es token."""

    def __init__(
        self,
        api_key: str,
        cache: Cache,
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
        self._cache = cache
        self._semaphore = asyncio.Semaphore(4)

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **params: str) -> Any:
        key = (
            "tmdb:"
            + path
            + "?"
            + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        )
        cached = self._cache.get_json(key)
        if cached is not None:
            return cached or None
        async with self._semaphore:
            try:
                response = await self._http.get(path, params={**self._params, **params})
            except httpx.HTTPError as error:
                raise TmdbError(f"nem érhető el ({type(error).__name__})") from error
        if response.status_code == 404:
            self._cache.set_json(key, {}, MISS_TTL)
            return None
        if response.status_code >= 400:
            raise TmdbError(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise TmdbError("érvénytelen válasz") from error
        self._cache.set_json(key, body, CACHE_TTL)
        return body

    async def resolve(self, kind: str, stremio_id: str) -> tuple[str, int] | None:
        """Stremio-azonosítóból (tt..., tmdb:...) TMDB-azonosító."""
        base = stremio_id.split(":")
        if base[0] == "tmdb" and len(base) > 1 and base[1].isdigit():
            return kind, int(base[1])
        if base[0].startswith("tt"):
            body = await self._get(f"/find/{base[0]}", external_source="imdb_id")
            if not body:
                return None
            results = body.get("tv_results" if kind == "series" else "movie_results")
            if results:
                return kind, int(results[0]["id"])
        return None

    async def title(
        self, kind: str, tmdb_id: int, season: int | None = None
    ) -> Title | None:
        path = f"/tv/{tmdb_id}" if kind == "series" else f"/movie/{tmdb_id}"
        hu = await self._get(
            path, language="hu-HU", append_to_response="alternative_titles"
        )
        if not hu:
            return None
        names: list[str] = []
        for key in ("name", "title", "original_name", "original_title"):
            if hu.get(key):
                names.append(str(hu[key]))
        alt = hu.get("alternative_titles") or {}
        for entry in alt.get("results") or alt.get("titles") or []:
            if entry.get("iso_3166_1") in ("HU", "US", "GB"):
                names.append(str(entry.get("title") or ""))
        runtime = int(hu.get("runtime") or 0)
        episodes: dict[str, str] = {}
        if kind == "series" and season is not None:
            body = await self._get(f"{path}/season/{season}", language="hu-HU")
            for episode in (body or {}).get("episodes") or []:
                episodes[f"{season}:{episode.get('episode_number')}"] = str(
                    episode.get("name") or ""
                )
            if not runtime:
                runtimes = [int(e) for e in hu.get("episode_run_time") or [] if e]
                runtime = min(runtimes) if runtimes else 0
        return Title(kind, tmdb_id, tuple(n for n in names if n), runtime, episodes)

    async def season_offsets(self, tmdb_id: int) -> dict[int, int]:
        """Évadonként az előző évadok részeinek összege (folyamatos sorszámhoz)."""
        body = await self._get(f"/tv/{tmdb_id}", language="hu-HU")
        offsets: dict[int, int] = {}
        total = 0
        for season in sorted(
            (s for s in (body or {}).get("seasons") or [] if s.get("season_number")),
            key=lambda s: s["season_number"],
        ):
            offsets[int(season["season_number"])] = total
            total += int(season.get("episode_count") or 0)
        return offsets
