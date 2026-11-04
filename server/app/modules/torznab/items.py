import re
from urllib.parse import quote

from app.common.schemas.internal import SeriesInfo
from app.modules.media_attributes.constants import MediaAttributeKey
from app.modules.media_attributes.parser import parse_torrent_name
from app.modules.preferences.constants import PreferenceKey
from app.modules.torrent_source_provider.schemas import TorrentSource
from app.modules.torrent_streams.utils.stream_file_resolver import StreamFileResolver
from app.modules.torznab.constants import (
    INDEXER_PRIVACY,
    TorznabAttrName,
)
from app.modules.torznab.enums import TorznabCategory
from app.modules.torznab.schemas.xml import (
    FeedItem,
    FeedItemEnclosure,
    FeedItemGuid,
    FeedItemIndexer,
    TorznabAttr,
)
from app.modules.torznab.utils import format_date_to_rfc822

# A kliensek kanonikus angol nyelvneveket várnak, nem ISO kódokat.
_LANGUAGE_NAMES = {
    MediaAttributeKey.HUN: "Hungarian",
    MediaAttributeKey.ENG: "English",
}

# A külföldi kategóriás torrent (pl. nCore HD/EN) hangja az eredeti nyelv:
# magyar hangú kiadás csak magyar kategóriába kerülhet.
_ORIGINAL_LANGUAGE = "Original"

# A „HUN.SUB”, „HUNSUB”, „magyar felirat” jelölés feliratot jelent, nem hangot.
_SUBTITLE_TOKENS = {
    "Hungarian": re.compile(
        r"\b(?:hun|hungarian|magyar)[-_. ]?(?:subs?|subbed|felirat(?:os|tal)?)\b",
        re.IGNORECASE,
    ),
    "English": re.compile(
        r"\b(?:eng|english|angol)[-_. ]?(?:subs?|subbed|felirat(?:os|tal)?)\b",
        re.IGNORECASE,
    ),
}


def contains_episode(source: TorrentSource, series: SeriesInfo | None) -> bool:
    """Csak azokat a torrenteket tartjuk meg, amikben tényleg benne van a kért epizód."""
    if series is None:
        return True

    try:
        resolver = StreamFileResolver(source.torrent_file.info, series)
    except Exception:
        return False

    return resolver.resolve() is not None


def _build_magnet(info_hash: str, name: str) -> str:
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"


def _subtitle_names(source: TorrentSource) -> list[str]:
    """A torrent nevében jelölt feliratnyelvek (pl. HUN.SUB)."""
    name = source.torrent_file.info.name
    return [
        language
        for language, pattern in _SUBTITLE_TOKENS.items()
        if pattern.search(name)
    ]


def _language_names(source: TorrentSource) -> list[str]:
    """A hang nyelvei (a kliensek ezt hangként értik, a feliratot külön kapják)."""
    names: list[str] = []

    # A feliratjelölést kivesszük, hogy a „HUN.SUB” ne számítson magyar hangnak.
    audio_name = source.torrent_file.info.name
    for pattern in _SUBTITLE_TOKENS.values():
        audio_name = pattern.sub(" ", audio_name)

    # A torrent nevéből kiolvasott nyelv megbízható.
    for attribute in parse_torrent_name(
        name=audio_name,
        use_fallbacks=False,
    ):
        if attribute.preference_id != PreferenceKey.LANGUAGE:
            continue

        name = _LANGUAGE_NAMES.get(attribute.id)
        if name and name not in names:
            names.append(name)

    # Ha a tracker magyar kategóriájából töltöttük le (pl HD/HUN)
    # akkor feltételezzük, hogy van magyar hang,
    # akkor is, ha véletlen kimaradt a fájlnévből.
    category_ids = {
        attribute.id for attribute in source.indexer_torrent.media_attributes
    }
    if MediaAttributeKey.HUN in category_ids:
        name = _LANGUAGE_NAMES[MediaAttributeKey.HUN]
        if name not in names:
            names.append(name)
    elif MediaAttributeKey.ENG in category_ids and not names:
        # Külföldi kategória, a névben nincs nyelv: biztosan nincs magyar hang,
        # a film eredeti nyelvén szól.
        names.append(_ORIGINAL_LANGUAGE)

    return names


def _attributes(
    source: TorrentSource,
    category: TorznabCategory,
    magnet: str,
) -> list[TorznabAttr]:
    torrent_info = source.torrent_file.info
    indexer_torrent = source.indexer_torrent

    attributes = [
        TorznabAttr(name=TorznabAttrName.CATEGORY, value=str(category)),
        TorznabAttr(name=TorznabAttrName.SIZE, value=str(torrent_info.size)),
        TorznabAttr(
            name=TorznabAttrName.INFOHASH,
            value=torrent_info.info_hash.lower(),
        ),
        TorznabAttr(name=TorznabAttrName.MAGNET_URL, value=magnet),
        TorznabAttr(
            name=TorznabAttrName.SEEDERS,
            value=str(indexer_torrent.seeders),
        ),
    ]

    if indexer_torrent.imdb_id:
        imdb_id = indexer_torrent.imdb_id.removeprefix("tt")
        attributes.append(TorznabAttr(name=TorznabAttrName.IMDB, value=imdb_id))
        attributes.append(
            TorznabAttr(name=TorznabAttrName.IMDB_ID, value=f"tt{imdb_id}")
        )

    attributes.extend(
        TorznabAttr(name=TorznabAttrName.LANGUAGE, value=language)
        for language in _language_names(source)
    )
    attributes.extend(
        TorznabAttr(name=TorznabAttrName.SUBS, value=language)
        for language in _subtitle_names(source)
    )

    return attributes


def build_feed_item(
    source: TorrentSource,
    category: TorznabCategory,
    download_url: str,
) -> FeedItem:
    torrent = source.torrent_file.info
    indexer = source.indexer_torrent.indexer_account.indexer_definition

    info_hash = torrent.info_hash.lower()
    magnet = _build_magnet(info_hash, torrent.name)

    return FeedItem(
        title=torrent.name,
        guid=FeedItemGuid(value=f"{indexer.id}:{source.indexer_torrent.torrent_id}"),
        indexer=FeedItemIndexer(id=indexer.id, name=indexer.name),
        type=INDEXER_PRIVACY,
        pub_date=format_date_to_rfc822(source.torrent_file.created_at),
        size=torrent.size,
        link=download_url,
        categories=[category],
        enclosure=FeedItemEnclosure(url=download_url, length=torrent.size),
        attributes=_attributes(source, category, magnet),
    )
