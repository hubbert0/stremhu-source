"""Kiadásnevek (pl. „X-Faktor.S14E11.HUN.WEB-DL.1080p.H264-LEGION”) elemzése.

Két célja van: a különböző verziókat (1080p, 720p, SD) egy címhez sorolni, és
a forráslistában kiírni a felbontást, a forrást, a kódeket, a hangot és a
feliratot.
"""

import re
import unicodedata
from dataclasses import dataclass

_EPISODE = re.compile(
    r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?:[ ._-]?e\d{1,3})*(?![0-9])", re.I
)
_SEASON = re.compile(r"(?<![a-z0-9])s(\d{1,2})(?![0-9e])", re.I)
_YEAR = re.compile(r"(?<![0-9])(19[2-9]\d|20[0-4]\d)(?![0-9])")
# Ahol a cím véget ér: az első minőség-, nyelv- vagy forrásjelzésnél.
_TITLE_END = re.compile(
    r"(?<![a-z0-9])("
    r"2160p|1080p|1080i|720p|576p|480p|4k|uhd|"
    r"hun|eng|ger|multi|dual|"
    r"web[ ._-]?dl|webrip|web|bluray|blu[ ._-]?ray|bdrip|brrip|dvdrip|dvd|hdtv|"
    r"remux|hdrip|xvid|x264|x265|h[ ._]?264|h[ ._]?265|hevc|avc|"
    r"complete|season|evad|proper|repack|extended|uncut|remastered|internal"
    r")(?![a-z0-9])",
    re.I,
)

_RESOLUTIONS = (
    ("4K", re.compile(r"(?<![a-z0-9])(2160p|4k|uhd)(?![a-z0-9])", re.I)),
    ("1080p", re.compile(r"(?<![a-z0-9])1080[pi](?![a-z0-9])", re.I)),
    ("720p", re.compile(r"(?<![a-z0-9])720p(?![a-z0-9])", re.I)),
    ("576p", re.compile(r"(?<![a-z0-9])576p(?![a-z0-9])", re.I)),
    ("480p", re.compile(r"(?<![a-z0-9])480p(?![a-z0-9])", re.I)),
)
_SOURCES = (
    ("REMUX", re.compile(r"(?<![a-z0-9])remux(?![a-z0-9])", re.I)),
    (
        "BluRay",
        re.compile(r"(?<![a-z0-9])(blu[ ._-]?ray|bdrip|brrip)(?![a-z0-9])", re.I),
    ),
    ("WEB-DL", re.compile(r"(?<![a-z0-9])web[ ._-]?dl(?![a-z0-9])", re.I)),
    ("WEBRip", re.compile(r"(?<![a-z0-9])(webrip|web)(?![a-z0-9])", re.I)),
    ("HDTV", re.compile(r"(?<![a-z0-9])hdtv(?![a-z0-9])", re.I)),
    ("DVDRip", re.compile(r"(?<![a-z0-9])(dvdrip|dvd[59]?)(?![a-z0-9])", re.I)),
)
_CODECS = (
    ("H.265", re.compile(r"(?<![a-z0-9])(x265|h[ ._]?265|hevc)(?![a-z0-9])", re.I)),
    ("H.264", re.compile(r"(?<![a-z0-9])(x264|h[ ._]?264|avc)(?![a-z0-9])", re.I)),
    ("XviD", re.compile(r"(?<![a-z0-9])xvid(?![a-z0-9])", re.I)),
)
_HDR = (
    (
        "Dolby Vision",
        re.compile(r"(?<![a-z0-9])(dv|dovi|dolby[ ._-]?vision)(?![a-z0-9])", re.I),
    ),
    ("HDR", re.compile(r"(?<![a-z0-9])hdr(10\+?|10plus)?(?![a-z0-9])", re.I)),
)
_AUDIO = (
    ("Atmos", re.compile(r"(?<![a-z0-9])atmos(?![a-z0-9])", re.I)),
    ("DTS-HD", re.compile(r"(?<![a-z0-9])dts[ ._-]?(hd|ma|x)(?![a-z0-9])", re.I)),
    ("DTS", re.compile(r"(?<![a-z0-9])dts(?![a-z0-9])", re.I)),
    ("TrueHD", re.compile(r"(?<![a-z0-9])truehd(?![a-z0-9])", re.I)),
    ("DD+", re.compile(r"(?<![a-z0-9])(ddp|dd\+|eac3|e-ac-3)", re.I)),
    (
        "DD",
        re.compile(r"(?<![a-z0-9])(dd|ac3)(?:[ ._]?[257]\.?[01])?(?![a-z0-9])", re.I),
    ),
    ("AAC", re.compile(r"(?<![a-z0-9])aac", re.I)),
)
_CHANNELS = re.compile(r"(?<![0-9])([257])[ ._]([01])(?![0-9])")
_HUN_AUDIO = re.compile(r"(?<![a-z0-9])(hun|hungarian|magyar)(?![a-z0-9])", re.I)
_HUN_SUB = re.compile(
    r"(?<![a-z0-9])(hunsub|hun[ ._-]?subs?|magyar[ ._-]?felirat|feliratos)(?![a-z0-9])",
    re.I,
)
_SUBS = re.compile(r"(?<![a-z0-9])(subs?|subbed|multisubs?|felirat)(?![a-z0-9])", re.I)


@dataclass(frozen=True)
class Release:
    title: str  # megjeleníthető cím, pl. „X-Faktor”
    key: str  # összevonási kulcs (ékezet nélkül, kisbetűvel)
    season: int | None
    episode: int | None
    year: int | None
    resolution: str
    source: str
    codec: str
    hdr: str
    audio: str
    hungarian_audio: bool
    hungarian_subtitles: bool
    subtitles: bool

    @property
    def marker(self) -> str:
        """„S14E11”, „S14” vagy az évszám – a cím mellé."""
        if self.season is not None and self.episode is not None:
            return f"S{self.season:02d}E{self.episode:02d}"
        if self.season is not None:
            return f"S{self.season:02d}"
        return str(self.year) if self.year else ""


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", stripped.lower()).split())


def _first(patterns: tuple[tuple[str, re.Pattern[str]], ...], text: str) -> str:
    for label, pattern in patterns:
        if pattern.search(text):
            return label
    return ""


def parse_release(name: str, category: str = "") -> Release:
    spaced = re.sub(r"[._]+", " ", name)
    episode = _EPISODE.search(spaced)
    season = None if episode else _SEASON.search(spaced)
    year = _YEAR.search(spaced)

    cut = len(spaced)
    for match in (episode, season, _TITLE_END.search(spaced)):
        if match:
            cut = min(cut, match.start())
    if year and year.start() > 0:
        cut = min(cut, year.start())
    title = spaced[:cut].strip(" -([")
    if not title:  # pl. ha a név évszámmal kezdődik
        title = spaced.strip()
    title = " ".join(title.split())

    resolution = _first(_RESOLUTIONS, spaced)
    if not resolution:
        resolution = "HD" if category.startswith("hd") else "SD"
    audio = _first(_AUDIO, spaced)
    channels = _CHANNELS.search(spaced)
    if audio and channels:
        audio = f"{audio} {channels.group(1)}.{channels.group(2)}"

    hungarian_subtitles = bool(_HUN_SUB.search(spaced))
    hungarian_audio = category.endswith("_hun") or (
        bool(_HUN_AUDIO.search(spaced)) and not hungarian_subtitles
    )
    return Release(
        title=title,
        key=_norm(title),
        season=int(episode.group(1))
        if episode
        else int(season.group(1))
        if season
        else None,
        episode=int(episode.group(2)) if episode else None,
        year=int(year.group(1)) if year else None,
        resolution=resolution,
        source=_first(_SOURCES, spaced),
        codec=_first(_CODECS, spaced),
        hdr=_first(_HDR, spaced),
        audio=audio,
        hungarian_audio=hungarian_audio,
        hungarian_subtitles=hungarian_subtitles,
        subtitles=hungarian_subtitles or bool(_SUBS.search(spaced)),
    )


RESOLUTION_ORDER = {
    "4K": 0,
    "1080p": 1,
    "720p": 2,
    "HD": 3,
    "576p": 4,
    "480p": 5,
    "SD": 6,
}
