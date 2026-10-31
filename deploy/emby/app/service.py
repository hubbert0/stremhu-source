"""Stremio-katalógusok, adatlapok és lejátszási linkek az Emby-szerverekből."""

import asyncio
import logging
import re
import urllib.parse
from typing import Any

from app.cache import Cache
from app.config import VERSION, Settings
from app.emby import EmbyClient, EmbyError, imdb_id, tmdb_id

logger = logging.getLogger("emby")

EMBY_TYPES = {"movie": "Movie", "series": "Series"}
CATALOG_RE = re.compile(r"^emby([1-9])-(movie|series)$")
EMBY_ID_RE = re.compile(r"^emby:([1-9]):([A-Za-z0-9]{1,64})$")
EXTERNAL_ID_RE = re.compile(r"^(tt\d{1,10}|tmdb:\d{1,10})(?::(\d{1,4}):(\d{1,5}))?$")
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")

CATALOG_TTL = 300
LOOKUP_TTL = 3600
MISS_TTL = 600
EPISODES_TTL = 600
IMAGE_TTL = 7 * 24 * 3600


def parse_extra(extra: str | None) -> dict[str, str]:
    if not extra:
        return {}
    parsed = urllib.parse.parse_qs(extra, keep_blank_values=False)
    return {key: values[0] for key, values in parsed.items() if values}


def human_size(size: float | str | None) -> str:
    try:
        value = float(size or 0)
    except ValueError:
        return ""
    if value <= 0:
        return ""
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GB"
    return f"{value / 1024**2:.0f} MB"


def resolution(video: dict[str, Any] | None) -> tuple[str, int]:
    """Felbontás címkéje és rangja. A szélességet is nézi (pl. 1920×800 = 1080p)."""
    if not video:
        return "SD", 0
    width = int(video.get("Width") or 0)
    height = int(video.get("Height") or 0)
    if width >= 3200 or height >= 2000:
        return "4K", 4
    if width >= 1800 or height >= 1000:
        return "1080p", 3
    if width >= 1200 or height >= 700:
        return "720p", 2
    if height:
        return f"{height}p", 1
    return "SD", 0


def is_hdr(video: dict[str, Any] | None) -> bool:
    if not video:
        return False
    text = " ".join(
        str(video.get(key) or "")
        for key in ("VideoRange", "VideoRangeType", "DisplayTitle", "ExtendedVideoType")
    ).lower()
    return "hdr" in text or "dolby vision" in text or "dovi" in text


def basename(path: object) -> str:
    text = str(path or "")
    return re.split(r"[\\/]", text)[-1] if text else ""


class EmbyService:
    def __init__(
        self, settings: Settings, cache: Cache, clients: dict[int, EmbyClient]
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.clients = clients

    # ------------------------------------------------------------- manifest

    def manifest(self) -> dict[str, Any]:
        catalogs: list[dict[str, Any]] = []
        for index, client in sorted(self.clients.items()):
            for kind, label in (("movie", "Filmek"), ("series", "Sorozatok")):
                catalogs.append(
                    {
                        "type": kind,
                        "id": f"emby{index}-{kind}",
                        "name": f"{client.server.name}: {label}",
                        "extra": [
                            {"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False},
                        ],
                    }
                )
        names = ", ".join(client.server.name for client in self.clients.values())
        return {
            "id": "hu.stremhu.emby",
            "version": VERSION,
            "name": "Emby-könyvtár",
            "description": "A saját Emby-szerverek filmjei és sorozatai a kezdőlapon, "
            "lejátszás közvetlenül az Embyről."
            + (f" Szerverek: {names}." if names else ""),
            "resources": [
                "catalog",
                {"name": "meta", "types": ["movie", "series"], "idPrefixes": ["emby:"]},
                {
                    "name": "stream",
                    "types": ["movie", "series"],
                    "idPrefixes": ["tt", "tmdb:", "emby:"],
                },
            ],
            "types": ["movie", "series"],
            "catalogs": catalogs,
            "behaviorHints": {"configurable": False, "configurationRequired": False},
        }

    # ------------------------------------------------------------- segédek

    def stremio_id(self, index: int, item: dict[str, Any]) -> str:
        imdb = imdb_id(item)
        if imdb:
            return imdb
        tmdb = tmdb_id(item)
        if tmdb:
            return f"tmdb:{tmdb}"
        return f"emby:{index}:{item.get('Id')}"

    def image_url(
        self, index: int, item: dict[str, Any], kind: str = "Primary"
    ) -> str | None:
        item_id = str(item.get("Id") or "")
        if not ITEM_ID_RE.match(item_id):
            return None
        if kind == "Primary" and "Primary" not in (item.get("ImageTags") or {}):
            return None
        if kind == "Backdrop" and not item.get("BackdropImageTags"):
            return None
        folder = "kep" if kind == "Primary" else "hatter"
        return f"{self.settings.addon_base}/{folder}/{index}/{item_id}.jpg"

    def preview(self, index: int, kind: str, item: dict[str, Any]) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "id": self.stremio_id(index, item),
            "type": kind,
            "name": item.get("Name") or "?",
            "posterShape": "poster",
        }
        poster = self.image_url(index, item)
        if poster:
            meta["poster"] = poster
        if item.get("ProductionYear"):
            meta["releaseInfo"] = str(item["ProductionYear"])
        if item.get("Genres"):
            meta["genres"] = list(item["Genres"])[:4]
        if item.get("Overview"):
            meta["description"] = str(item["Overview"])
        return meta

    # ------------------------------------------------------------ katalógus

    async def catalog(
        self, kind: str, catalog_id: str, extra: dict[str, str]
    ) -> list[dict[str, Any]]:
        match = CATALOG_RE.match(catalog_id)
        if not match or match.group(2) != kind:
            return []
        index = int(match.group(1))
        client = self.clients.get(index)
        if client is None:
            return []
        try:
            skip = max(0, int(extra.get("skip", "0")))
        except ValueError:
            skip = 0
        search = (extra.get("search") or "").strip()[:100] or None
        key = f"catalog:{index}:{kind}:{skip}:{search or ''}"
        cached = self.cache.get_json(key)
        if cached is not None:
            return list(cached)
        try:
            items = await client.items(
                EMBY_TYPES[kind], skip, self.settings.page_size, search
            )
        except EmbyError as error:
            logger.warning(
                "%s: a katalógus nem tölthető be: %s", client.server.name, error
            )
            return []
        metas = [self.preview(index, kind, item) for item in items if item.get("Id")]
        self.cache.set_json(key, metas, CATALOG_TTL)
        return metas

    # ------------------------------------------------------------- adatlap

    async def meta(self, kind: str, stremio_id: str) -> dict[str, Any] | None:
        match = EMBY_ID_RE.match(stremio_id)
        if not match or kind not in EMBY_TYPES:
            return None
        index, item_id = int(match.group(1)), match.group(2)
        client = self.clients.get(index)
        if client is None:
            return None
        try:
            item = await client.item(item_id)
            meta = self.preview(index, kind, item)
            meta["id"] = stremio_id
            background = self.image_url(index, item, "Backdrop")
            if background:
                meta["background"] = background
            if kind == "series":
                meta["videos"] = [
                    self._video(index, episode)
                    for episode in await client.episodes(item_id)
                    if episode.get("Id")
                ]
        except EmbyError as error:
            logger.warning(
                "%s: az adatlap nem tölthető be: %s", client.server.name, error
            )
            return None
        return meta

    def _video(self, index: int, episode: dict[str, Any]) -> dict[str, Any]:
        video: dict[str, Any] = {
            "id": f"emby:{index}:{episode['Id']}",
            "title": episode.get("Name") or "",
            "season": int(episode.get("ParentIndexNumber") or 0),
            "episode": int(episode.get("IndexNumber") or 0),
        }
        if episode.get("PremiereDate"):
            video["released"] = episode["PremiereDate"]
        if episode.get("Overview"):
            video["overview"] = episode["Overview"]
        thumbnail = self.image_url(index, episode)
        if thumbnail:
            video["thumbnail"] = thumbnail
        return video

    # ------------------------------------------------------------- lejátszás

    async def streams(self, kind: str, stremio_id: str) -> list[dict[str, Any]]:
        if kind not in EMBY_TYPES:
            return []
        found: list[tuple[int, int, dict[str, Any]]] = []
        emby = EMBY_ID_RE.match(stremio_id)
        if emby:
            client = self.clients.get(int(emby.group(1)))
            if client is not None:
                found = await self._safe(
                    client, self._item_streams(client, emby.group(2))
                )
        else:
            external = EXTERNAL_ID_RE.match(stremio_id)
            if not external:
                return []
            raw, season, episode = external.groups()
            provider, value = (
                ("tmdb", raw[5:]) if raw.startswith("tmdb:") else ("imdb", raw)
            )
            results = await asyncio.gather(
                *(
                    self._safe(
                        client,
                        self._lookup_streams(
                            client,
                            kind,
                            provider,
                            value,
                            int(season) if season else None,
                            int(episode) if episode else None,
                        ),
                    )
                    for client in self.clients.values()
                )
            )
            found = [stream for result in results for stream in result]
        found.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [stream for _, _, stream in found]

    async def _safe(
        self, client: EmbyClient, work: Any
    ) -> list[tuple[int, int, dict[str, Any]]]:
        try:
            return list(await work)
        except EmbyError as error:
            logger.warning("%s: nincs lejátszási link: %s", client.server.name, error)
            return []

    async def _lookup_streams(
        self,
        client: EmbyClient,
        kind: str,
        provider: str,
        value: str,
        season: int | None,
        episode: int | None,
    ) -> list[tuple[int, int, dict[str, Any]]]:
        ids = await self._find_ids(client, EMBY_TYPES[kind], provider, value)
        if kind == "movie":
            item_ids = ids
        else:
            if season is None or episode is None:
                return []
            item_ids = []
            for series_id in ids:
                for found in await self._episodes(client, series_id):
                    first = found.get("IndexNumber")
                    last = found.get("IndexNumberEnd") or first
                    if first is None or last is None:
                        continue
                    if found.get("ParentIndexNumber") == season and int(
                        first
                    ) <= episode <= int(last):
                        item_ids.append(str(found["Id"]))
        streams: list[tuple[int, int, dict[str, Any]]] = []
        for item_id in item_ids:
            streams.extend(await self._item_streams(client, item_id))
        return streams

    async def _find_ids(
        self, client: EmbyClient, item_type: str, provider: str, value: str
    ) -> list[str]:
        key = f"find:{client.server.index}:{item_type}:{provider}:{value}"
        cached = self.cache.get_json(key)
        if cached is not None:
            return list(cached)
        ids = [
            str(item["Id"]) for item in await client.find(item_type, provider, value)
        ]
        self.cache.set_json(key, ids, LOOKUP_TTL if ids else MISS_TTL)
        return ids

    async def _episodes(
        self, client: EmbyClient, series_id: str
    ) -> list[dict[str, Any]]:
        key = f"episodes:{client.server.index}:{series_id}"
        cached = self.cache.get_json(key)
        if cached is not None:
            return list(cached)
        episodes = [
            {
                "Id": episode.get("Id"),
                "ParentIndexNumber": episode.get("ParentIndexNumber"),
                "IndexNumber": episode.get("IndexNumber"),
                "IndexNumberEnd": episode.get("IndexNumberEnd"),
            }
            for episode in await client.episodes(series_id)
            if episode.get("Id")
        ]
        self.cache.set_json(key, episodes, EPISODES_TTL)
        return episodes

    async def _item_streams(
        self, client: EmbyClient, item_id: str
    ) -> list[tuple[int, int, dict[str, Any]]]:
        if not ITEM_ID_RE.match(item_id):
            return []
        return [
            self._stream(client, item_id, source)
            for source in await client.media_sources(item_id)
            if source.get("Id")
        ]

    def _stream(
        self, client: EmbyClient, item_id: str, source: dict[str, Any]
    ) -> tuple[int, int, dict[str, Any]]:
        server = client.server
        media = source.get("MediaStreams") or []
        video = next((m for m in media if m.get("Type") == "Video"), None)
        label, rank = resolution(video)
        quality = label
        if video and video.get("Codec"):
            quality += f" {str(video['Codec']).upper()}"
        if is_hdr(video):
            quality += " HDR"
        languages: list[str] = []
        for stream in media:
            language = str(stream.get("Language") or "").lower()
            if stream.get("Type") == "Audio" and language and language not in languages:
                languages.append(language)
        size = int(source.get("Size") or 0)
        filename = basename(source.get("Path")) or str(source.get("Name") or "")
        lines = [filename] if filename else []
        lines.append(" · ".join(part for part in (quality, human_size(size)) if part))
        if languages:
            lines.append("🔊 " + ", ".join(languages[:4]))

        subtitles = []
        for stream in media:
            if (
                stream.get("Type") == "Subtitle"
                and stream.get("IsExternal")
                and stream.get("IsTextSubtitleStream", True)
                and stream.get("Index") is not None
            ):
                subtitles.append(
                    {
                        "id": f"emby-{server.index}-{item_id}-{stream['Index']}",
                        "lang": str(stream.get("Language") or "und").lower(),
                        "url": client.subtitle_url(
                            item_id, str(source["Id"]), int(stream["Index"])
                        ),
                    }
                )

        container = str(source.get("Container") or "").lower()
        hints: dict[str, Any] = {
            "bingeGroup": f"emby-{server.index}-{label}",
            "notWebReady": not (
                server.url.startswith("https://") and container == "mp4"
            ),
        }
        if filename:
            hints["filename"] = filename
        if size:
            hints["videoSize"] = size
        stream_entry: dict[str, Any] = {
            "name": f"Emby · {server.name}\n{label}",
            "title": "\n".join(lines),
            "url": client.stream_url(item_id, source),
            "behaviorHints": hints,
        }
        if subtitles:
            stream_entry["subtitles"] = subtitles
        return rank, size, stream_entry

    # ------------------------------------------------------------ plakátok

    async def image(
        self, index: int, item_id: str, kind: str
    ) -> tuple[bytes, str] | None:
        client = self.clients.get(index)
        if client is None or not ITEM_ID_RE.match(item_id):
            return None
        key = f"image:{index}:{item_id}:{kind}"
        cached = self.cache.get_bytes(key)
        content_type = self.cache.get_json(key + ":type")
        if cached is not None and isinstance(content_type, str):
            return cached, content_type
        try:
            data, content_type = await client.image(item_id, kind)
        except EmbyError as error:
            logger.info("%s: nincs kép (%s): %s", client.server.name, item_id, error)
            return None
        self.cache.set_bytes(key, data, IMAGE_TTL)
        self.cache.set_json(key + ":type", content_type, IMAGE_TTL)
        return data, content_type
