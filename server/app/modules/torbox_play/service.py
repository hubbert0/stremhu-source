"""nCore-keresés és lejátszás a TorBoxon át – a TMDB-n nem szereplő tartalomhoz.

A Nuvio keresőjéből szabad szöveggel keresünk az nCore-on. A forráslista csak
lekérdezi a TorBox-állapotot; a TorBoxra a lejátszásra kiválasztott verzió
kerül fel, és amíg tölt, egy rövid „még tölti” videóra irányítunk.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.common.logger import logger
from app.common.torrent_info import TorrentFileInfo, TorrentInfo, parse_torrent_info
from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionRecommendedItem,
    IndexerDefinitionSearchHit,
)
from app.modules.indexer_definitions.service import IndexerDefinitionsService
from app.modules.torbox_play.nfo import NfoInfo, parse_nfo
from app.modules.torbox_seed.client import TorboxApiError, TorboxClient
from app.modules.torbox_seed.config import TorboxSeedSettings

INDEXER_ID = "ncore"
SEARCH_TTL = 10 * 60
TORRENT_CACHE_SIZE = 200
NFO_CACHE_SIZE = 1000
NFO_TIMEOUT = 6.0
RECOMMENDED_TTL = 3 * 60 * 60
RECOMMENDED_RETRY = 10 * 60
IMDB_CACHE_SIZE = 5000
# A borítós „Staff által jelölt” sáv egy nagyobb készletből mutat 6 címet, és
# legfeljebb félóránként cseréli őket; ezért félóránként lekérjük az oldalt, és
# gyűjtjük, amit 7 napon belül láttunk.
STAFF_REFRESH_MINUTES = 30
STAFF_GROUP = "staff"
STAFF_KEEP = 7 * 24 * 60 * 60

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


@dataclass
class TorboxStates:
    by_hash: dict[str, TorboxState] = field(default_factory=dict)
    by_name: dict[str, TorboxState] = field(default_factory=dict)

    def find(self, info_hash: str | None, name: str) -> TorboxState | None:
        if info_hash and info_hash.lower() in self.by_hash:
            return self.by_hash[info_hash.lower()]
        return self.by_name.get(name.strip().lower())


def _state_of(item: dict[str, Any]) -> TorboxState:
    return TorboxState(
        torbox_id=int(item["id"]),
        finished=bool(item.get("download_finished"))
        and bool(item.get("download_present", True)),
        progress=float(item.get("progress") or 0),
        state=str(item.get("download_state") or ""),
        files=list(item.get("files") or []),
    )


class TorboxPlayService:
    def __init__(
        self,
        settings: TorboxSeedSettings,
        indexer_definitions_service: IndexerDefinitionsService,
        transport: httpx.AsyncBaseTransport | None = None,
        staff_pool_path: Path | None = None,
    ):
        self._settings = settings
        self._definitions = indexer_definitions_service
        self._transport = transport
        self._search_cache: dict[
            str, tuple[float, list[IndexerDefinitionSearchHit]]
        ] = {}
        self._torrents: dict[str, CachedTorrent] = {}
        # A legutóbbi keresések találatai azonosító szerint (seeder, kategória).
        self._hits: dict[str, IndexerDefinitionSearchHit] = {}
        self._lock = asyncio.Lock()
        # NFO-elemzések torrentenként (a „nincs adat” is, hogy ne kérjük újra).
        self._nfos: dict[str, NfoInfo] = {}
        # Az nCore ajánló oldala (3 óránként frissül, hibánál a régi marad).
        self._recommended: list[IndexerDefinitionRecommendedItem] = []
        self._recommended_at = 0.0
        self._recommended_lock = asyncio.Lock()
        self._recommended_task: asyncio.Task[None] | None = None
        # Az eddig látott staff-ajánlások: torrent → (elem, utoljára látva).
        self._staff_pool_path = staff_pool_path
        self._staff_pool: dict[str, tuple[IndexerDefinitionRecommendedItem, float]] = (
            self._load_staff_pool()
        )
        # Torrent → IMDb-azonosító (az adatlapról; nem változik).
        self._imdb: dict[str, str | None] = {}
        self._imdb_limit = asyncio.Semaphore(3)
        # Egy torrentet egyszerre csak egy kérés töltsön fel (a lejátszók
        # gyakran több kérést is küldenek ugyanarra a linkre).
        self._upload_locks: dict[str, asyncio.Lock] = {}

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
        return await self._cached_search(query.lower(), kind, by_imdb=False)

    async def search_imdb(
        self, imdb_id: str, kind: str
    ) -> list[IndexerDefinitionSearchHit]:
        """Egy TMDB/IMDb-cím kiadásai (a Nuvio rendes adatlapjához)."""
        return await self._cached_search(imdb_id, kind, by_imdb=True)

    async def _cached_search(
        self, query: str, kind: str, *, by_imdb: bool
    ) -> list[IndexerDefinitionSearchHit]:
        key = f"imdb:{query}" if by_imdb else query
        cached = self._search_cache.get(key)
        if cached and time.monotonic() - cached[0] < SEARCH_TTL:
            hits = cached[1]
        else:
            definition = self._definitions.get_by_id(INDEXER_ID)
            if by_imdb:
                hits = await definition.search_by_imdb(query)
            else:
                hits = await definition.search_by_name(query)
            self._search_cache[key] = (time.monotonic(), hits)
            for hit in hits:
                self._hits[hit.torrent_id] = hit
            while len(self._hits) > 2000:
                self._hits.pop(next(iter(self._hits)), None)
            if len(self._search_cache) > 100:
                oldest = min(self._search_cache, key=lambda k: self._search_cache[k][0])
                self._search_cache.pop(oldest, None)
        return [hit for hit in hits if kind_of(hit.category) == kind]

    def hit(self, torrent_id: str) -> IndexerDefinitionSearchHit | None:
        return self._hits.get(torrent_id)

    def remember_hit(self, hit: IndexerDefinitionSearchHit) -> None:
        """Ajánlóból ismert adatok (kategória, seeder) a forráslistához."""
        self._hits.setdefault(hit.torrent_id, hit)

    # ------------------------------------------------------------ ajánló
    async def recommended(self) -> list[IndexerDefinitionRecommendedItem]:
        """Az nCore ajánló elemei. Frissítés 3 óránként a háttérben; amíg
        tart, a régi lista megy ki. Csak legelőször kell rá várni."""
        age = time.monotonic() - self._recommended_at
        if self._recommended and age < RECOMMENDED_TTL:
            return self._recommended
        if self._recommended:
            if self._recommended_task is None or self._recommended_task.done():
                self._recommended_task = asyncio.create_task(
                    self._refresh_recommended()
                )
            return self._recommended
        await self._refresh_recommended()
        return self._recommended

    async def refresh_recommended_now(self) -> None:
        """Az ütemezett, félórás lekérés: a staff-borítók időnként cserélődnek,
        így a gyűjtés akkor is halad, ha senki nem nyitja meg a Nuviót."""
        await self._refresh_recommended(force=True)

    async def _refresh_recommended(self, *, force: bool = False) -> None:
        async with self._recommended_lock:
            if (
                not force
                and self._recommended
                and time.monotonic() - self._recommended_at < RECOMMENDED_TTL
            ):
                return
            definition = self._definitions.get_by_id(INDEXER_ID)
            try:
                items = await definition.fetch_recommended()
            except Exception as error:
                logger.warning("nCore-ajánló nem olvasható: %s", error)
                # Pár perc múlva újrapróbáljuk; addig a régi lista marad.
                self._recommended_at = (
                    time.monotonic() - RECOMMENDED_TTL + RECOMMENDED_RETRY
                )
                return
            if items:
                self._remember_staff(items)
                self._recommended = [
                    i for i in items if i.group != STAFF_GROUP
                ] + self._staff_items()
            self._recommended_at = time.monotonic()

    def _remember_staff(self, items: list[IndexerDefinitionRecommendedItem]) -> None:
        now = time.time()
        for item in items:
            if item.group == STAFF_GROUP:
                self._staff_pool[item.torrent_id] = (item, now)
        for torrent_id, (_, seen) in list(self._staff_pool.items()):
            if now - seen > STAFF_KEEP:
                del self._staff_pool[torrent_id]
        self._save_staff_pool()

    def _staff_items(self) -> list[IndexerDefinitionRecommendedItem]:
        """A gyűjtött staff-ajánlások, a legújabb feltöltés elöl (a nagyobb
        nCore-azonosító későbbi feltöltés)."""
        ordered = sorted(
            (item for item, _ in self._staff_pool.values()),
            key=lambda i: -int(i.torrent_id) if i.torrent_id.isdigit() else 0,
        )
        ranks: dict[str, int] = {}
        result: list[IndexerDefinitionRecommendedItem] = []
        for item in ordered:
            rank = ranks.get(item.section, 0)
            ranks[item.section] = rank + 1
            result.append(item.model_copy(update={"rank": rank}))
        return result

    def _load_staff_pool(
        self,
    ) -> dict[str, tuple[IndexerDefinitionRecommendedItem, float]]:
        path = self._staff_pool_path
        if path is None or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            pool = {
                entry["item"]["torrent_id"]: (
                    IndexerDefinitionRecommendedItem(**entry["item"]),
                    float(entry["seen"]),
                )
                for entry in raw.get("items", [])
            }
        except Exception as error:
            logger.warning("A staff-ajánló gyűjtése nem olvasható: %s", error)
            return {}
        now = time.time()
        return {k: v for k, v in pool.items() if now - v[1] <= STAFF_KEEP}

    def _save_staff_pool(self) -> None:
        path = self._staff_pool_path
        if path is None:
            return
        data = {
            "items": [
                {"item": item.model_dump(), "seen": seen}
                for item, seen in self._staff_pool.values()
            ]
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError as error:
            logger.warning("A staff-ajánló gyűjtése nem menthető: %s", error)

    async def imdb_of(self, torrent_id: str) -> str | None:
        """A torrent IMDb-azonosítója az nCore-adatlapról (egyszer kérjük le)."""
        if torrent_id in self._imdb:
            return self._imdb[torrent_id]
        definition = self._definitions.get_by_id(INDEXER_ID)
        async with self._imdb_limit:
            try:
                found = await definition.find_torrent_by_id(torrent_id)
            except Exception as error:
                logger.info("IMDb nem olvasható (%s): %s", torrent_id, error)
                return None
        imdb_id = found.imdb_id if found and found.imdb_id else None
        if imdb_id and not imdb_id.startswith("tt"):
            imdb_id = None
        if len(self._imdb) >= IMDB_CACHE_SIZE:
            self._imdb.pop(next(iter(self._imdb)), None)
        self._imdb[torrent_id] = imdb_id
        return imdb_id

    def cached_torrent(self, torrent_id: str) -> CachedTorrent | None:
        return self._torrents.get(torrent_id)

    async def torrents(self, torrent_ids: list[str]) -> list[CachedTorrent]:
        """Több .torrent egyszerre; ami nem tölthető le, kimarad."""
        results = await asyncio.gather(
            *(self.torrent(tid) for tid in torrent_ids), return_exceptions=True
        )
        return [r for r in results if isinstance(r, CachedTorrent)]

    async def nfo(self, torrent_id: str) -> NfoInfo:
        """Hang-, felirat- és bitrátaadatok az nCore-os NFO-ból; hibánál üres."""
        cached = self._nfos.get(torrent_id)
        if cached is not None:
            return cached
        definition = self._definitions.get_by_id(INDEXER_ID)
        try:
            text = await asyncio.wait_for(
                definition.fetch_nfo(torrent_id), timeout=NFO_TIMEOUT
            )
            info = await asyncio.to_thread(parse_nfo, text)
        except Exception as error:  # az NFO csak kiegészítés, nem lehet akadály
            logger.info("NFO nem olvasható (%s): %s", torrent_id, error)
            return NfoInfo()
        if len(self._nfos) >= NFO_CACHE_SIZE:
            self._nfos.pop(next(iter(self._nfos)), None)
        self._nfos[torrent_id] = info
        return info

    async def nfos(self, torrent_ids: list[str]) -> dict[str, NfoInfo]:
        results = await asyncio.gather(*(self.nfo(tid) for tid in torrent_ids))
        return dict(zip(torrent_ids, results, strict=True))

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
                return _state_of(item)
        return None

    async def torbox_states(self) -> "TorboxStates":
        """Az összes TorBox-torrent állapota hash és név szerint (egy lekérdezés)."""
        client = self._torbox()
        try:
            items = await client.list_raw()
        except TorboxApiError as error:
            raise TorboxPlayError(str(error)) from error
        except httpx.HTTPError as error:
            raise TorboxPlayError(
                f"A TorBox nem érhető el ({type(error).__name__})."
            ) from error
        states = TorboxStates()
        for item in items:
            state = _state_of(item)
            if item.get("hash"):
                states.by_hash[str(item["hash"]).lower()] = state
            if item.get("name"):
                states.by_name.setdefault(str(item["name"]).strip().lower(), state)
        return states

    async def ensure_on_torbox(self, torrent: CachedTorrent) -> TorboxState | None:
        """A torrent állapota a TorBoxon; ha nincs fent, most feltöltjük."""
        client = self._torbox()
        info_hash = torrent.info.info_hash.lower()
        lock = self._upload_locks.setdefault(info_hash, asyncio.Lock())
        try:
            async with lock:
                state = await self._torbox_state(client, info_hash)
                if state is None:
                    await client.create_torrent(
                        torrent_file=torrent.data,
                        filename=f"{torrent.info.name}.torrent",
                    )
                    state = await self._torbox_state(client, info_hash)
        except TorboxApiError as error:
            raise TorboxPlayError(str(error)) from error
        except httpx.HTTPError as error:
            raise TorboxPlayError(
                f"A TorBox nem érhető el ({type(error).__name__})."
            ) from error
        return state

    async def play_url(self, torrent_id: str, file_index: int | None) -> str | None:
        """Lejátszáskor: ha kész, a fájl friss TorBox-linkje; ha nincs fent,
        most kerül fel a TorBoxra, és None (még tölt). Fájlindex nélkül a
        legnagyobb videófájl (film, egyepizódos kiadás)."""
        torrent = await self.torrent(torrent_id)
        files = {f.index: f for f in torrent.info.files}
        if file_index is None:
            videos = video_files(torrent.info)
            if not videos:
                raise TorboxPlayError("Ebben a torrentben nincs videófájl.")
            file_index = max(videos, key=lambda f: f.size).index
        if file_index not in files:
            raise TorboxPlayError("Nincs ilyen fájl a torrentben.")
        state = await self.ensure_on_torbox(torrent)
        if state is None or not state.finished:
            return None
        client = self._torbox()
        try:
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
