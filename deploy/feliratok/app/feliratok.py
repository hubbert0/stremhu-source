"""A feliratok.eu (Super Subtitles) elérése.

A sorozatokhoz a Kodi- és a Bazarr-kiegészítő által is használt JSON-felületet
használjuk:
  index.php?action=autoname&nyelv=0&term=NÉV     – sorozatazonosító keresése
  index.php?action=xbmc&sid=AZON&ev=ÉVAD          – az évad feliratai
  index.php?action=letolt&felirat=AZON            – letöltés (srt vagy tömörített)
A filmeket a kereső HTML-oldaláról olvassuk ki (index.php?search=…&tab=film).
"""

import asyncio
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

YEAR_RE = re.compile(r"\((\d{4})\)")
TRAILING_PARENS_RE = re.compile(r"\(([^()]*)\)\s*$")


@dataclass
class Show:
    id: str
    title: str
    year: int | None


@dataclass
class Subtitle:
    id: str
    language: str
    name: str
    filename: str
    release: str
    season: int
    episode: int  # -1 = évadpakk, 0 = film
    season_pack: bool
    uploader: str
    exact_flags: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Subtitle":
        return cls(**data)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_autoname(data: Any) -> list[Show]:
    """[{"name": "Loki (2021)", "ID": "4567"}, …]"""
    shows: list[Show] = []
    if not isinstance(data, list):
        return shows
    for item in data:
        if not isinstance(item, dict):
            continue
        show_id = str(item.get("ID", "")).strip()
        name = str(item.get("name", "")).strip()
        if not show_id or not name or show_id.startswith("-"):
            continue
        year_match = YEAR_RE.search(name)
        year = int(year_match.group(1)) if year_match else None
        title = YEAR_RE.sub("", name).strip()
        shows.append(Show(id=show_id, title=title, year=year))
    return shows


def release_from_name(name: str) -> str:
    """„Loki - 1x02 (WEB.2160p-KOGi)” → „WEB.2160p-KOGi”"""
    match = TRAILING_PARENS_RE.search(name.strip())
    return match.group(1).strip() if match else ""


def parse_series_results(data: Any) -> list[Subtitle]:
    """Az action=xbmc válasza: szótár (vagy üres lista, ha nincs találat)."""
    if isinstance(data, dict):
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        return []
    subtitles: dict[str, Subtitle] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("felirat"):
            continue
        sub_id = str(item["felirat"]).strip()
        name = str(item.get("nev", "")).strip()
        season_pack = str(item.get("evadpakk", "0")).strip() == "1"
        entry = Subtitle(
            id=sub_id,
            language=str(item.get("language", "")).strip(),
            name=name,
            filename=str(item.get("fnev", "")).strip(),
            release=release_from_name(name),
            season=_int(item.get("evad")),
            episode=-1 if season_pack else _int(item.get("ep"), -1),
            season_pack=season_pack,
            uploader=str(item.get("feltolto", "")).strip(),
            exact_flags=str(item.get("pontos_talalat", "")).count("1"),
        )
        # Ugyanaz a felirat több kiadáshoz is szerepelhet: a kiadásneveket összefűzzük.
        if sub_id in subtitles:
            known = subtitles[sub_id]
            if entry.release and entry.release not in known.release:
                known.release = f"{known.release},{entry.release}".strip(",")
            known.exact_flags = max(known.exact_flags, entry.exact_flags)
        else:
            subtitles[sub_id] = entry
    return list(subtitles.values())


@dataclass
class MovieHit:
    subtitle: Subtitle
    title: str
    year: int | None


def parse_movie_html(html: str) -> list[MovieHit]:
    """A film-kereső találati táblázata (a Bazarr-kiegészítő alapján)."""
    soup = BeautifulSoup(html, "html.parser")
    hits: list[MovieHit] = []
    seen: set[str] = set()
    for row in soup.find_all("tr"):
        link = None
        for anchor in row.find_all("a", href=True):
            href = str(anchor["href"])
            if "action=letolt" in href and "felirat=" in href:
                link = href
        if not link:
            continue
        query = parse_qs(urlparse(link.replace("&amp;", "&")).query)
        sub_id = (query.get("felirat") or [""])[0].strip()
        if not sub_id or sub_id in seen:
            continue
        seen.add(sub_id)
        english = row.find("div", class_="eredeti")
        english_text = english.get_text(" ", strip=True) if english else ""
        language_tag = row.find("small")
        language = language_tag.get_text(strip=True) if language_tag else ""
        year_match = YEAR_RE.search(english_text)
        title = english_text.split(" (")[0].strip()
        release = release_from_name(english_text)
        if year_match and release == year_match.group(1):
            release = ""
        filename = (query.get("fnev") or [""])[0]
        hits.append(
            MovieHit(
                subtitle=Subtitle(
                    id=sub_id,
                    language=language,
                    name=english_text,
                    filename=filename,
                    release=release,
                    season=0,
                    episode=0,
                    season_pack=False,
                    uploader="",
                    exact_flags=0,
                ),
                title=title,
                year=int(year_match.group(1)) if year_match else None,
            )
        )
    return hits


class FeliratokClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
            transport=transport,
        )
        self._limit = asyncio.Semaphore(3)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, params: dict[str, str]) -> httpx.Response:
        async with self._limit:
            response = await self._client.get(self.base_url, params=params)
        response.raise_for_status()
        return response

    async def autoname(self, term: str) -> list[Show]:
        response = await self._get({"action": "autoname", "nyelv": "0", "term": term})
        try:
            return parse_autoname(response.json())
        except ValueError:
            return []

    async def series_subtitles(self, show_id: str, season: int) -> list[Subtitle]:
        response = await self._get(
            {"action": "xbmc", "sid": show_id, "ev": str(season)}
        )
        try:
            return parse_series_results(response.json())
        except ValueError:
            return []

    async def movie_search(self, title: str) -> list[MovieHit]:
        response = await self._get(
            {"search": title, "soriSorszam": "", "nyelv": "", "tab": "film"}
        )
        return parse_movie_html(response.text)

    async def download(self, subtitle_id: str) -> bytes:
        response = await self._get({"action": "letolt", "felirat": subtitle_id})
        return response.content
