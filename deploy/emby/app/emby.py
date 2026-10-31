"""Egy Emby-szerver elérése (Emby REST API).

Saját felhasználóval lép be (Users/AuthenticateByName), a kapott tokennel
kérdez. Ha a token lejár (401), egyszer újra belép.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import VERSION, EmbyServer

logger = logging.getLogger("emby")

ITEM_FIELDS = "ProviderIds,ProductionYear,PremiereDate,Overview,Genres,CommunityRating"


class EmbyError(Exception):
    pass


def provider_value(provider_ids: dict[str, Any] | None, name: str) -> str | None:
    """Egy azonosító (Imdb, Tmdb) a ProviderIds-ből, kis- és nagybetűtől függetlenül."""
    for key, value in (provider_ids or {}).items():
        if key.lower() == name.lower() and value not in (None, ""):
            return str(value).strip()
    return None


def imdb_id(item: dict[str, Any]) -> str | None:
    value = provider_value(item.get("ProviderIds"), "imdb")
    if not value:
        return None
    if value.isdigit():
        value = f"tt{value}"
    return value if value.startswith("tt") and value[2:].isdigit() else None


def tmdb_id(item: dict[str, Any]) -> str | None:
    value = provider_value(item.get("ProviderIds"), "tmdb")
    return value if value and value.isdigit() else None


class EmbyClient:
    def __init__(
        self,
        server: EmbyServer,
        timeout: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.server = server
        self._http = httpx.AsyncClient(
            base_url=server.url,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._token: str | None = None
        self._user_id: str | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    def _auth_header(self) -> str:
        value = (
            f'Emby Client="StremHU Emby-konyvtar", Device="Stremio", '
            f'DeviceId="stremhu-emby-{self.server.index}", Version="{VERSION}"'
        )
        if self._token:
            value += f', Token="{self._token}"'
        return value

    async def _login(self) -> None:
        try:
            response = await self._http.post(
                "/Users/AuthenticateByName",
                json={"Username": self.server.username, "Pw": self.server.password},
                headers={"X-Emby-Authorization": self._auth_header()},
            )
        except httpx.HTTPError as error:
            raise EmbyError(f"nem érhető el ({type(error).__name__})") from error
        if response.status_code != 200:
            raise EmbyError(f"a belépés nem sikerült (HTTP {response.status_code})")
        data = response.json()
        try:
            self._token = str(data["AccessToken"])
            self._user_id = str(data["User"]["Id"])
        except (KeyError, TypeError) as error:
            raise EmbyError("váratlan válasz a belépésre") from error

    async def login(self) -> None:
        async with self._lock:
            if self._token is None:
                await self._login()

    async def _relogin(self, stale_token: str | None) -> None:
        async with self._lock:
            if self._token == stale_token:
                self._token = None
                await self._login()

    @property
    def token(self) -> str:
        if self._token is None:
            raise EmbyError("még nincs belépve")
        return self._token

    @property
    def user_id(self) -> str:
        if self._user_id is None:
            raise EmbyError("még nincs belépve")
        return self._user_id

    async def _request(
        self, path: str, params: dict[str, Any] | None
    ) -> httpx.Response:
        await self.login()
        for attempt in (1, 2):
            token = self._token
            try:
                response = await self._http.get(
                    path,
                    params=params,
                    headers={
                        "X-Emby-Token": token or "",
                        "X-Emby-Authorization": self._auth_header(),
                    },
                )
            except httpx.HTTPError as error:
                raise EmbyError(f"nem érhető el ({type(error).__name__})") from error
            if response.status_code == 401 and attempt == 1:
                await self._relogin(token)
                continue
            if response.status_code >= 400:
                raise EmbyError(f"HTTP {response.status_code}: {path}")
            return response
        raise EmbyError("a belépés nem sikerült")

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._request(path, params)
        try:
            return response.json()
        except ValueError as error:
            raise EmbyError(f"hibás válasz: {path}") from error

    async def get_bytes(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[bytes, str]:
        response = await self._request(path, params)
        return response.content, response.headers.get("content-type", "image/jpeg")

    # ------------------------------------------------------------ lekérdezések

    async def items(
        self, item_type: str, start: int, limit: int, search: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "IncludeItemTypes": item_type,
            "Recursive": "true",
            "Fields": ITEM_FIELDS,
            "StartIndex": start,
            "Limit": limit,
            "EnableImageTypes": "Primary",
            "ImageTypeLimit": 1,
        }
        if search:
            params.update(SearchTerm=search, SortBy="SortName", SortOrder="Ascending")
        else:
            params.update(SortBy="DateCreated,SortName", SortOrder="Descending")
        await self.login()
        data = await self.get_json(f"/Users/{self.user_id}/Items", params)
        return list(data.get("Items") or [])

    async def count(self, item_type: str) -> int:
        await self.login()
        data = await self.get_json(
            f"/Users/{self.user_id}/Items",
            {"IncludeItemTypes": item_type, "Recursive": "true", "Limit": 0},
        )
        return int(data.get("TotalRecordCount") or 0)

    async def find(
        self, item_type: str, provider: str, value: str
    ) -> list[dict[str, Any]]:
        """Film vagy sorozat keresése IMDb- vagy TMDb-azonosító alapján."""
        formats = [f"{provider}.{value}"]
        if provider == "imdb" and value.startswith("tt"):
            formats.append(f"imdb.{value[2:]}")
        await self.login()
        for wanted in formats:
            data = await self.get_json(
                f"/Users/{self.user_id}/Items",
                {
                    "IncludeItemTypes": item_type,
                    "Recursive": "true",
                    "Fields": "ProviderIds",
                    "Limit": 10,
                    "AnyProviderIdEquals": wanted,
                },
            )
            matches = [
                item
                for item in data.get("Items") or []
                if (imdb_id(item) if provider == "imdb" else tmdb_id(item)) == value
            ]
            if matches:
                return matches
        return []

    async def item(self, item_id: str) -> dict[str, Any]:
        await self.login()
        return dict(
            await self.get_json(
                f"/Users/{self.user_id}/Items/{item_id}", {"Fields": ITEM_FIELDS}
            )
        )

    async def episodes(self, series_id: str) -> list[dict[str, Any]]:
        await self.login()
        data = await self.get_json(
            f"/Shows/{series_id}/Episodes",
            {"UserId": self.user_id, "Fields": "ProviderIds,Overview,PremiereDate"},
        )
        return list(data.get("Items") or [])

    async def media_sources(self, item_id: str) -> list[dict[str, Any]]:
        await self.login()
        data = await self.get_json(
            f"/Items/{item_id}/PlaybackInfo", {"UserId": self.user_id}
        )
        return list(data.get("MediaSources") or [])

    async def image(self, item_id: str, kind: str = "Primary") -> tuple[bytes, str]:
        return await self.get_bytes(
            f"/Items/{item_id}/Images/{kind}", {"maxHeight": 600, "quality": 85}
        )

    def stream_url(self, item_id: str, source: dict[str, Any]) -> str:
        container = str(source.get("Container") or "mkv").split(",")[0]
        return (
            f"{self.server.url}/Videos/{item_id}/stream.{container}"
            f"?MediaSourceId={source.get('Id')}&Static=true"
            f"&api_key={self.token}&DeviceId=stremhu-emby-{self.server.index}"
        )

    def subtitle_url(self, item_id: str, source_id: str, index: int) -> str:
        return (
            f"{self.server.url}/Videos/{item_id}/{source_id}/Subtitles/{index}/Stream.srt"
            f"?api_key={self.token}"
        )
