"""A Stremio-kérések kiszolgálása: cím → feliratok.eu → rangsor → letöltési linkek."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

import httpx

from app.cache import Cache
from app.config import Settings
from app.feliratok import FeliratokClient, Subtitle
from app.matching import pick_show, rank_movies, rank_series, release_score
from app.subfile import build_srt

logger = logging.getLogger("feliratok")

DAY = 24 * 3600
IMDB_RE = re.compile(r"^tt\d{5,10}$")


@dataclass
class Title:
    name: str
    year: int | None


def parse_stremio_id(stremio_id: str) -> tuple[str, int, int] | None:
    """tt123 → (tt123, 0, 0); tt123:1:2 → (tt123, 1, 2)"""
    parts = stremio_id.split(":")
    if not IMDB_RE.match(parts[0]):
        return None
    if len(parts) == 1:
        return parts[0], 0, 0
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return parts[0], int(parts[1]), int(parts[2])
    return None


def parse_extra(extra: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not extra:
        return values
    for part in extra.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            values[key] = unquote(value)
    return values


def _year(value: object) -> int | None:
    match = re.search(r"(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


class SubtitleService:
    def __init__(
        self,
        settings: Settings,
        cache: Cache,
        client: FeliratokClient,
        http: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.client = client
        self.http = http

    async def title(self, kind: str, imdb_id: str) -> Title | None:
        key = f"cinemeta:{kind}:{imdb_id}"
        cached = self.cache.get_json(key)
        if cached is not None:
            return Title(cached["name"], cached["year"]) if cached else None
        try:
            response = await self.http.get(
                f"{self.settings.cinemeta_url}/meta/{kind}/{imdb_id}.json", timeout=15
            )
            response.raise_for_status()
            meta = (response.json() or {}).get("meta") or {}
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Cinemeta hiba (%s): %s", imdb_id, error)
            return None
        name = str(meta.get("name") or "").strip()
        if not name:
            self.cache.set_json(key, {}, DAY)
            return None
        title = Title(name, _year(meta.get("year") or meta.get("releaseInfo")))
        self.cache.set_json(key, {"name": title.name, "year": title.year}, 7 * DAY)
        return title

    async def show_id(self, imdb_id: str, title: Title) -> str | None:
        key = f"show:{imdb_id}"
        cached = self.cache.get_json(key)
        if cached is not None:
            return cached or None
        terms = [title.name, title.name.replace("'", ""), title.name.split(":")[0]]
        found = None
        for term in dict.fromkeys(t.strip() for t in terms if t.strip()):
            shows = await self.client.autoname(term)
            show = pick_show(shows, title.name, title.year)
            if show:
                found = show.id
                break
        self.cache.set_json(key, found or "", 30 * DAY if found else DAY)
        return found

    async def series(
        self, imdb_id: str, season: int, episode: int, filename: str
    ) -> list[Subtitle]:
        title = await self.title("series", imdb_id)
        if title is None:
            return []
        show = await self.show_id(imdb_id, title)
        if not show:
            return []
        key = f"season:{show}:{season}"
        cached = self.cache.get_json(key)
        if cached is None:
            subtitles = await self.client.series_subtitles(show, season)
            self.cache.set_json(key, [s.to_dict() for s in subtitles], 6 * 3600)
        else:
            subtitles = [Subtitle.from_dict(item) for item in cached]
        return rank_series(
            subtitles, season, episode, filename, self.settings.max_results
        )

    async def movie(self, imdb_id: str, filename: str) -> list[Subtitle]:
        title = await self.title("movie", imdb_id)
        if title is None:
            return []
        key = f"movie:{imdb_id}"
        cached = self.cache.get_json(key)
        if cached is None:
            hits = await self.client.movie_search(title.name)
            ranked = rank_movies(hits, title.name, title.year, "", 50)
            self.cache.set_json(key, [s.to_dict() for s in ranked], 12 * 3600)
        else:
            ranked = [Subtitle.from_dict(item) for item in cached]
        if filename:
            ranked.sort(key=lambda s: release_score(s, filename), reverse=True)
        return ranked[: self.settings.max_results]

    async def find(
        self, kind: str, stremio_id: str, extra: dict[str, str]
    ) -> list[dict[str, str]]:
        parsed = parse_stremio_id(stremio_id)
        if parsed is None:
            return []
        imdb_id, season, episode = parsed
        filename = extra.get("filename", "")
        try:
            if kind == "series" and season > 0:
                found = await self.series(imdb_id, season, episode, filename)
            elif kind == "movie":
                found = await self.movie(imdb_id, filename)
            else:
                return []
        except httpx.HTTPError as error:
            logger.warning("feliratok.eu hiba (%s): %s", stremio_id, error)
            return []
        return [
            {
                "id": f"feliratok-{sub.id}",
                "url": f"{self.settings.base_url}/sub/{quote(sub.id)}/{season}/{episode}.srt",
                "lang": "hun",
            }
            for sub in found
        ]

    async def srt(self, subtitle_id: str, season: int, episode: int) -> str:
        key = f"raw:{subtitle_id}"
        raw = self.cache.get_bytes(key)
        if raw is None:
            raw = await self.client.download(subtitle_id)
            if raw:
                self.cache.set_bytes(key, raw, 30 * DAY)
        return build_srt(raw, season, episode)
