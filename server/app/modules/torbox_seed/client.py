from typing import Any

import httpx

from app.modules.torbox_seed.logic import TorboxTorrent, parse_torbox_torrent

TORBOX_API_URL = "https://api.torbox.app"


class TorboxApiError(Exception):
    pass


class TorboxClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = TORBOX_API_URL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
            transport=self._transport,
        )

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError as e:
            raise TorboxApiError(
                f"Érvénytelen TorBox-válasz (HTTP {response.status_code})"
            ) from e

        if response.status_code >= 400 or not body.get("success", False):
            detail = body.get("detail") or body.get("error") or "ismeretlen hiba"
            raise TorboxApiError(f"TorBox-hiba (HTTP {response.status_code}): {detail}")

        return body.get("data")

    async def list_torrents(self) -> list[TorboxTorrent]:
        async with self._client() as client:
            response = await client.get(
                "/v1/api/torrents/mylist",
                params={"bypass_cache": "true", "limit": 1000},
            )

        # Üres lista esetén a TorBox 404-et is adhat.
        if response.status_code == 404:
            return []

        data = self._unwrap(response)
        if not data:
            return []
        if isinstance(data, dict):
            data = [data]

        return [parse_torbox_torrent(item) for item in data]

    async def _control(self, torrent_id: int, operation: str) -> None:
        async with self._client() as client:
            response = await client.post(
                "/v1/api/torrents/controltorrent",
                json={"torrent_id": torrent_id, "operation": operation},
            )
        self._unwrap(response)

    async def stop_seeding(self, torrent_id: int) -> None:
        """Leállítja a seedelést. A fájl a TorBoxon marad, és lejátszható.

        (A TorBox már nem ismeri a „pause” műveletet; az elfogadottak:
        reannounce, delete, resume, stop_seeding.)"""
        await self._control(torrent_id, "stop_seeding")

    async def resume_torrent(self, torrent_id: int) -> None:
        await self._control(torrent_id, "resume")

    async def list_raw(self) -> list[dict[str, Any]]:
        """A torrentlista nyers formában (állapot, haladás, méret is)."""
        async with self._client() as client:
            response = await client.get(
                "/v1/api/torrents/mylist",
                params={"bypass_cache": "true", "limit": 1000},
            )
        if response.status_code == 404:
            return []
        data = self._unwrap(response)
        if not data:
            return []
        return [data] if isinstance(data, dict) else list(data)

    async def create_torrent(
        self,
        *,
        torrent_file: bytes | None = None,
        filename: str = "upload.torrent",
        magnet: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Új torrent a TorBoxra (.torrent fájlból vagy magnetlinkből).

        A seedelésről a fiók alapbeállítása dönt (seed=1); a helykezelés a
        Hit'n'Run teljesülése után amúgy is leállítja."""
        # A TorBox multipart űrlapot vár, magnetlinknél is.
        parts: dict[str, tuple[str | None, bytes | str, str] | tuple[None, str]] = {
            "seed": (None, "1"),
            "allow_zip": (None, "false"),
        }
        if torrent_file is not None:
            parts["file"] = (filename, torrent_file, "application/x-bittorrent")
        elif magnet:
            parts["magnet"] = (None, magnet)
        else:
            raise TorboxApiError("Nincs megadva torrent.")
        async with self._client() as client:
            response = await client.post("/v1/api/torrents/createtorrent", files=parts)
        data = self._unwrap(response) or {}
        try:
            detail = str(response.json().get("detail") or "")
        except ValueError:
            detail = ""
        return (data if isinstance(data, dict) else {}), detail
