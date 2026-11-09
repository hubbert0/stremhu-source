"""Stremio-válaszok: manifest, katalógus, meta és streamek."""

import logging
from typing import Any
from urllib.parse import unquote

from app.config import VERSION, Settings
from app.index import Index, PlaylistRow, Video
from app.matching import (
    MIN_MOVIE_SECONDS,
    MIN_VIDEO_SECONDS,
    Candidate,
    episode_name,
    episode_numbers,
    excluded,
    has_phrase,
    norm,
    strip_series,
    title_score,
    unique_names,
)
from app.tmdb import TmdbClient, TmdbError

logger = logging.getLogger("youtube")

CATALOG_ID = "ythu-mesek"
PAGE_SIZE = 100
MAX_STREAMS = 6
PLAYLIST_PREFIX = "ytpl:"


def parse_extra(extra: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in (extra or "").split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            values[key] = unquote(value)
    return values


def format_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def stream_for(video: Video, note: str = "") -> dict[str, Any]:
    lines = [video.title, f"{video.channel_title} · {format_duration(video.duration)}"]
    if note:
        lines.append(note)
    return {
        "ytId": video.id,
        "name": "YouTube",
        "title": "\n".join(lines),
        "behaviorHints": {"bingeGroup": f"ythu-{video.channel_id}"},
    }


class YouTubeService:
    def __init__(
        self, settings: Settings, index: Index, tmdb: TmdbClient | None
    ) -> None:
        self._settings = settings
        self._index = index
        self._tmdb = tmdb

    # ---------------------------------------------------------- manifest
    def manifest(self) -> dict[str, Any]:
        genres = [title for _, title, count, _ in self._index.channels() if count]
        return {
            "id": "hu.stremhu.youtube-mesek",
            "version": VERSION,
            "name": "YouTube-mesék",
            "description": (
                "Magyar mesék a hivatalos YouTube-csatornákról. Ha egy mese "
                "nincs meg torrentként, itt is kereshető."
            ),
            "logo": "https://www.youtube.com/s/desktop/favicon_144x144.png",
            "resources": [
                "catalog",
                {"name": "meta", "types": ["series"], "idPrefixes": [PLAYLIST_PREFIX]},
                {
                    "name": "stream",
                    "types": ["movie", "series"],
                    "idPrefixes": ["tt", "tmdb:", PLAYLIST_PREFIX],
                },
            ],
            "types": ["movie", "series"],
            "catalogs": [
                {
                    "type": "series",
                    "id": CATALOG_ID,
                    "name": "YouTube-mesék",
                    "extra": [
                        {"name": "genre", "options": genres, "isRequired": False},
                        {"name": "search", "isRequired": False},
                        {"name": "skip", "isRequired": False},
                    ],
                }
            ],
            "behaviorHints": {"configurable": False},
        }

    # ---------------------------------------------------------- katalógus
    def _playlist_meta(self, playlist: PlaylistRow) -> dict[str, Any]:
        return {
            "id": f"{PLAYLIST_PREFIX}{playlist.id}",
            "type": "series",
            "name": playlist.title,
            "poster": playlist.thumb,
            "posterShape": "landscape",
            "background": playlist.thumb,
            "description": f"{playlist.channel_title} · {playlist.count} mese",
            "genres": [playlist.channel_title],
        }

    def catalog(
        self, kind: str, catalog_id: str, extra: dict[str, str]
    ) -> list[dict[str, Any]]:
        if kind != "series" or catalog_id != CATALOG_ID:
            return []
        playlists = self._index.playlists()
        genre = extra.get("genre")
        if genre:
            playlists = [p for p in playlists if p.channel_title == genre]
        search = extra.get("search")
        if search:
            needle = norm(search)
            playlists = [p for p in playlists if needle and needle in p.norm]
        try:
            skip = max(0, int(extra.get("skip", "0")))
        except ValueError:
            skip = 0
        return [self._playlist_meta(p) for p in playlists[skip : skip + PAGE_SIZE]]

    def meta(self, kind: str, stremio_id: str) -> dict[str, Any] | None:
        if kind != "series" or not stremio_id.startswith(PLAYLIST_PREFIX):
            return None
        playlist = self._index.playlist(stremio_id[len(PLAYLIST_PREFIX) :])
        if playlist is None:
            return None
        meta = self._playlist_meta(playlist)
        meta["videos"] = [
            {
                "id": f"{stremio_id}:1:{number}",
                "title": video.title,
                "season": 1,
                "episode": number,
                "thumbnail": video.thumb,
                "released": video.published or None,
                "overview": f"{video.channel_title} · {format_duration(video.duration)}",
            }
            for number, video in enumerate(self._index.playlist_videos(playlist.id), 1)
        ]
        return meta

    # ---------------------------------------------------------- streamek
    async def streams(self, kind: str, stremio_id: str) -> list[dict[str, Any]]:
        if stremio_id.startswith(PLAYLIST_PREFIX):
            return self._playlist_stream(stremio_id)
        if self._tmdb is None:
            return []
        try:
            resolved = await self._tmdb.resolve(kind, stremio_id)
            if resolved is None:
                return []
            parts = stremio_id.split(":")
            if kind == "series":
                numbers = [p for p in parts[1:] if p.isdigit()]
                if parts[0] == "tmdb":
                    numbers = numbers[1:]
                if len(numbers) < 2:
                    return []
                season, episode = int(numbers[0]), int(numbers[1])
                return await self._series_streams(resolved[1], season, episode)
            return await self._movie_streams(resolved[1])
        except TmdbError as error:
            logger.warning("TMDB-hiba (%s): %s", stremio_id, error)
            return []

    def _playlist_stream(self, stremio_id: str) -> list[dict[str, Any]]:
        parts = stremio_id[len(PLAYLIST_PREFIX) :].split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            return []
        videos = self._index.playlist_videos(parts[0])
        number = int(parts[2])
        if not 1 <= number <= len(videos):
            return []
        return [stream_for(videos[number - 1])]

    async def _movie_streams(self, tmdb_id: int) -> list[dict[str, Any]]:
        assert self._tmdb is not None
        title = await self._tmdb.title("movie", tmdb_id)
        if title is None:
            return []
        names = unique_names(title.names)
        minimum = MIN_MOVIE_SECONDS
        if title.runtime_minutes:
            minimum = int(title.runtime_minutes * 60 * 0.6)
        found: list[tuple[float, Video]] = []
        for video in self._index.videos():
            if excluded(video.norm) or video.duration < max(minimum, MIN_VIDEO_SECONDS):
                continue
            score = title_score(video.norm, names)
            if score:
                found.append((score, video))
        found.sort(key=lambda pair: (-pair[0], -pair[1].duration))
        return [stream_for(video) for _, video in found[:MAX_STREAMS]]

    async def _series_streams(
        self, tmdb_id: int, season: int, episode: int
    ) -> list[dict[str, Any]]:
        assert self._tmdb is not None
        title = await self._tmdb.title("series", tmdb_id, season)
        if title is None:
            return []
        names = unique_names(title.names)
        wanted = episode_name(title.episodes.get(f"{season}:{episode}"))
        absolute = episode
        if season > 1:
            offsets = await self._tmdb.season_offsets(tmdb_id)
            absolute = offsets.get(season, 0) + episode

        candidates: dict[str, Candidate] = {}

        def add(video: Video, score: float, note: str = "") -> None:
            old = candidates.get(video.id)
            if old is None or old.score < score:
                candidates[video.id] = Candidate(video.id, score, note)

        videos = {v.id: v for v in self._index.videos()}
        for video in videos.values():
            if excluded(video.norm) or video.duration < MIN_VIDEO_SECONDS:
                continue
            in_series = title_score(video.norm, names) > 0
            rest = strip_series(video.norm, names) if in_series else video.norm
            # 1. Az epizód címe szerepel a videó címében.
            # Sorozatcím nélkül csak hosszabb epizódcímet fogadunk el.
            if (
                wanted
                and has_phrase(rest, wanted)
                and (in_series or len(wanted.split()) >= 3)
            ):
                add(video, 1.0 if in_series else 0.9)
                continue
            if not in_series:
                continue
            # 2. A részszám szerepel a címben (S01E05, „5. rész”).
            pairs, singles = episode_numbers(video.norm)
            if (season, episode) in pairs or (not pairs and absolute in singles):
                add(video, 0.8)

        # 3. A sorozat nevű lejátszási lista adott sorszámú videója.
        for playlist in self._index.playlists():
            if title_score(playlist.norm, names) < 1.0:
                continue
            items = self._index.playlist_videos(playlist.id)
            if 1 <= absolute <= len(items):
                add(items[absolute - 1], 0.5, "(a lejátszási lista sorrendje szerint)")

        ranked = sorted(candidates.values(), key=lambda c: -c.score)[:MAX_STREAMS]
        return [
            stream_for(videos[c.video_id], c.note)
            for c in ranked
            if c.video_id in videos
        ]
