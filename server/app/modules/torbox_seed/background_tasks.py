import asyncio
import datetime

from fastapi import HTTPException

from app.common.database import db_session, isolated_db_session
from app.common.logger import logger
from app.config import config
from app.modules.indexer_definitions.dependencies import (
    get_indexer_definitions_service,
)
from app.modules.torbox_seed.alerts import due_alerts, mark_sent, send_ntfy
from app.modules.torbox_seed.client import TorboxClient
from app.modules.torbox_seed.config import TorboxSeedSettings, get_torbox_seed_settings
from app.modules.torbox_seed.logic import (
    HnrList,
    PlannerSettings,
    StateEntry,
    TorboxTorrent,
    TrackerTorrentRef,
    plan_actions,
    prune_state,
)
from app.modules.torbox_seed.report import REASON_LABELS, build_status
from app.modules.torbox_seed.storage import (
    ALERTS_FILE_NAME,
    STATE_FILE_NAME,
    STATUS_FILE_NAME,
    load_alert_records,
    load_state,
    save_alert_records,
    save_state,
    save_status,
    utc_now,
)
from app.modules.torrent_files.models import TorrentFileModel
from app.modules.torrent_files.repository import TorrentFilesRepository
from app.modules.torrent_files.schemas import TorrentFileIdentifier
from app.modules.torrent_source_provider.dependencies import (
    create_torrent_source_provider_service,
)

# indexer_id -> (lekérés ideje, nem teljesített torrent-azonosítók)
_hnr_cache: dict[str, tuple[datetime.datetime, set[str]]] = {}

# (indexer_id, torrent_id) -> mikor derült ki, hogy a tracker oldalán nincs ilyen
# torrent (törölték). Ezeket egy napig nem próbáljuk újra azonosítani.
_absent_ids: dict[tuple[str, str], datetime.datetime] = {}
_ABSENT_TTL = datetime.timedelta(hours=24)

# Ha egy általunk leállított torrent ennyi idő után is helyet foglal, szólunk.
_STOP_GRACE = datetime.timedelta(minutes=15)

_RefMaps = tuple[dict[str, TrackerTorrentRef], dict[str, TrackerTorrentRef]]


def _normalize_name(name: str) -> str:
    return name.strip().lower()


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


async def _fetch_hnr_ids(
    indexer_ids: tuple[str, ...],
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
                "az ehhez tartozó és az ismeretlen torrentekhez most nem nyúlok.",
                indexer_id,
            )

    return result


def _add_to_maps(
    maps: _RefMaps, torrent_file: TorrentFileModel, ref: TrackerTorrentRef
) -> None:
    by_hash, by_name = maps
    by_hash[torrent_file.info_hash.lower()] = ref
    try:
        by_name[_normalize_name(torrent_file.info.name)] = ref
    except Exception:
        pass


def _lookup_known(indexer_id: str, ids: set[str]) -> tuple[_RefMaps, list[str]]:
    """A Hit'n'Run-lista azon elemei, amelyek .torrent fájlja már megvan."""
    maps: _RefMaps = ({}, {})
    missing: list[str] = []
    found: list[TorrentFileIdentifier] = []

    with isolated_db_session() as db:
        repository = TorrentFilesRepository(db)
        for torrent_id in sorted(ids):
            torrent_file = repository.find_by_id(indexer_id, torrent_id)
            if torrent_file is None:
                missing.append(torrent_id)
                continue
            _add_to_maps(maps, torrent_file, TrackerTorrentRef(indexer_id, torrent_id))
            found.append(
                TorrentFileIdentifier(indexer_id=indexer_id, torrent_id=torrent_id)
            )
        if found:
            repository.touch(found)

    return maps, missing


async def _resolve_hnr_ids(
    indexer_id: str,
    ids: set[str],
    limit: int,
    now: datetime.datetime,
) -> tuple[_RefMaps, int]:
    """A Hit'n'Run-lista elemeihez kikeresi a torrent hash-ét és nevét.

    Ami még nincs meg, annak a .torrent fájlját letölti (ellenőrzésenként
    legfeljebb `limit` darabot). Visszaadja a talált hozzárendeléseket, és hogy
    hány elemet nem sikerült még azonosítani."""
    maps, missing = await asyncio.to_thread(_lookup_known, indexer_id, ids)

    for key, at in list(_absent_ids.items()):
        if now - at >= _ABSENT_TTL:
            _absent_ids.pop(key, None)
    missing = [
        torrent_id
        for torrent_id in missing
        if (indexer_id, torrent_id) not in _absent_ids
    ]

    unresolved = 0
    if missing:
        with db_session() as db:
            provider = create_torrent_source_provider_service(db)
            for index, torrent_id in enumerate(missing):
                if index >= limit:
                    unresolved += len(missing) - index
                    break
                try:
                    source = await provider.find_one_by_indexer(indexer_id, torrent_id)
                except HTTPException as error:
                    if error.status_code == 404:
                        _absent_ids[(indexer_id, torrent_id)] = now
                    else:
                        unresolved += 1
                    continue
                except Exception:
                    logger.warning(
                        "⚠️ TorBox: a(z) %s #%s torrentet nem sikerült azonosítani.",
                        indexer_id,
                        torrent_id,
                    )
                    unresolved += 1
                    continue
                if source is None:
                    _absent_ids[(indexer_id, torrent_id)] = now
                    continue
                _add_to_maps(
                    maps, source.torrent_file, TrackerTorrentRef(indexer_id, torrent_id)
                )

    return maps, unresolved


def _match_refs(
    torrents: list[TorboxTorrent],
    refs: dict[str, TrackerTorrentRef],
    maps: _RefMaps,
) -> None:
    by_hash, by_name = maps
    for torrent in torrents:
        if torrent.hash in refs:
            continue
        ref = by_hash.get(torrent.hash) or by_name.get(_normalize_name(torrent.name))
        if ref is not None:
            refs[torrent.hash] = ref


def _planner_settings(settings: TorboxSeedSettings) -> PlannerSettings:
    return PlannerSettings(
        slot_limit=settings.slot_limit,
        min_free_slots=settings.min_free_slots,
        min_seed=datetime.timedelta(hours=settings.min_seed_hours),
        seed_trackers=settings.seed_tracker_list,
    )


async def run_torbox_seed_cleanup() -> None:
    settings = get_torbox_seed_settings()
    if not settings.enabled or settings.api_key is None:
        return

    state_path = config.system_dir / STATE_FILE_NAME
    status_path = config.system_dir / STATUS_FILE_NAME
    alerts_path = config.system_dir / ALERTS_FILE_NAME
    planner_settings = _planner_settings(settings)
    dry_run = settings.cleanup_dry_run

    try:
        now = utc_now()
        client = TorboxClient(settings.api_key)
        torrents = await client.list_torrents()

        hashes = {torrent.hash for torrent in torrents if torrent.hash}
        refs = await asyncio.to_thread(_find_refs, hashes)

        hnr_ids = await _fetch_hnr_ids(
            planner_settings.seed_trackers,
            now,
            datetime.timedelta(minutes=settings.hnr_cache_minutes),
        )
        hnr_lists: dict[str, HnrList] = {}
        for indexer_id, ids in hnr_ids.items():
            maps, unresolved = await _resolve_hnr_ids(
                indexer_id, ids, settings.hnr_resolve_limit, now
            )
            _match_refs(torrents, refs, maps)
            hnr_lists[indexer_id] = HnrList(
                pending_ids=frozenset(ids), complete=unresolved == 0
            )
            if unresolved:
                logger.info(
                    "ℹ️ TorBox: a(z) %s Hit'n'Run-listájából még %d torrent "
                    "azonosítatlan; az ismeretlen torrentekhez addig nem nyúlok.",
                    indexer_id,
                    unresolved,
                )

        state = prune_state(load_state(state_path), torrents)
        plan = plan_actions(
            torrents=torrents,
            refs_by_hash=refs,
            hnr_lists=hnr_lists,
            state=state,
            now=now,
            settings=planner_settings,
        )

        warnings: list[str] = []
        stopped_now: set[int] = set()
        for torrent, reason in plan.stop:
            label = f"{torrent.name} – {REASON_LABELS[reason]}"
            if dry_run:
                logger.info(
                    "🧪 TorBox (próbaüzem) – leállítanám a seedelést: %s", label
                )
                continue
            try:
                await client.stop_seeding(torrent.id)
                stopped_now.add(torrent.id)
                if torrent.id not in state:
                    state[torrent.id] = StateEntry(reason=reason, at=now)
                logger.info("⏹️ TorBox: seedelés leállítva: %s", label)
            except Exception as error:
                logger.warning(
                    "❌ TorBox: nem sikerült leállítani: %s (%s)", label, error
                )
                warnings.append(
                    f"Nem sikerült leállítani a seedelést: {torrent.name} ({error})"
                )

        for status in plan.statuses:
            entry = status.entry
            if (
                entry is not None
                and status.occupies_slot
                and now - entry.at >= _STOP_GRACE
                and status.torrent.id in stopped_now
            ):
                warnings.append(
                    "A TorBox a leállított torrentet még mindig aktívnak jelzi "
                    f"(foglalja a helyet): {status.torrent.name}"
                )

        if not dry_run:
            save_state(state_path, state)

        records = load_alert_records(alerts_path)
        due, records = due_alerts(
            plan.alerts,
            records,
            now,
            datetime.timedelta(hours=settings.alert_repeat_hours),
        )
        topic = (settings.ntfy_topic or "").strip()
        if due and topic:
            if await send_ntfy(settings.ntfy_url, topic, due):
                records = mark_sent(records, due, now)
                logger.info(
                    "📣 TorBox: értesítés elküldve (%d figyelmeztetés).", len(due)
                )
        save_alert_records(alerts_path, records)

        for alert in plan.alerts:
            logger.warning("⚠️ TorBox: %s", alert.message)
        for warning in warnings:
            logger.warning("⚠️ TorBox: %s", warning)

        save_status(
            status_path,
            build_status(
                plan,
                state,
                planner_settings,
                now,
                dry_run,
                [alert.message for alert in plan.alerts] + warnings,
            ),
        )

        logger.info(
            "✅ TorBox-ellenőrzés: %d torrent, %d foglalt hely → %d szabad%s; "
            "seed leállítva: %d.",
            len(torrents),
            plan.occupied_before,
            plan.free_after,
            " (próbaüzem)" if dry_run else "",
            len(plan.stop),
        )
    except Exception:
        logger.exception("‼️ Hiba történt a TorBox-seedkezelés során.")
