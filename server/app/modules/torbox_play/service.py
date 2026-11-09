"""nCore-keresés és lejátszás a TorBoxon át – a TMDB-n nem szereplő tartalomhoz.

A Nuvio keresőjéből szabad szöveggel keresünk az nCore-on. A találat
adatlapja a .torrent fájllistájából készül (évadcsomagnál epizódonként), a
lejátszás pedig a TorBoxon megy: ha a torrent még nincs fent, feltöltjük, és
amíg tölt, egy rövid „még tölti” videót adunk forrásként.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.common.torrent_info import TorrentFileInfo, TorrentInfo, parse_torrent_info
from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionSearchHit,
)
from app.modules.indexer_definitions.service import IndexerDefinitionsService
from app.modules.torbox_seed.client import TorboxApiError, TorboxClient
from app.modules.torbox_seed.config import TorboxSeedSettings

INDEXER_ID = "ncore"
SEARCH_TTL = 10 * 60
TORRENT_CACHE_SIZE = 200

# Csak videós kategóriák (felnőtt, zene, könyv, játék, program kimarad).
SERIES_CATEGORIES = {
    "xvidser_hun",
    "xvidser",
    "dvdser_hun",
    "dvdser",
    "hdser_hun",
    "hdser",
}
MOVIE_CATEGORIES = {
    "xvid_hun",
    "xvid",
    "dvd_hun",
    "dvd",
    "dvd9_hun",
    "dvd9",
    "hd_hun",
    "hd",
}

_EPISODE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})")
_SEASON_PACK = re.compile(r"\b[Ss](\d{1,2})\b(?![ ._-]?[Ee]\d)")
_SAMPLE = re.compile(r"(^|[^a-z])(sample|trailer|extras?)([^a-z]|$)", re.IGNORECASE)


class TorboxPlayError(Exception):
    """A felhasználónak megjeleníthető hiba."""


@dataclass(frozen=True)
class CachedTorrent:
    torrent_id: str
    data: bytes
    info: TorrentInfo
    imdb_id: str | None


@dataclass(frozen=True)
class VideoFile:
    index: int
    name: str
    size: int
    season: int
    episode: int


@dataclass(frozen=True)
class TorboxState:
    torbox_id: int
    finished: bool
    progress: float
    state: str
    files: list[dict[str, Any]]


def kind_of(category: str) -> str | None:
    if category in SERIES_CATEGORIES:
        return "series"
    if category in MOVIE_CATEGORIES:
        return "movie"
    return None


def human_size(size: int) -> str:
    gb = size / 1024**3
    return f"{gb:.1f} GB" if gb >= 1 else f"{size / 1024**2:.0f} MB"


def video_files(info: TorrentInfo) -> list[VideoFile]:
    """A torrent videófájljai epizódszámmal; minta- és extrafájlok nélkül."""
    candidates = [
        f for f in info.files if f.is_video and not _SAMPLE.search(f.path.lower())
    ]
    if not candidates:
        candidates = [f for f in info.files if f.is_video]
    if len(candidates) > 1:
        # A nagyon kicsi kísérőfájlok (pl. 30 mp-es minták) kimaradnak.
        largest = max(f.size for f in candidates)
        candidates = [f for f in candidates if f.size >= largest * 0.05]
    pack_season = _SEASON_PACK.search(info.name)
    default_season = int(pack_season.group(1)) if pack_season else 1
    result: list[VideoFile] = []
    for number, file in enumerate(sorted(candidates, key=lambda f: f.path), 1):
        match = _EPISODE.search(file.name) or _EPISODE.search(file.path)
        season, episode = (
            (int(match.group(1)), int(match.group(2)))
            if match
            else (default_season, number)
        )
        result.append(VideoFile(file.index, file.name, file.size, season, episode))
    return result


def match_torbox_file(
    file: TorrentFileInfo | VideoFile, torbox_files: list[dict[str, Any]]
) -> int | None:
    """A torrent egy fájljának megfelelő TorBox-fájl azonosítója (név + méret)."""
    name = file.name.lower()
    for item in torbox_files:
        torbox_name = str(item.get("name") or item.get("short_name") or "").lower()
        if int(item.get("size") or -1) == file.size and torbox_name.endswith(name):
            return int(item["id"])
    for item in torbox_files:
        if int(item.get("size") or -1) == file.size:
            return int(item["id"])
    return None


class TorboxPlayService:
    def __init__(
        self,
        settings: TorboxSeedSettings,
        indexer_definitions_service: IndexerDefinitionsService,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._definitions = indexer_definitions_service
        self._transport = transport
        self._search_cache: dict[
            str, tuple[float, list[IndexerDefinitionSearchHit]]
        ] = {}
        self._torrents: dict[str, CachedTorrent] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def _torbox(self) -> TorboxClient:
        if not self._settings.api_key:
            raise TorboxPlayError("Nincs beállítva TorBox-kulcs (TORBOX_API_KEY).")
        return TorboxClient(self._settings.api_key.strip(), transport=self._transport)

    # ------------------------------------------------------------ keresés
    async def search(self, query: str, kind: str) -> list[IndexerDefinitionSearchHit]:
        query = " ".join(query.split())
        if len(query) < 2:
            return []
        key = query.lower()
        cached = self._search_cache.get(key)
        if cached and time.monotonic() - cached[0] < SEARCH_TTL:
            hits = cached[1]
        else:
            definition = self._definitions.get_by_id(INDEXER_ID)
            hits = await definition.search_by_name(query)
            self._search_cache[key] = (time.monotonic(), hits)
            if len(self._search_cache) > 100:
                oldest = min(self._search_cache, key=lambda k: self._search_cache[k][0])
                self._search_cache.pop(oldest, None)
        return [hit for hit in hits if kind_of(hit.category) == kind]

    # ------------------------------------------------------------ torrent
    async def torrent(self, torrent_id: str) -> CachedTorrent:
        cached = self._torrents.get(torrent_id)
        if cached:
            return cached
        definition = self._definitions.get_by_id(INDEXER_ID)
        found = await definition.find_torrent_by_id(torrent_id)
        if found is None:
            raise TorboxPlayError("Ez a torrent már nincs fent az nCore-on.")
        data = await definition.download_torrent(found.download_url)
        info = await asyncio.to_thread(parse_torrent_info, data)
        cached = CachedTorrent(torrent_id, data, info, found.imdb_id)
        async with self._lock:
            if len(self._torrents) >= TORRENT_CACHE_SIZE:
                self._torrents.pop(next(iter(self._torrents)), None)
            self._torrents[torrent_id] = cached
        return cached

    # ------------------------------------------------------------ TorBox
    async def _torbox_state(
        self, client: TorboxClient, info_hash: str
    ) -> TorboxState | None:
        for item in await client.list_raw():
            if str(item.get("hash") or "").lower() == info_hash.lower():
                return TorboxState(
                    torbox_id=int(item["id"]),
                    finished=bool(item.get("download_finished"))
                    and bool(item.get("download_present", True)),
                    progress=float(item.get("progress") or 0),
                    state=str(item.get("download_state") or ""),
                    files=list(item.get("files") or []),
                )
        return None

    async def ensure_on_torbox(self, torrent: CachedTorrent) -> TorboxState | None:
        """A torrent állapota a TorBoxon; ha nincs fent, most feltöltjük."""
        client = self._torbox()
        try:
            state = await self._torbox_state(client, torrent.info.info_hash)
            if state is None:
                await client.create_torrent(
                    torrent_file=torrent.data,
                    filename=f"{torrent.info.name}.torrent",
                )
                state = await self._torbox_state(client, torrent.info.info_hash)
        except TorboxApiError as error:
            raise TorboxPlayError(str(error)) from error
        except httpx.HTTPError as error:
            raise TorboxPlayError(
                f"A TorBox nem érhető el ({type(error).__name__})."
            ) from error
        return state

    async def download_url(self, torrent_id: str, file_index: int) -> str:
        """A lejátszáskor kért fájl friss TorBox-linkje."""
        torrent = await self.torrent(torrent_id)
        info_hash = torrent.info.info_hash
        files = {f.index: f for f in torrent.info.files}
        if file_index not in files:
            raise TorboxPlayError("Nincs ilyen fájl a torrentben.")
        client = self._torbox()
        try:
            state = await self._torbox_state(client, info_hash)
            if state is None or not state.finished:
                raise TorboxPlayError("A TorBox még tölti ezt a torrentet.")
            file_id = match_torbox_file(files[file_index], state.files)
            if file_id is None:
                raise TorboxPlayError("A fájl nem található a TorBoxon.")
            return await client.request_download(state.torbox_id, file_id)
        except TorboxApiError as error:
            raise TorboxPlayError(str(error)) from error
        except httpx.HTTPError as error:
            raise TorboxPlayError(
                f"A TorBox nem érhető el ({type(error).__name__})."
            ) from error
