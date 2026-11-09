"""Az nCore-keresés Stremio/Nuvio-válaszai (katalógus, adatlap, források).

Egy cím egy csempe: a keresés találatait (1080p, 720p, SD… verziók) címre és
epizódra/évre vonjuk össze, és a forráslistában verziónként írjuk ki a
felbontást, a forrást, a hangot, a feliratot, a méretet és a TorBox-állapotot.
A TorBoxra csak a lejátszásra kiválasztott verzió kerül fel.

Azonosítók:
- adatlap: ``stremhu-tb:ncore:<v1>_<v2>_…``, ahol egy verzió ``<torrent_id>``,
  magyar kategóriánál ``<torrent_id>h`` (így újraindítás után is tudjuk);
- epizód: ``<adatlap>:<évad>:<epizód>``;
- régi forma (még elfogadjuk): ``stremhu-tb:ncore:<torrent_id>[:<fájlindex>]``.
"""

import functools
import re
from dataclasses import dataclass
from pathlib import Path

from app.common.logger import logger
from app.modules.indexer_definitions.dependencies import (
    get_indexer_definitions_service,
)
from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionSearchHit,
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
from app.modules.torbox_play.release import (
    RESOLUTION_ORDER,
    Release,
    parse_release,
)
from app.modules.torbox_play.service import (
    INDEXER_ID,
    CachedTorrent,
    TorboxPlayError,
    TorboxPlayService,
    TorboxState,
    VideoFile,
    human_size,
    video_files,
)
from app.modules.torbox_seed.config import get_torbox_seed_settings
from app.modules.users.models import UserModel

PREFIX = "stremhu-tb:"
CATALOG_ID = "ncore-kereses"
CATALOG_NAME = "🔎 nCore-keresés"
WAIT_VIDEO = Path(__file__).parent / "assets" / "varj.mp4"
MAX_VERSIONS = 8

_VERSION = re.compile(r"^(\d+)(h?)$")


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


@dataclass(frozen=True)
class Version:
    torrent_id: str
    hungarian: bool  # magyar kategória (…_hun)


@dataclass(frozen=True)
class OwnId:
    versions: list[Version]
    season: int | None = None
    episode: int | None = None
    file_index: int | None = None  # csak a régi formában

    @property
    def meta_id(self) -> str:
        return meta_id(self.versions)


def meta_id(versions: list[Version]) -> str:
    tokens = "_".join(f"{v.torrent_id}{'h' if v.hungarian else ''}" for v in versions)
    return f"{PREFIX}{INDEXER_ID}:{tokens}"


def parse_own_id(value: str) -> OwnId | None:
    parts = value.removeprefix(PREFIX).split(":")
    if len(parts) < 2 or parts[0] != INDEXER_ID:
        return None
    versions: list[Version] = []
    for token in parts[1].split("_"):
        match = _VERSION.match(token)
        if not match:
            return None
        versions.append(Version(match.group(1), bool(match.group(2))))
    if not versions:
        return None
    rest = parts[2:]
    if len(rest) == 2 and all(p.isdigit() for p in rest):
        return OwnId(versions, season=int(rest[0]), episode=int(rest[1]))
    if len(rest) == 1 and rest[0].isdigit():
        return OwnId(versions, file_index=int(rest[0]))
    return OwnId(versions)


def poster_for(imdb_id: str | None) -> str | None:
    if not imdb_id:
        return None
    return f"https://images.metahub.space/poster/medium/{imdb_id}/img"


def _category(service: TorboxPlayService, version: Version) -> str:
    hit = service.hit(version.torrent_id)
    if hit is not None:
        return hit.category
    return "hd_hun" if version.hungarian else ""


# ---------------------------------------------------------------- katalógus
@dataclass
class _Group:
    release: Release
    hits: list[tuple[IndexerDefinitionSearchHit, Release]]


def _group_key(kind: str, hit: IndexerDefinitionSearchHit, release: Release) -> str:
    if kind == ContentType.MOVIE.value:
        return hit.imdb_id or f"{release.key}|{release.year or ''}"
    return f"{hit.imdb_id or release.key}|{release.marker}"


def _summary(hits: list[tuple[IndexerDefinitionSearchHit, Release]]) -> str:
    resolutions = sorted(
        {release.resolution for _, release in hits},
        key=lambda r: RESOLUTION_ORDER.get(r, 99),
    )
    count = len(hits)
    parts = [
        f"{count} verzió: {', '.join(resolutions)}"
        if count > 1
        else f"1 verzió: {resolutions[0]}"
    ]
    hungarian = [release.hungarian_audio for _, release in hits]
    if all(hungarian):
        parts.append("🇭🇺 magyar hang")
    elif any(hungarian):
        parts.append("🇭🇺 magyar és eredeti hang")
    else:
        parts.append("eredeti hang")
    if any(release.hungarian_subtitles for _, release in hits):
        parts.append("💬 magyar felirat")
    parts.append(f"🌱 max. {max(hit.seeders for hit, _ in hits)} seeder")
    return " · ".join(parts)


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

    groups: dict[str, _Group] = {}
    for hit in hits:
        release = parse_release(hit.name, hit.category)
        key = _group_key(kind, hit, release)
        group = groups.setdefault(key, _Group(release, []))
        group.hits.append((hit, release))

    previews: list[MetaPreview] = []
    for group in groups.values():
        chosen = sorted(group.hits, key=lambda item: -item[0].seeders)[:MAX_VERSIONS]
        versions = [
            Version(hit.torrent_id, hit.category.endswith("_hun")) for hit, _ in chosen
        ]
        imdb_id = next((hit.imdb_id for hit, _ in chosen if hit.imdb_id), None)
        name = " ".join(p for p in (group.release.title, group.release.marker) if p)
        previews.append(
            MetaPreview(
                id=meta_id(versions),
                imdb_id=imdb_id,
                type=ContentType(kind),
                name=name,
                poster=poster_for(imdb_id),
                description=_summary(chosen),
            )
        )
    return previews


# ---------------------------------------------------------------- adatlap
def _release_of(
    service: TorboxPlayService, version: Version, torrent: CachedTorrent | None
) -> Release | None:
    hit = service.hit(version.torrent_id)
    name = hit.name if hit is not None else torrent.info.name if torrent else None
    if not name:
        return None
    return parse_release(name, _category(service, version))


async def meta(service: TorboxPlayService, kind: str, value: str) -> MetaDetail | None:
    own = parse_own_id(value)
    if own is None:
        return None
    releases = [_release_of(service, v, None) for v in own.versions]
    single_episodes = all(r is not None and r.episode is not None for r in releases)
    torrents: dict[str, CachedTorrent] = {}
    # Filmnél és egyepizódos kiadásoknál a keresés adata elég; évadcsomagnál
    # (vagy újraindítás után) a .torrent fájllistájából dolgozunk.
    if (kind == ContentType.SERIES.value and not single_episodes) or None in releases:
        found = await service.torrents([v.torrent_id for v in own.versions])
        torrents = {t.torrent_id: t for t in found}
        if not torrents:
            logger.warning("nCore-torrent nem olvasható (%s)", value)
            return None
        releases = [
            _release_of(service, v, torrents.get(v.torrent_id)) for v in own.versions
        ]
    known = [r for r in releases if r is not None]
    if not known:
        return None
    first = known[0]
    imdb_id = next(
        (
            hit.imdb_id
            for v in own.versions
            if (hit := service.hit(v.torrent_id)) and hit.imdb_id
        ),
        next((t.imdb_id for t in torrents.values() if t.imdb_id), None),
    )
    resolutions = sorted(
        {r.resolution for r in known}, key=lambda r: RESOLUTION_ORDER.get(r, 99)
    )
    detail = MetaDetail(
        id=own.meta_id,
        imdb_id=imdb_id,
        type=ContentType(kind),
        name=" ".join(p for p in (first.title, first.marker) if p),
        poster=poster_for(imdb_id),
        description=(
            f"nCore · {len(own.versions)} verzió ({', '.join(resolutions)}). "
            "A forráslistában választhatsz; a TorBoxra csak az kerül fel, "
            "amelyiket elindítod."
        ),
    )
    if kind != ContentType.SERIES.value:
        return detail

    episodes: set[tuple[int, int]] = set()
    if single_episodes and not torrents:
        for release in known:
            assert release.season is not None and release.episode is not None
            episodes.add((release.season, release.episode))
    else:
        for torrent in torrents.values():
            episodes.update((f.season, f.episode) for f in video_files(torrent.info))
    # Megjelenési dátumot nem adunk: a Nuvio a jövőbeli (vagy a telefon órájánál
    # pár másodperccel későbbi) dátumú részt „UNAVAILABLE”-nek jelöli.
    detail.videos = [
        MetaVideo(
            id=f"{own.meta_id}:{season}:{episode}",
            title=f"{first.title} S{season:02d}E{episode:02d}",
            season=season,
            episode=episode,
            available=True,
        )
        for season, episode in sorted(episodes)
    ]
    return detail


# ---------------------------------------------------------------- források
def _pick_file(
    own: OwnId, torrent: CachedTorrent, release: Release
) -> VideoFile | None:
    files = video_files(torrent.info)
    if not files:
        return None
    if own.file_index is not None:
        return next((f for f in files if f.index == own.file_index), None)
    if own.season is None or own.episode is None:
        return max(files, key=lambda f: f.size)
    match = next(
        (f for f in files if (f.season, f.episode) == (own.season, own.episode)), None
    )
    if match is None and len(files) == 1 and release.episode == own.episode:
        match = files[0]
    return match


def _status(state: TorboxState | None, known: bool) -> tuple[str, str, int]:
    """(ikon, leírás, sorrend) a TorBox-állapotból."""
    if not known:
        return "❔", "❔ A TorBox állapota most nem kérdezhető le", 2
    if state is None:
        return "⬇️", "⬇️ Elindításkor feltölti a TorBoxra (pár perc)", 2
    if state.finished:
        return "▶️", "▶️ Kész a TorBoxon – azonnal indul", 0
    return (
        f"⏳ {state.progress:.0%}",
        f"⏳ A TorBox tölti: {state.progress:.0%}",
        1,
    )


def _description(
    release: Release,
    file: VideoFile,
    torrent: CachedTorrent,
    seeders: int | None,
    status_line: str,
) -> str:
    language = "🗣️ magyar hang" if release.hungarian_audio else "🗣️ eredeti hang"
    if release.audio:
        language += f" · {release.audio}"
    lines = [language]
    if release.hungarian_subtitles:
        lines.append("💬 magyar felirat")
    elif release.subtitles:
        lines.append("💬 felirat (a kiadás neve szerint)")
    video = " · ".join(p for p in (release.source, release.codec, release.hdr) if p)
    if video:
        lines.append(f"🎞️ {video}")
    size = f"💾 {human_size(file.size)}"
    if seeders is not None:
        size += f" · 🌱 {seeders}"
    lines.append(size)
    lines.append(status_line)
    lines.append(f"📦 {torrent.info.name}")
    return "\n".join(lines)


async def streams(
    service: TorboxPlayService, value: str, user: UserModel, app_url: str
) -> list[StremioStream]:
    own = parse_own_id(value)
    if own is None:
        return []
    try:
        found = await service.torrents([v.torrent_id for v in own.versions])
        if not found:
            raise TorboxPlayError("Ez a torrent már nincs fent az nCore-on.")
        try:
            states = await service.torbox_states()
            states_known = True
        except TorboxPlayError as error:
            logger.warning("TorBox-állapot nem kérdezhető le: %s", error)
            states, states_known = {}, False
    except TorboxPlayError as error:
        return [_error_stream(app_url, str(error))]
    except Exception as error:
        logger.exception("nCore/TorBox-forrás hiba (%s)", value)
        return [_error_stream(app_url, f"Váratlan hiba: {type(error).__name__}")]

    by_id = {t.torrent_id: t for t in found}
    rows: list[tuple[tuple[int, int, int], StremioStream]] = []
    for version in own.versions:
        torrent = by_id.get(version.torrent_id)
        if torrent is None:
            continue
        release = _release_of(service, version, torrent)
        if release is None:
            continue
        file = _pick_file(own, torrent, release)
        if file is None:
            continue
        state = states.get(torrent.info.info_hash.lower())
        icon, status_line, order = _status(state, states_known)
        hit = service.hit(version.torrent_id)
        seeders = hit.seeders if hit is not None else None
        stream = StremioStream(
            name=f"nCore {icon}\n{release.resolution}",
            description=_description(release, file, torrent, seeders, status_line),
            url=(
                f"{app_url}/api/{user.api_key}/ncore/play/"
                f"{version.torrent_id}/{file.index}"
            ),
            behavior_hints=BehaviorHints(
                filename=file.name,
                video_size=file.size,
                binge_group=(
                    f"stremhu-tb-{release.resolution}-"
                    f"{'hu' if release.hungarian_audio else 'orig'}"
                ),
            ),
        )
        sort_key = (
            order,
            RESOLUTION_ORDER.get(release.resolution, 99),
            -(seeders or 0),
        )
        rows.append((sort_key, stream))

    if not rows:
        return [_error_stream(app_url, "Ehhez nem találtam videófájlt a torrentekben.")]
    rows.sort(key=lambda row: row[0])
    return [stream for _, stream in rows]


def _error_stream(app_url: str, text: str) -> StremioStream:
    return StremioStream(
        name="⚠️ TorBox",
        description=text,
        external_url=f"{app_url}/torbox",
    )
