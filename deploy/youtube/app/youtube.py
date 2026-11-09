"""A YouTube Data API v3 elérése – csak olvasás, API-kulccsal.

Kvóta (alapból napi 10 000 egység): minden itt használt kérés 1 egység, egy
oldal legfeljebb 50 elem. Keresést (search.list, 100 egység) nem használunk:
a csatornák feltöltéseit és lejátszási listáit olvassuk be, és helyben
keresünk bennük.
"""

import re
from dataclasses import dataclass
from typing import Any

import httpx

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
MAX_ITEMS_PER_LIST = 5000

_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)
# A törölt vagy privát videók címe a lejátszási listákban.
_UNAVAILABLE_TITLES = {"private video", "deleted video"}


class YouTubeError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Channel:
    id: str
    title: str
    thumb: str
    uploads: str


@dataclass(frozen=True)
class PlaylistEntry:
    video_id: str
    title: str
    position: int
    thumb: str
    published: str


@dataclass(frozen=True)
class Playlist:
    id: str
    title: str
    count: int
    thumb: str


def parse_duration(value: str) -> int:
    """ISO 8601 időtartam (PT1H2M3S) másodpercben; ismeretlennél 0."""
    match = _DURATION.match(value or "")
    if not match:
        return 0
    parts = {key: int(val) for key, val in match.groupdict(default="0").items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def _best_thumb(thumbnails: dict[str, Any] | None) -> str:
    thumbnails = thumbnails or {}
    for size in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbnails.get(size) or {}).get("url")
        if url:
            return str(url)
    return ""


class YouTubeClient:
    def __init__(
        self,
        api_key: str,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._http = httpx.AsyncClient(
            base_url=YOUTUBE_API, timeout=timeout, transport=transport
        )
        self.units = 0  # az ebben a folyamatban elhasznált kvóta

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **params: str | int) -> dict[str, Any]:
        query = {key: str(value) for key, value in params.items()}
        query["key"] = self._key
        try:
            response = await self._http.get(path, params=query)
        except httpx.HTTPError as error:
            # Az URL-ben benne van a kulcs, ezért csak a hiba típusát írjuk ki.
            raise YouTubeError(f"nem érhető el ({type(error).__name__})") from error
        self.units += 1
        try:
            body = response.json()
        except ValueError as error:
            raise YouTubeError(
                f"érvénytelen válasz (HTTP {response.status_code})",
                response.status_code,
            ) from error
        if response.status_code >= 400:
            reason = ""
            errors = (body.get("error") or {}).get("errors") or []
            if errors:
                reason = str(errors[0].get("reason") or "")
            raise YouTubeError(
                f"HTTP {response.status_code} {reason}".strip(), response.status_code
            )
        return body

    async def channel(self, ref: str) -> Channel | None:
        params: dict[str, str | int] = {"part": "snippet,contentDetails"}
        if ref.startswith("@"):
            params["forHandle"] = ref
        else:
            params["id"] = ref
        body = await self._get("/channels", **params)
        items = body.get("items") or []
        if not items:
            return None
        item = items[0]
        uploads = (
            (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
        ).get("uploads") or ""
        snippet = item.get("snippet") or {}
        return Channel(
            id=str(item["id"]),
            title=str(snippet.get("title") or item["id"]),
            thumb=_best_thumb(snippet.get("thumbnails")),
            uploads=str(uploads),
        )

    async def playlist_items(self, playlist_id: str) -> list[PlaylistEntry]:
        entries: list[PlaylistEntry] = []
        token = ""
        while len(entries) < MAX_ITEMS_PER_LIST:
            params: dict[str, str | int] = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if token:
                params["pageToken"] = token
            body = await self._get("/playlistItems", **params)
            for item in body.get("items") or []:
                snippet = item.get("snippet") or {}
                details = item.get("contentDetails") or {}
                video_id = str(details.get("videoId") or "")
                title = str(snippet.get("title") or "")
                if not video_id or title.strip().lower() in _UNAVAILABLE_TITLES:
                    continue
                entries.append(
                    PlaylistEntry(
                        video_id=video_id,
                        title=title,
                        position=int(snippet.get("position") or len(entries)),
                        thumb=_best_thumb(snippet.get("thumbnails")),
                        published=str(
                            details.get("videoPublishedAt")
                            or snippet.get("publishedAt")
                            or ""
                        ),
                    )
                )
            token = str(body.get("nextPageToken") or "")
            if not token:
                break
        return entries

    async def durations(self, video_ids: list[str]) -> dict[str, int]:
        """Videóhosszak másodpercben. A nem nyilvános videók kimaradnak."""
        found: dict[str, int] = {}
        for start in range(0, len(video_ids), 50):
            chunk = video_ids[start : start + 50]
            body = await self._get(
                "/videos", part="contentDetails,status", id=",".join(chunk)
            )
            for item in body.get("items") or []:
                status = item.get("status") or {}
                if status.get("privacyStatus") not in (None, "public", "unlisted"):
                    continue
                if status.get("embeddable") is False:
                    continue
                found[str(item["id"])] = parse_duration(
                    str((item.get("contentDetails") or {}).get("duration") or "")
                )
        return found

    async def channel_playlists(self, channel_id: str) -> list[Playlist]:
        playlists: list[Playlist] = []
        token = ""
        while True:
            params: dict[str, str | int] = {
                "part": "snippet,contentDetails",
                "channelId": channel_id,
                "maxResults": 50,
            }
            if token:
                params["pageToken"] = token
            body = await self._get("/playlists", **params)
            for item in body.get("items") or []:
                snippet = item.get("snippet") or {}
                playlists.append(
                    Playlist(
                        id=str(item["id"]),
                        title=str(snippet.get("title") or ""),
                        count=int(
                            (item.get("contentDetails") or {}).get("itemCount") or 0
                        ),
                        thumb=_best_thumb(snippet.get("thumbnails")),
                    )
                )
            token = str(body.get("nextPageToken") or "")
            if not token or len(playlists) >= 1000:
                break
        return playlists
