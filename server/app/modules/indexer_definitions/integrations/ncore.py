"""
@author Sappi <https://github.com/s4pp1/stremhu-source>
@website https://stremhu.hu
"""

import datetime
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.modules.indexer_definitions.base_indexer_definition import (
    BaseIndexerDefinition,
)
from app.modules.indexer_definitions.schemas.internal import (
    AuthCredentialError,
    AuthError,
    AuthSessionError,
    IndexerDefinitionFindTorrentsResult,
    IndexerDefinitionLoginPayload,
    IndexerDefinitionTorrent,
)
from app.modules.media_attributes.constants import MediaAttributeKey


class NcoreIndexerDefinition(BaseIndexerDefinition):
    @property
    def id(self) -> str:
        return "ncore"

    @property
    def name(self) -> str:
        return "nCore"

    @property
    def requires_full_download(self) -> bool:
        return False

    @property
    def supports_totp(self) -> bool:
        return True

    @property
    def url(self) -> str:
        return "https://ncore.pro"

    @property
    def login_path(self) -> str:
        return "/login.php"

    @property
    def details_path(self) -> str:
        return "/torrents.php?action=details&id={torrent_id}"

    def _detect_authentication_error(
        self,
        response: httpx.Response,
    ) -> AuthError:
        final_path = str(response.url.path)
        original_url = str(response.request.url)
        if response.history:
            original_url = str(response.history[0].url)

        ended_up_at_login = self.login_path in final_path

        if ended_up_at_login:
            if self.login_path in original_url:
                return AuthCredentialError()
            return AuthSessionError()

        return None

    async def _login(
        self,
        payload: IndexerDefinitionLoginPayload,
    ) -> httpx.Response:
        data = {"nev": payload.username, "pass": payload.password}
        if payload.totp_code:
            data["2factor"] = payload.totp_code

        return await self._client.post(
            self.login_path,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    async def _fetch_torrents(
        self, imdb_id: str, page: int | None = None
    ) -> IndexerDefinitionFindTorrentsResult:
        current_page = page or 1
        response = await self._client.get(
            "/torrents.php",
            params={
                "oldal": str(current_page),
                "miben": "imdb",
                "mire": imdb_id,
                "miszerint": "seeders",
                "hogyan": "DESC",
                "jsons": True,
                "tipus": "all",
            },
        )

        try:
            data = response.json()
        except Exception:
            tree = LexborHTMLParser(response.text)
            error_node = tree.css_first(".lista_mini_error")
            error_text = error_node.text(strip=True) if error_node else None
            if error_text == "Nincs találat!":
                return IndexerDefinitionFindTorrentsResult(torrents=[])
            raise Exception(error_text or "Ismeretlen nCore hiba.")

        torrents: list[IndexerDefinitionTorrent] = []

        for torrent in data.get("results", []):
            category = torrent.get("category", "")
            torrents.append(
                IndexerDefinitionTorrent(
                    imdb_id=torrent.get("imdb_id"),
                    torrent_id=str(torrent["torrent_id"]),
                    seeders=int(torrent.get("seeders", 0)),
                    download_url=torrent["download_url"],
                    attribute_ids=[
                        self._resolve_language(category),
                        self._resolve_resolution(category),
                    ],
                )
            )

        total = int(data.get("total_results", 0))
        limit = int(data.get("perpage", 1)) or 1
        last_page = -(-total // limit)  # math.ceil

        return IndexerDefinitionFindTorrentsResult(
            torrents=torrents,
            next_page=current_page + 1 if current_page < last_page else None,
        )

    async def _fetch_torrent(
        self,
        torrent_id: str,
    ) -> IndexerDefinitionTorrent | None:
        details_url = self.details_path.replace("{torrent_id}", torrent_id)
        response = await self._client.get(details_url)

        tree = LexborHTMLParser(response.text)

        html_node = tree.css_first("html")
        if html_node and "Nem található az adatbázisunkban" in html_node.text():
            return None

        download_node = tree.css_first(
            f'.download a[href*="torrents.php?action=download&id={torrent_id}"]'
        )
        download_path = download_node.attributes.get("href") if download_node else None

        imdb_node = tree.css_first('a[href*="imdb.com/title/"]')
        imdb_anchor_href = imdb_node.attributes.get("href") if imdb_node else None
        imdb_id = (
            imdb_anchor_href.rstrip("/").split("/")[-1] if imdb_anchor_href else None
        )

        if not download_path:
            raise Exception("A letöltési link nem található!")

        return IndexerDefinitionTorrent(
            torrent_id=torrent_id,
            imdb_id=imdb_id,
            download_url=urljoin(self.url, download_path),
        )

    async def _fetch_hit_and_run_ids(self) -> list[str]:
        return list(await self._fetch_hit_and_run_entries())

    async def _fetch_hit_and_run_entries(self) -> dict[str, datetime.timedelta | None]:
        response = await self._client.get("/hitnrun.php", params={"showall": "false"})
        return parse_hit_and_run_page(response.text, self.url)

    def _resolve_resolution(self, category: str) -> str:
        if "hd" in category:
            return MediaAttributeKey.R720P
        return MediaAttributeKey.R480P

    def _resolve_language(self, category: str) -> str:
        if "hun" in category:
            return MediaAttributeKey.HUN
        return MediaAttributeKey.ENG


_DETAILS_LINK = 'a[href*="torrents.php?action=details&id="]'

# A „Hátravan” oszlop, pl. „10ó 36p”, „45ó 19p”, „1n 2ó 5p”, „47p”. A többi
# időpont-oszlop („2 napja”, „24 perce”, „3 órája”) szavakkal van kiírva, azokra
# ezek a minták nem illeszkednek.
_REMAINING_FULL = re.compile(
    r"(?:(\d+)\s*n(?:ap)?\s+)?(\d+)\s*ó\s*(\d+)\s*p(?![a-záéíóöőúüű])", re.IGNORECASE
)
_REMAINING_MINUTES = re.compile(
    r"(?<![\d.,])(\d+)\s*p(?![a-záéíóöőúüű])", re.IGNORECASE
)


def parse_remaining(text: str) -> datetime.timedelta | None:
    """A sor szövegéből kiolvassa a hátralévő seedidőt (az utolsó egyezést)."""
    matches = list(_REMAINING_FULL.finditer(text))
    if matches:
        days, hours, minutes = matches[-1].groups()
        return datetime.timedelta(
            days=int(days or 0), hours=int(hours), minutes=int(minutes)
        )
    matches = list(_REMAINING_MINUTES.finditer(text))
    if matches:
        return datetime.timedelta(minutes=int(matches[-1].group(1)))
    return None


def _row_of(link: LexborNode) -> LexborNode:
    """A torrent sora: a legkülső szülő, amelyben még csak ez az egy link van."""
    row = link
    parent = row.parent
    while parent is not None and len(parent.css(_DETAILS_LINK)) == 1:
        row = parent
        parent = row.parent
    return row


def parse_hit_and_run_page(
    html: str, base_url: str
) -> dict[str, datetime.timedelta | None]:
    tree = LexborHTMLParser(html)

    content = tree.css_first("#main_tartalom")
    if not content:
        raise Exception("A tartalom nem található.")

    entries: dict[str, datetime.timedelta | None] = {}
    for link in content.css(_DETAILS_LINK):
        href = link.attributes.get("href")
        if not href:
            continue
        full_url = urljoin(base_url, href)
        id_val = parse_qs(urlparse(full_url).query).get("id", [None])[0]
        if not id_val or id_val in entries:
            continue
        entries[id_val] = parse_remaining(_row_of(link).text(separator=" "))

    return entries
