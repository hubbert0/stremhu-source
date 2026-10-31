import asyncio
import datetime

from app.common.database import isolated_db_session
from app.common.logger import logger
from app.config import config
from app.modules.indexer_definitions.dependencies import (
    get_indexer_definitions_service,
)
from app.modules.torbox_seed.client import TorboxClient
from app.modules.torbox_seed.config import TorboxSeedSettings, get_torbox_seed_settings
from app.modules.torbox_seed.logic import (
    REASON_COOLDOWN,
    REASON_FULFILLED,
    REASON_PUBLIC,
    REASON_SLOT,
    PlannerSettings,
    StateEntry,
    TrackerTorrentRef,
    is_paused,
    plan_actions,
    prune_state,
)
from app.modules.torbox_seed.report import REASON_LABELS, build_status
from app.modules.torbox_seed.storage import (
    STATE_FILE_NAME,
    STATUS_FILE_NAME,
    load_state,
    save_state,
    save_status,
    utc_now,
)
from app.modules.torrent_files.repository import TorrentFilesRepository
from app.modules.torrent_files.schemas import TorrentFileIdentifier

# indexer_id -> (lekérés ideje, nem teljesített torrent-azonosítók)
_hnr_cache: dict[str, tuple[datetime.datetime, set[str]]] = {}


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


async def _fetch_not_completed(
    indexer_ids: set[str],
    now: datetime.datetime,
    cache_ttl: datetime.timedelta,
) -> dict[str, set[str]]:
    """Lekéri a trackerek Hit'n'Run „nem teljesített” listáit (gyorsítótárral).
    Ha egy tracker listája nem érhető el, az kimarad: ott nem nyúlunk semmihez."""
    definitions_service = get_indexer_definitions_service()
    result: dict[str, set[str]] = {}

    for indexer_id in indexer_ids:
        cached = _hnr_cache.get(indexer_id)
        if cached and now - cached[0] < cache_ttl:
            result[indexer_id] = cached[1]
            continue
        try:
            definition = definitions_service.get_by_id(indexer_id)
            ids = {
                str(torrent_id)
                for torrent_id in await definition.find_hit_and_run_ids()
            }
            _hnr_cache[indexer_id] = (now, ids)
            result[indexer_id] = ids
        except Exception:
            logger.warning(
                "⚠️ TorBox: a(z) %s Hit'n'Run listája nem érhető el, "
                "az ehhez tartozó torrentekhez most nem nyúlok.",
                indexer_id,
            )

    return result


def _planner_settings(settings: TorboxSeedSettings) -> PlannerSettings:
    return PlannerSettings(
        slot_limit=settings.slot_limit,
        min_free_slots=settings.min_free_slots,
        max_pause=datetime.timedelta(hours=settings.max_pause_hours),
        min_seed=datetime.timedelta(hours=settings.min_seed_hours),
    )


async def run_torbox_seed_cleanup() -> None:
    settings = get_torbox_seed_settings()
    if not settings.enabled or settings.api_key is None:
        return

    state_path = config.system_dir / STATE_FILE_NAME
    status_path = config.system_dir / STATUS_FILE_NAME
    planner_settings = _planner_settings(settings)
    dry_run = settings.cleanup_dry_run

    try:
        now = utc_now()
        client = TorboxClient(settings.api_key)
        torrents = await client.list_torrents()

        hashes = {torrent.hash for torrent in torrents if torrent.hash}
        refs = await asyncio.to_thread(_find_refs, hashes)
        not_completed = await _fetch_not_completed(
            {ref.indexer_id for ref in refs.values()},
            now,
            datetime.timedelta(minutes=settings.hnr_cache_minutes),
        )

        state = prune_state(
            load_state(state_path), torrents, now, planner_settings.max_pause
        )
        plan = plan_actions(
            torrents=torrents,
            refs_by_hash=refs,
            not_completed_by_indexer=not_completed,
            state=state,
            now=now,
            settings=planner_settings,
        )

        warnings: list[str] = []
        for status in plan.statuses:
            entry = status.entry
            if (
                entry
                and entry.reason in (REASON_FULFILLED, REASON_PUBLIC, REASON_SLOT)
                and is_paused(status.torrent)
                and status.torrent.active
            ):
                warnings.append(
                    "A TorBox a szüneteltetett torrentet is aktívnak jelzi "
                    f"(lehet, hogy foglalja a helyet): {status.torrent.name}"
                )

        for torrent, reason in plan.pause:
            label = f"{torrent.name} – {REASON_LABELS[reason]}"
            if dry_run:
                logger.info("🧪 TorBox (próbaüzem) – szüneteltetném: %s", label)
                continue
            try:
                await client.pause_torrent(torrent.id)
                state[torrent.id] = StateEntry(reason=reason, at=now)
                logger.info("⏸️ TorBox: szüneteltetve: %s", label)
            except Exception:
                logger.exception("❌ TorBox: nem sikerült szüneteltetni: %s", label)

        for torrent, why in plan.resume:
            label = f"{torrent.name} – {why}"
            if dry_run:
                logger.info("🧪 TorBox (próbaüzem) – visszakapcsolnám: %s", label)
                continue
            try:
                await client.resume_torrent(torrent.id)
                state[torrent.id] = StateEntry(reason=REASON_COOLDOWN, at=now)
                logger.info("▶️ TorBox: visszakapcsolva: %s", label)
            except Exception:
                logger.exception("❌ TorBox: nem sikerült visszakapcsolni: %s", label)

        if not dry_run:
            for torrent_id, reason in plan.reclassify.items():
                previous = state.get(torrent_id)
                state[torrent_id] = StateEntry(
                    reason=reason, at=previous.at if previous else now
                )
            save_state(state_path, state)

        for warning in warnings:
            logger.warning("⚠️ TorBox: %s", warning)

        save_status(
            status_path,
            build_status(plan, state, planner_settings, now, dry_run, warnings),
        )

        logger.info(
            "✅ TorBox-ellenőrzés: %d torrent, %d foglalt hely → %d szabad%s; "
            "szüneteltetve: %d, visszakapcsolva: %d.",
            len(torrents),
            plan.occupied_before,
            plan.free_after,
            " (próbaüzem)" if dry_run else "",
            len(plan.pause),
            len(plan.resume),
        )
    except Exception:
        logger.exception("‼️ Hiba történt a TorBox-helykezelés során.")
