"""Az nCore-keresés Stremio/Nuvio-válaszai (katalógus, adatlap, források)."""

import datetime
import functools
from pathlib import Path

from app.common.logger import logger
from app.modules.indexer_definitions.dependencies import (
    get_indexer_definitions_service,
)
from app.modules.stremio.enums import ContentType, ExtraName
from app.modules.stremio.schemas import (
    BehaviorHints,
    ManifestCatalog,
    ManifestExtra,
    MetaDetail,
    MetaPreview,
    MetaVideo,
    StremioStream,
)
from app.modules.torbox_play.service import (
    INDEXER_ID,
    TorboxPlayError,
    TorboxPlayService,
    human_size,
    video_files,
)
from app.modules.torbox_seed.config import get_torbox_seed_settings
from app.modules.users.models import UserModel

PREFIX = "stremhu-tb:"
CATALOG_ID = "ncore-kereses"
CATALOG_NAME = "🔎 nCore-keresés"
WAIT_VIDEO = Path(__file__).parent / "assets" / "varj.mp4"


@functools.lru_cache(maxsize=1)
def get_torbox_play_service() -> TorboxPlayService:
    return TorboxPlayService(
        get_torbox_seed_settings(), get_indexer_definitions_service()
    )


def manifest_catalogs() -> list[ManifestCatalog]:
    return [
        ManifestCatalog(
            id=CATALOG_ID,
            name=CATALOG_NAME,
            type=kind,
            extra=[ManifestExtra(name=ExtraName.SEARCH, is_required=True)],
        )
        for kind in (ContentType.MOVIE, ContentType.SERIES)
    ]


def is_own_id(value: str) -> bool:
    return value.startswith(PREFIX)


def parse_own_id(value: str) -> tuple[str, int | None] | None:
    """stremhu-tb:ncore:<torrent_id>[:<fájlindex>] → (torrent_id, fájlindex)."""
    parts = value.removeprefix(PREFIX).split(":")
    if len(parts) < 2 or parts[0] != INDEXER_ID or not parts[1].isdigit():
        return None
    if len(parts) > 2 and parts[2].isdigit():
        return parts[1], int(parts[2])
    return parts[1], None


def meta_id(torrent_id: str) -> str:
    return f"{PREFIX}{INDEXER_ID}:{torrent_id}"


def poster_for(imdb_id: str | None) -> str | None:
    if not imdb_id:
        return None
    return f"https://images.metahub.space/poster/medium/{imdb_id}/img"


async def catalog(
    service: TorboxPlayService, kind: str, search: str | None
) -> list[MetaPreview]:
    if not search or not service.enabled:
        return []
    try:
        hits = await service.search(search, kind)
    except Exception as error:
        logger.warning("nCore-keresés sikertelen: %s", error)
        return []
    return [
        MetaPreview(
            id=meta_id(hit.torrent_id),
            imdb_id=hit.imdb_id,
            type=ContentType(kind),
            name=hit.name,
            poster=poster_for(hit.imdb_id),
            description=" · ".join(
                part
                for part in (
                    f"💾 {human_size(hit.size)}" if hit.size else "",
                    f"🌱 {hit.seeders} seeder",
                    hit.category,
                )
                if part
            ),
        )
        for hit in hits
    ]


async def meta(service: TorboxPlayService, kind: str, value: str) -> MetaDetail | None:
    parsed = parse_own_id(value)
    if parsed is None:
        return None
    try:
        torrent = await service.torrent(parsed[0])
    except Exception as error:
        logger.warning("nCore-torrent nem olvasható (%s): %s", value, error)
        return None
    files = video_files(torrent.info)
    detail = MetaDetail(
        id=meta_id(torrent.torrent_id),
        imdb_id=torrent.imdb_id,
        type=ContentType(kind),
        name=torrent.info.name,
        poster=poster_for(torrent.imdb_id),
        description=(
            f"nCore · 💾 {human_size(torrent.info.size)} · "
            f"{len(files)} videófájl. A lejátszás a TorBoxon megy."
        ),
    )
    if kind == ContentType.SERIES.value:
        released = datetime.datetime.now(datetime.timezone.utc).isoformat()
        detail.videos = [
            MetaVideo(
                id=f"{detail.id}:{file.index}",
                title=file.name,
                season=file.season,
                episode=file.episode,
                released=released,
                overview=f"💾 {human_size(file.size)}",
            )
            for file in files
        ]
    return detail


def _wait_stream(app_url: str, user: UserModel, title: str, text: str) -> StremioStream:
    return StremioStream(
        name=title,
        description=text,
        url=f"{app_url}/api/{user.api_key}/ncore/varj.mp4",
        behavior_hints=BehaviorHints(not_web_ready=False),
    )


async def streams(
    service: TorboxPlayService, value: str, user: UserModel, app_url: str
) -> list[StremioStream]:
    parsed = parse_own_id(value)
    if parsed is None:
        return []
    torrent_id, file_index = parsed
    try:
        torrent = await service.torrent(torrent_id)
        files = video_files(torrent.info)
        if not files:
            raise TorboxPlayError("Ebben a torrentben nincs videófájl.")
        if file_index is None:
            chosen = max(files, key=lambda f: f.size)
        else:
            chosen = next((f for f in files if f.index == file_index), None)
            if chosen is None:
                raise TorboxPlayError("Nincs ilyen fájl a torrentben.")
        state = await service.ensure_on_torbox(torrent)
    except TorboxPlayError as error:
        return [
            StremioStream(
                name="⚠️ TorBox",
                description=str(error),
                external_url=f"{app_url}/torbox",
            )
        ]
    except Exception as error:
        logger.exception("nCore/TorBox-forrás hiba (%s)", value)
        return [
            StremioStream(
                name="⚠️ TorBox",
                description=f"Váratlan hiba: {type(error).__name__}",
                external_url=f"{app_url}/torbox",
            )
        ]

    if state is None:
        return [
            _wait_stream(
                app_url,
                user,
                "⏳ TorBox – sorban áll",
                "Feltöltve a TorBoxra, de még nem indult el (lehet, hogy nincs "
                "szabad hely). Nyisd meg újra pár perc múlva.",
            )
        ]
    if not state.finished:
        return [
            _wait_stream(
                app_url,
                user,
                f"⏳ TorBox tölti: {state.progress:.0%}",
                f"📁 {chosen.name}\nNyisd meg újra pár perc múlva.",
            )
        ]
    return [
        StremioStream(
            name="▶️ TorBox",
            description=f"📁 {chosen.name}\n💾 {human_size(chosen.size)}",
            url=(
                f"{app_url}/api/{user.api_key}/ncore/play/{torrent_id}/{chosen.index}"
            ),
            behavior_hints=BehaviorHints(
                filename=chosen.name,
                video_size=chosen.size,
                binge_group=f"stremhu-tb-{torrent_id}",
            ),
        )
    ]
