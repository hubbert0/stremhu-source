"""Saját torrent hozzáadása a TorBoxhoz (.torrent fájl vagy magnetlink).

A hozzáadott torrent ugyanúgy kerül a TorBoxra, mintha a Stremióból indult
volna: megjelenik a LeLibrary-sorban és a Nuvio felhőnézetében, és ha
nCore-os, a TorBox-helykezelés a Hit'n'Run-ig seedelteti.
"""

import datetime
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.modules.torbox_seed.client import TorboxApiError, TorboxClient
from app.modules.torbox_seed.config import TorboxSeedSettings
from app.modules.torbox_seed.logic import parse_datetime

MAX_TORRENT_BYTES = 10 * 1024 * 1024
RECENT_LIMIT = 10

_MAGNET = re.compile(r"magnet:\?[^\s\"'<>]+", re.IGNORECASE)
_BTIH = re.compile(r"xt=urn:btih:([0-9a-z]{32}|[0-9a-f]{40})", re.IGNORECASE)


class TorboxUploadError(Exception):
    """A felhasználónak megjeleníthető hiba."""


@dataclass(frozen=True)
class AddResult:
    name: str
    hash: str
    message: str


@dataclass(frozen=True)
class RecentTorrent:
    name: str
    state: str
    progress: float
    finished: bool
    size: int
    created_at: datetime.datetime | None


def find_magnet(*texts: str | None) -> str | None:
    """Az első érvényes magnetlink a megadott szövegekből (pl. megosztásból)."""
    for text in texts:
        for match in _MAGNET.findall(text or ""):
            if _BTIH.search(match):
                return match
    return None


def looks_like_torrent(data: bytes) -> bool:
    """Bencode-olt szótár, benne „info” kulccsal – ez minden .torrent fájl."""
    return data.startswith(b"d") and b"4:info" in data


class TorboxUploadService:
    def __init__(
        self,
        settings: TorboxSeedSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def _client(self) -> TorboxClient:
        if not self.enabled or not self._settings.api_key:
            raise TorboxUploadError(
                "Nincs beállítva TorBox-kulcs (TORBOX_API_KEY a .env-ben)."
            )
        return TorboxClient(self._settings.api_key.strip(), transport=self._transport)

    async def add(
        self,
        *,
        torrent_file: bytes | None = None,
        filename: str | None = None,
        magnet: str | None = None,
    ) -> AddResult:
        client = self._client()
        if torrent_file:
            if len(torrent_file) > MAX_TORRENT_BYTES:
                raise TorboxUploadError("A fájl túl nagy, ez nem .torrent fájl.")
            if not looks_like_torrent(torrent_file):
                raise TorboxUploadError("Ez nem érvényes .torrent fájl.")
            name = (filename or "upload.torrent").rsplit("/", 1)[-1][:200]
            call: dict[str, Any] = {"torrent_file": torrent_file, "filename": name}
        else:
            found = find_magnet(magnet)
            if not found:
                raise TorboxUploadError(
                    "Adj meg egy .torrent fájlt vagy egy magnetlinket "
                    "(magnet:?xt=urn:btih:…)."
                )
            call = {"magnet": found}
        try:
            data, detail = await client.create_torrent(**call)
        except TorboxApiError as error:
            raise TorboxUploadError(str(error)) from error
        except httpx.HTTPError as error:
            raise TorboxUploadError(
                f"A TorBox nem érhető el ({type(error).__name__})."
            ) from error
        return AddResult(
            name=str(data.get("name") or filename or "torrent"),
            hash=str(data.get("hash") or ""),
            message=detail or "Hozzáadva a TorBoxhoz.",
        )

    async def recent(self, limit: int = RECENT_LIMIT) -> list[RecentTorrent]:
        client = self._client()
        try:
            items = await client.list_raw()
        except TorboxApiError as error:
            raise TorboxUploadError(str(error)) from error
        except httpx.HTTPError as error:
            raise TorboxUploadError(
                f"A TorBox nem érhető el ({type(error).__name__})."
            ) from error
        torrents = [
            RecentTorrent(
                name=str(item.get("name") or ""),
                state=str(item.get("download_state") or ""),
                progress=float(item.get("progress") or 0),
                finished=bool(item.get("download_finished")),
                size=int(item.get("size") or 0),
                created_at=parse_datetime(item.get("created_at")),
            )
            for item in items
        ]
        oldest = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        torrents.sort(key=lambda t: t.created_at or oldest, reverse=True)
        return torrents[:limit]
