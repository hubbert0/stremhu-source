"""Címegyeztetés: TMDB-címek és YouTube-videócímek összevetése.

Szándékosan szigorú: inkább ne adjon találatot, mint rossz mesét. Csak a
beállított (hivatalos) csatornák videói között keres.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# Ha ezek valamelyike szerepel a címben, az nem maga a mese.
EXCLUDE_WORDS = {
    "elozetes",
    "trailer",
    "teaser",
    "reszlet",
    "focim",
    "focimdal",
    "intro",
    "promo",
    "kulisszatitkok",
    "werkfilm",
    "interju",
    "forgatas",
    "kepregeny",
    "diafilm",
    "hangoskonyv",
    "hangosmese",
    "shorts",
}
MIN_VIDEO_SECONDS = 120
MIN_MOVIE_SECONDS = 20 * 60

_EPISODE_PATTERNS = (
    re.compile(r"\bs(\d{1,2})\s*e(\d{1,3})\b"),
    re.compile(r"\b(\d{1,2})\s*x\s*(\d{1,3})\b"),
)
_EPISODE_ONLY = (
    re.compile(r"\b(\d{1,3})\s+(?:resz|epizod|fejezet|adas)\b"),
    re.compile(r"\b(?:resz|epizod|ep|e)\s*(\d{1,3})\b"),
)
_PLACEHOLDER = re.compile(r"^(?:\d+\s+)?(?:epizod|resz|episode)(?:\s+\d+)?$")


def norm(text: str) -> str:
    """Kisbetűs, ékezet nélküli, csak betűk és számok szóközzel elválasztva."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^0-9a-z]+", " ", stripped.lower())
    return " ".join(cleaned.split())


def has_phrase(haystack: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def excluded(video_norm: str) -> bool:
    return any(word in EXCLUDE_WORDS for word in video_norm.split())


def title_score(video_norm: str, names: Iterable[str]) -> float:
    """1.0: a cím szó szerint benne van; 0.8: szinte minden szava benne van."""
    best = 0.0
    words = set(video_norm.split())
    for name in names:
        if len(name) < 3:
            continue
        if has_phrase(video_norm, name):
            return 1.0
        tokens = [t for t in name.split() if len(t) > 1]
        if len(tokens) >= 3:
            hit = sum(1 for t in tokens if t in words) / len(tokens)
            if hit >= 0.85:
                best = max(best, 0.8)
    return best


def unique_names(titles: Iterable[str | None]) -> list[str]:
    names: list[str] = []
    for title in titles:
        value = norm(title or "")
        if len(value) >= 3 and value not in names:
            names.append(value)
    return names


def episode_name(name: str | None) -> str:
    """A TMDB-epizódcím normalizálva; a „3. epizód”-féle helyőrző üres."""
    value = norm(name or "")
    if len(value) < 4 or _PLACEHOLDER.match(value):
        return ""
    return value


def episode_numbers(video_norm: str) -> tuple[set[tuple[int, int]], set[int]]:
    """(évad, rész) párok és önálló részszámok a videó címéből."""
    pairs: set[tuple[int, int]] = set()
    singles: set[int] = set()
    for pattern in _EPISODE_PATTERNS:
        for season, episode in pattern.findall(video_norm):
            pairs.add((int(season), int(episode)))
    for pattern in _EPISODE_ONLY:
        for episode in pattern.findall(video_norm):
            singles.add(int(episode))
    return pairs, singles


@dataclass(frozen=True)
class Candidate:
    video_id: str
    score: float
    note: str = ""


def strip_series(video_norm: str, names: list[str]) -> str:
    """A sorozatcím kivétele a videócímből (ami marad, az az epizód címe)."""
    rest = f" {video_norm} "
    for name in names:
        rest = rest.replace(f" {name} ", " ")
    return " ".join(rest.split())
