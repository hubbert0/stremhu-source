"""Cím-egyeztetés és a feliratok sorrendje."""

import re
import unicodedata

from app.feliratok import MovieHit, Show, Subtitle

TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
NOISE = {
    "mkv",
    "mp4",
    "avi",
    "srt",
    "hun",
    "eng",
    "magyar",
    "angol",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "aac",
    "ac3",
    "dd5",
    "ddp5",
    "web",
    "the",
    "a",
    "and",
}
SOURCES = {
    "web-dl",
    "webdl",
    "webrip",
    "bluray",
    "bdrip",
    "brrip",
    "hdtv",
    "dvdrip",
    "remux",
}
RESOLUTIONS = {"2160p", "1080p", "720p", "576p", "480p"}
HUNGARIAN = {"magyar", "hungarian"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def tokens(text: str) -> set[str]:
    return {t for t in TOKEN_SPLIT_RE.split(text.lower()) if t and t not in NOISE}


def release_group(text: str) -> str:
    match = re.search(r"-([A-Za-z0-9]+)(?:\.[a-z0-9]{2,4})?$", text.strip())
    return match.group(1).lower() if match else ""


def is_hungarian(subtitle: Subtitle) -> bool:
    return subtitle.language.strip().lower() in HUNGARIAN


def pick_show(shows: list[Show], title: str, year: int | None) -> Show | None:
    wanted = normalize(title)
    exact = [show for show in shows if normalize(show.title) == wanted]
    if year is not None:
        same_year = [
            show
            for show in exact
            if show.year is not None and abs(show.year - year) <= 1
        ]
        if same_year:
            return same_year[0]
    if exact:
        # Azonos nevű sorozatoknál évszám nélkül a legújabb (legnagyobb azonosítójú) a valószínűbb.
        return max(exact, key=lambda s: int(s.id) if s.id.isdigit() else 0)
    return None


def release_score(subtitle: Subtitle, filename: str) -> int:
    if not filename:
        return 0
    file_tokens = tokens(filename)
    sub_text = f"{subtitle.release} {subtitle.filename}"
    sub_tokens = tokens(sub_text)
    score = 0
    group = release_group(filename)
    if group and group in sub_tokens:
        score += 4
    for token in sub_tokens & file_tokens:
        if token in SOURCES or token in RESOLUTIONS:
            score += 2
        elif len(token) > 2:
            score += 1
    return score


def rank_series(
    subtitles: list[Subtitle], season: int, episode: int, filename: str, limit: int
) -> list[Subtitle]:
    relevant = [
        s
        for s in subtitles
        if is_hungarian(s)
        and (s.season in (season, 0))
        and (s.episode == episode or s.season_pack)
    ]
    relevant.sort(
        key=lambda s: (
            not s.season_pack,
            release_score(s, filename),
            s.exact_flags,
            int(s.id) if s.id.isdigit() else 0,
        ),
        reverse=True,
    )
    return relevant[:limit]


def rank_movies(
    hits: list[MovieHit], title: str, year: int | None, filename: str, limit: int
) -> list[Subtitle]:
    wanted = normalize(title)
    matched = [
        hit
        for hit in hits
        if is_hungarian(hit.subtitle)
        and normalize(hit.title) == wanted
        and (year is None or hit.year is None or abs(hit.year - year) <= 1)
    ]
    matched.sort(
        key=lambda hit: (
            release_score(hit.subtitle, filename),
            int(hit.subtitle.id) if hit.subtitle.id.isdigit() else 0,
        ),
        reverse=True,
    )
    return [hit.subtitle for hit in matched[:limit]]
