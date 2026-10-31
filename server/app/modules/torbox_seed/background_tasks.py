import asyncio
import datetime

from app.common.database import isolated_db_session
from app.common.logger import logger
from app.modules.indexer_definitions.dependencies import (
    get_indexer_definitions_service,
)
from app.modules.torbox_seed.client import TorboxClient
from app.modules.torbox_seed.config import get_torbox_seed_settings
from app.modules.torbox_seed.logic import (
    TrackerTorrentRef,
    occupies_slot,
    select_for_cleanup,
)
from app.modules.torrent_files.repository import TorrentFilesRepository
from app.modules.torrent_files.schemas import TorrentFileIdentifier


def _find_refs(info_hashes: set[str]) -> dict[str, TrackerTorrentRef]:
    """A TorBox-hashekhez megkeresi a tracker-azonosítókat, és frissíti a
    használati időt, hogy a gyorsítótár-takarítás ne törölje őket, amíg a
    torrent a TorBoxon van."""
    refs: dict[str, TrackerTorrentRef] = {}

    with isolated_db_session() as db:
        repository = TorrentFilesRepository(db)
        for info_hash in info_hashes:
            torrent_file = repository.find_by_info_hash(info_hash)
            if torrent_file is None:
                continue
            refs[info_hash] = TrackerTorrentRef(
                indexer_id=torrent_file.indexer_id,
                torrent_id=torrent_file.torrent_id,
            )

        if refs:
            repository.touch(
                [
                    TorrentFileIdentifier(
                        indexer_id=ref.indexer_id,
                        torrent_id=ref.torrent_id,
                    )
                    for ref in refs.values()
                ]
            )

    return refs


async def _fetch_not_completed(indexer_ids: set[str]) -> dict[str, set[str]]:
    """Lekéri a trackerek Hit'n'Run „nem teljesített” listáit.
    Ha egy tracker listája nem érhető el, az kimarad (annál nem törlünk)."""
    definitions_service = get_indexer_definitions_service()
    result: dict[str, set[str]] = {}

    for indexer_id in indexer_ids:
        try:
            definition = definitions_service.get_by_id(indexer_id)
            ids = await definition.find_hit_and_run_ids()
            result[indexer_id] = {str(torrent_id) for torrent_id in ids}
        except Exception:
            logger.warning(
                "⚠️ TorBox-takarítás: a(z) %s Hit'n'Run listája nem érhető el, "
                "ennél a trackernél most nem törlök.",
                indexer_id,
            )

    return result


async def run_torbox_seed_cleanup() -> None:
    settings = get_torbox_seed_settings()
    if not settings.enabled or settings.api_key is None:
        return

    try:
        client = TorboxClient(settings.api_key)
        torrents = await client.list_torrents()

        hashes = {torrent.hash for torrent in torrents if torrent.hash}
        refs = await asyncio.to_thread(_find_refs, hashes)
        not_completed = await _fetch_not_completed(
            {ref.indexer_id for ref in refs.values()}
        )

        decisions = select_for_cleanup(
            torrents=torrents,
            refs_by_hash=refs,
            not_completed_by_indexer=not_completed,
            now=datetime.datetime.now(datetime.timezone.utc),
            min_seed=datetime.timedelta(hours=settings.min_seed_hours),
        )

        deleted = 0
        for decision in decisions:
            label = (
                f"{decision.torrent.name} "
                f"({decision.ref.indexer_id} #{decision.ref.torrent_id})"
            )
            if settings.cleanup_dry_run:
                logger.info("🧪 TorBox-takarítás (próbaüzem) – törölném: %s", label)
                continue
            try:
                await client.delete_torrent(decision.torrent.id)
                deleted += 1
                logger.info(
                    "🧹 TorBox-takarítás: törölve (Hit'n'Run teljesítve): %s", label
                )
            except Exception:
                logger.exception("❌ TorBox-takarítás: nem sikerült törölni: %s", label)

        slots_before = sum(1 for torrent in torrents if occupies_slot(torrent))
        logger.info(
            "✅ TorBox-takarítás kész: %d torrent a TorBoxon, %d foglal helyet, "
            "%d tracker-torrent felismerve, %d törölve%s.",
            len(torrents),
            slots_before,
            len(refs),
            deleted if not settings.cleanup_dry_run else len(decisions),
            " (próbaüzem)" if settings.cleanup_dry_run else "",
        )
    except Exception:
        logger.exception("‼️ Hiba történt a TorBox-takarítás során.")
