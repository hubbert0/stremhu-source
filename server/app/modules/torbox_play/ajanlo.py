"""nCore-ajánló katalógus a Nuvio-gyűjteményhez (4 kártya).

Két katalógus (film, sorozat), mindkettő kötelező „genre” választóval:
„Népszerű” (a jobb oldali lista és a „Legaktívabb” borítók) és „Staff ajánló”.
A kötelező választó miatt a Nuvio a kezdőlapon nem mutatja őket külön sorként,
gyűjteménymappába viszont felvehetők (mappa = katalógus + választó).

Egy cím egy csempe. Ha az nCore-adatlapon van IMDb-link, a csempe a rendes
adatlapot nyitja (ott a „nCore-keresés (TorBox)” fül adja a forrásokat);
ha nincs, a keresőével azonos nCore-csempét.
"""

import asyncio
from dataclasses import dataclass, field

from app.common.logger import logger
from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionRecommendedItem,
    IndexerDefinitionSearchHit,
)
from app.modules.stremio.enums import ContentType, ExtraName
from app.modules.stremio.schemas import ManifestCatalog, ManifestExtra, MetaPreview
from app.modules.torbox_play.release import Release, parse_release
from app.modules.torbox_play.service import TorboxPlayService
from app.modules.torbox_play.stremio import (
    MAX_VERSIONS,
    Version,
    meta_id,
    poster_for,
)

CATALOG_ID = "ncore-ajanlo"
CATALOG_NAME = "nCore-ajánló"
GENRE_POPULAR = "Népszerű"
GENRE_STAFF = "Staff ajánló"
MAX_TILES = 40
IMDB_WAIT = 12.0

_GROUPS = {GENRE_POPULAR: ("nepszeru", "aktiv"), GENRE_STAFF: ("staff",)}
_LABELS = {
    "nepszeru": "🔥 Most népszerű az nCore-on",
    "aktiv": "📈 A legaktívabbak között az nCore-on",
    "staff": "⭐ Az nCore staff ajánlja",
}
# Borítós elem minősége → nCore-kategória (a forráslista magyar/eredeti hangjához).
_MOVIE_CATEGORY = {"HD": "hd", "SD": "xvid", "DVD": "dvd", "DVD9": "dvd9"}
_SERIES_CATEGORY = {"HD": "hdser", "SD": "xvidser", "DVD": "dvdser", "DVD9": "dvdser"}


def manifest_catalogs() -> list[ManifestCatalog]:
    return [
        ManifestCatalog(
            id=CATALOG_ID,
            name=CATALOG_NAME,
            type=kind,
            extra=[
                ManifestExtra(
                    name=ExtraName.GENRE,
                    is_required=True,
                    options=[GENRE_POPULAR, GENRE_STAFF],
                )
            ],
        )
        for kind in (ContentType.MOVIE, ContentType.SERIES)
    ]


@dataclass
class _Title:
    release: Release
    score: float
    group: str
    items: list[IndexerDefinitionRecommendedItem] = field(default_factory=list)
    imdb_id: str | None = None

    @property
    def poster(self) -> str | None:
        return next((i.poster for i in self.items if i.poster), None)


def _score(item: IndexerDefinitionRecommendedItem, release: Release) -> float:
    if item.group == "aktiv":
        base = 95 - 4 * item.rank  # 3 borító: a népszerű lista élére kerülnek
    else:
        base = 100 - item.rank
    return base + (3 if release.hungarian_audio else 0)


def _category(item: IndexerDefinitionRecommendedItem) -> str | None:
    table = _MOVIE_CATEGORY if item.section == "film" else _SERIES_CATEGORY
    category = table.get((item.quality or "").upper())
    if category is None or item.hungarian is None:
        return None
    return f"{category}_hun" if item.hungarian else category


async def _imdb_ids(
    service: TorboxPlayService, titles: list[_Title]
) -> list[str | None]:
    """Címenként egy adatlap-lekérés. Ha az első betöltés túl sokáig tartana,
    ami még nincs meg, most nCore-csempe lesz; a lekérés a háttérben
    befejeződik, és a következő betöltéskor már rendes adatlap nyílik."""
    tasks = [
        asyncio.ensure_future(service.imdb_of(t.items[0].torrent_id)) for t in titles
    ]
    if tasks:
        await asyncio.wait(tasks, timeout=IMDB_WAIT)
    return [task.result() if task.done() else None for task in tasks]


def _key(kind: str, release: Release) -> str:
    # Sorozatnál a cím (az összes rész egy csempe), filmnél cím + év.
    if kind == ContentType.SERIES.value:
        return release.key
    return f"{release.key}|{release.year or ''}"


async def catalog(
    service: TorboxPlayService, kind: str, genre: str | None
) -> list[MetaPreview]:
    groups = _GROUPS.get(genre or "")
    if groups is None or not service.enabled:
        return []
    section = "film" if kind == ContentType.MOVIE.value else "sorozat"
    try:
        items = await service.recommended()
    except Exception as error:
        logger.warning("nCore-ajánló sikertelen: %s", error)
        return []

    titles: dict[str, _Title] = {}
    for item in items:
        if item.section != section or item.group not in groups:
            continue
        category = _category(item)
        release = parse_release(item.name, category or "")
        if not release.key:
            continue
        if category is not None:
            service.remember_hit(
                IndexerDefinitionSearchHit(
                    torrent_id=item.torrent_id,
                    name=item.name,
                    category=category,
                    seeders=item.seeders or 0,
                )
            )
        score = _score(item, release)
        title = titles.setdefault(
            _key(kind, release), _Title(release, score, item.group)
        )
        title.items.append(item)
        if score > title.score:
            title.score, title.group = score, item.group

    ranked = sorted(titles.values(), key=lambda t: -t.score)[:MAX_TILES]
    imdb_ids = await _imdb_ids(service, ranked)
    previews: list[MetaPreview] = []
    by_imdb: dict[str, MetaPreview] = {}
    for title, imdb_id in zip(ranked, imdb_ids, strict=True):
        if imdb_id and imdb_id in by_imdb:
            continue  # ugyanaz a cím más névvel (a jobb helyezésű marad)
        versions = [
            Version(
                i.torrent_id, parse_release(i.name, _category(i) or "").hungarian_audio
            )
            for i in title.items[:MAX_VERSIONS]
        ]
        count = len(title.items)
        details = [_LABELS[title.group]]
        if count > 1:
            noun = "rész" if kind == ContentType.SERIES.value else "verzió"
            details.append(f"{count} {noun}")
        if any(v.hungarian for v in versions):
            details.append("🇭🇺 magyar hang")
        name = title.release.title
        if kind == ContentType.MOVIE.value and title.release.year:
            name = f"{name} ({title.release.year})"
        preview = MetaPreview(
            id=imdb_id or meta_id(versions),
            imdb_id=imdb_id,
            type=ContentType(kind),
            name=name,
            poster=title.poster or poster_for(imdb_id),
            description=" · ".join(details),
        )
        if imdb_id:
            by_imdb[imdb_id] = preview
        previews.append(preview)
    return previews
