"""A YouTube-mesék beállításai környezeti változókból (a .env-ből).

YOUTUBE_API_KEY=...        – YouTube Data API v3 kulcs (Google Cloud)
TMDB_API_KEY=...           – ugyanaz, mint a saját TMDB-addoné
YOUTUBE_CSATORNAK=alap     – vesszővel elválasztott csatornák: UC-vel kezdődő
                             azonosító vagy @név. Az „alap” szó a beépített
                             hivatalos mesecsatornákat jelenti, pl.
                             „alap,@valamelyikcsatorna”.
YOUTUBE_FRISSITES_ORA=24   – ilyen gyakran olvassa újra a csatornákat
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.0.0"

# Hivatalos, a jogtulajdonos által feltöltött magyar mesecsatornák.
DEFAULT_CHANNELS: tuple[str, ...] = (
    "UC5a6bhZOXaYdcHU6esrZCGg",  # Kecskemetfilm Kids – Magyar népmesék, Mátyás, Leo & Fred
    "UC6Ap2GH9CQAO_bVjt9W4VhA",  # KEDD – Magyar népmesék, Boribon, Cigánymesék
    "UCmsxuZrnb-MNYpqPd-XxLog",  # Bogyó és Babóca (hivatalos)
    "UC4jkiQfipNXU6PdIpFtgC4A",  # M2 Mesetévé (MTVA)
    "UCZ8y10ppNbWS47P-ykNMSNw",  # Legendárium – Zete & Rika
    "UC3l7A_Tmg67-d23o6xGGAYw",  # Nemzeti Filmintézet
)


@dataclass(frozen=True)
class Settings:
    base_url: str
    data_dir: Path
    youtube_api_key: str
    tmdb_api_key: str
    channels: tuple[str, ...]
    refresh_hours: float = 24.0
    timeout: float = 15.0


def parse_channels(value: str) -> tuple[str, ...]:
    """A YOUTUBE_CSATORNAK értelmezése. Üresen vagy „alap”-pal a beépített lista."""
    refs: list[str] = []
    parts = [part.strip() for part in value.replace(";", ",").split(",")]
    if not any(parts):
        parts = ["alap"]
    for part in parts:
        if not part:
            continue
        if part.lower() == "alap":
            refs.extend(DEFAULT_CHANNELS)
        elif part.startswith(("UC", "@")):
            refs.append(part)
    return tuple(dict.fromkeys(refs))


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    try:
        refresh = max(1.0, float(env.get("YOUTUBE_FRISSITES_ORA", "24") or 24))
    except ValueError:
        refresh = 24.0
    return Settings(
        base_url=env.get("YOUTUBE_BASE_URL", "http://127.0.0.1:7100").rstrip("/"),
        data_dir=Path(env.get("YOUTUBE_DATA_DIR", "/data")),
        youtube_api_key=env.get("YOUTUBE_API_KEY", "").strip(),
        tmdb_api_key=env.get("TMDB_API_KEY", "").strip(),
        channels=parse_channels(env.get("YOUTUBE_CSATORNAK", "")),
        refresh_hours=refresh,
    )
