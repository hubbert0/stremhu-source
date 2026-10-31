"""A TorBox-helykezelés döntési logikája – mellékhatás nélkül, külön tesztelhető.

Szabályok:
  1. Ami seedel, de már nincs seedkötelezettsége (a tracker Hit'n'Run-ja
     teljesült, vagy nyilvános torrent), azt szüneteltetjük. Nem töröljük:
     a fájl a TorBoxon marad, és a TorBox a saját lejárati ideje után törli.
  2. Ha kevesebb a szabad hely, mint `min_free_slots`, a legfrissebb, még
     folyamatban lévő tracker-seedet ideiglenesen szüneteltetjük (egyszerre
     legfeljebb `min_free_slots` darabot), a legfrissebbet, mert annak van
     a legtöbb ideje hátra a Hit'n'Run-határidőig.
  3. Az ideiglenesen szüneteltetettet visszakapcsoljuk, ha van elég hely,
     vagy legkésőbb `max_pause` után (a Hit'n'Run elsőbbséget élvez). A
     visszakapcsolt torrent ezután `max_pause` ideig nem szüneteltethető újra.
  4. Ismeretlen privát torrenthez és folyamatban lévő letöltéshez nem nyúlunk.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

# A TorBox állapotszövegei nem mindig egyszavasak (pl. „uploading (no peers)”,
# „stopped seeding”), ezért előtag alapján, kisbetűsen vizsgáljuk őket.
PAUSED_PREFIXES = ("paused",)
INACTIVE_PREFIXES = ("paused", "stopped", "expired")
ACTIVE_PREFIXES = (
    "uploading",
    "seeding",
    "downloading",
    "metadl",
    "checking",
    "stalled",
)

REASON_FULFILLED = "fulfilled"  # Hit'n'Run teljesítve
REASON_PUBLIC = "public"  # nyilvános torrent, nincs seedkötelezettség
REASON_SLOT = "slot"  # ideiglenes, helyfelszabadítás miatt
REASON_COOLDOWN = "cooldown"  # nemrég visszakapcsolt, pihentetjük

HNR_DONE = "teljesítve"
HNR_PENDING = "folyamatban"
HNR_UNKNOWN = "ismeretlen"
HNR_NONE = "nincs"  # nyilvános torrent

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


@dataclass(frozen=True)
class TorboxTorrent:
    id: int
    hash: str
    name: str
    private: bool
    active: bool
    download_finished: bool
    download_state: str
    created_at: datetime.datetime | None


@dataclass(frozen=True)
class TrackerTorrentRef:
    """Melyik tracker melyik torrentje (a StremHU torrent_files táblája alapján)."""

    indexer_id: str
    torrent_id: str


@dataclass(frozen=True)
class StateEntry:
    """Amit mi csináltunk egy torrenttel, és mikor."""

    reason: str
    at: datetime.datetime


@dataclass(frozen=True)
class PlannerSettings:
    slot_limit: int
    min_free_slots: int
    max_pause: datetime.timedelta
    min_seed: datetime.timedelta


@dataclass(frozen=True)
class TorrentStatus:
    torrent: TorboxTorrent
    ref: TrackerTorrentRef | None
    hnr: str
    occupies_slot: bool
    entry: StateEntry | None


@dataclass
class Plan:
    statuses: list[TorrentStatus] = field(default_factory=list)
    pause: list[tuple[TorboxTorrent, str]] = field(default_factory=list)
    resume: list[tuple[TorboxTorrent, str]] = field(default_factory=list)
    reclassify: dict[int, str] = field(default_factory=dict)
    occupied_before: int = 0
    free_after: int = 0


def parse_datetime(value: Any) -> datetime.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def parse_torbox_torrent(item: dict[str, Any]) -> TorboxTorrent:
    return TorboxTorrent(
        id=int(item["id"]),
        hash=str(item.get("hash") or "").lower(),
        name=str(item.get("name") or ""),
        private=bool(item.get("private")),
        active=bool(item.get("active")),
        download_finished=bool(item.get("download_finished")),
        download_state=str(item.get("download_state") or ""),
        created_at=parse_datetime(item.get("created_at")),
    )


def normalized_state(torrent: TorboxTorrent) -> str:
    return torrent.download_state.strip().lower()


def is_paused(torrent: TorboxTorrent) -> bool:
    return normalized_state(torrent).startswith(PAUSED_PREFIXES)


def occupies_slot(torrent: TorboxTorrent) -> bool:
    state = normalized_state(torrent)
    if state.startswith(INACTIVE_PREFIXES):
        return False
    return torrent.active or state.startswith(ACTIVE_PREFIXES)


def hnr_status(
    torrent: TorboxTorrent,
    ref: TrackerTorrentRef | None,
    not_completed_by_indexer: dict[str, set[str]],
    now: datetime.datetime,
    min_seed: datetime.timedelta,
) -> str:
    if ref is None:
        return HNR_UNKNOWN if torrent.private else HNR_NONE

    not_completed = not_completed_by_indexer.get(ref.indexer_id)
    if not_completed is None:
        return HNR_UNKNOWN

    if ref.torrent_id in not_completed:
        return HNR_PENDING

    # A tracker kis késéssel regisztrálja az új letöltést, ezért egy friss
    # torrentet akkor is folyamatban lévőnek veszünk, ha még nincs a listán.
    if torrent.created_at is None or now - torrent.created_at < min_seed:
        return HNR_PENDING

    return HNR_DONE


def prune_state(
    state: dict[int, StateEntry],
    torrents: list[TorboxTorrent],
    now: datetime.datetime,
    max_pause: datetime.timedelta,
) -> dict[int, StateEntry]:
    """Kidobja az elavult bejegyzéseket: eltűnt torrent, lejárt pihentetés,
    vagy amit valaki közben kézzel visszakapcsolt (újra helyet foglal).

    A szüneteltetett torrentet nem a pontos „paused” állapotszöveg alapján
    követjük, hanem az alapján, hogy nem foglal helyet – így akkor sem
    veszítjük el a nyomát, ha a TorBox másképp jelzi (pl. „stopped seeding”)."""
    by_id = {torrent.id: torrent for torrent in torrents}
    pruned: dict[int, StateEntry] = {}

    for torrent_id, entry in state.items():
        torrent = by_id.get(torrent_id)
        if torrent is None:
            continue
        if entry.reason == REASON_COOLDOWN:
            if now - entry.at < max_pause:
                pruned[torrent_id] = entry
            continue
        if not occupies_slot(torrent):
            pruned[torrent_id] = entry

    return pruned


def plan_actions(
    torrents: list[TorboxTorrent],
    refs_by_hash: dict[str, TrackerTorrentRef],
    not_completed_by_indexer: dict[str, set[str]],
    state: dict[int, StateEntry],
    now: datetime.datetime,
    settings: PlannerSettings,
) -> Plan:
    plan = Plan()

    for torrent in torrents:
        ref = refs_by_hash.get(torrent.hash.lower())
        plan.statuses.append(
            TorrentStatus(
                torrent=torrent,
                ref=ref,
                hnr=hnr_status(
                    torrent, ref, not_completed_by_indexer, now, settings.min_seed
                ),
                occupies_slot=occupies_slot(torrent),
                entry=state.get(torrent.id),
            )
        )

    occupied = sum(1 for status in plan.statuses if status.occupies_slot)
    plan.occupied_before = occupied

    # 1. Seedkötelezettség nélküli seedek szüneteltetése (végleges).
    for status in plan.statuses:
        if not (status.torrent.download_finished and status.occupies_slot):
            continue
        if status.hnr == HNR_DONE:
            plan.pause.append((status.torrent, REASON_FULFILLED))
        elif status.hnr == HNR_NONE:
            plan.pause.append((status.torrent, REASON_PUBLIC))
    occupied -= len(plan.pause)

    # 2. Az általunk ideiglenesen szüneteltetettek.
    waiting: list[TorrentStatus] = []
    for status in plan.statuses:
        entry = status.entry
        if entry is None or entry.reason != REASON_SLOT:
            continue
        if status.occupies_slot:
            continue
        if status.hnr == HNR_DONE:
            plan.reclassify[status.torrent.id] = REASON_FULFILLED
            continue
        if now - entry.at >= settings.max_pause:
            plan.resume.append((status.torrent, "időkorlát"))
            occupied += 1
        else:
            waiting.append(status)

    free = settings.slot_limit - occupied

    # 3. Visszakapcsolás, ha utána is marad elég szabad hely.
    resumed_ids = {torrent.id for torrent, _ in plan.resume}
    for status in sorted(waiting, key=lambda s: s.entry.at if s.entry else now):
        if free - 1 >= settings.min_free_slots:
            plan.resume.append((status.torrent, "van szabad hely"))
            resumed_ids.add(status.torrent.id)
            free -= 1
    still_waiting = [s for s in waiting if s.torrent.id not in resumed_ids]

    # 4. Ha kevés a szabad hely: ideiglenes szüneteltetés, a legfrissebbel kezdve.
    if free < settings.min_free_slots:
        pausing_ids = {torrent.id for torrent, _ in plan.pause}
        candidates = [
            status
            for status in plan.statuses
            if status.hnr == HNR_PENDING
            and status.ref is not None
            and status.torrent.download_finished
            and status.occupies_slot
            and status.torrent.id not in pausing_ids
            and not (status.entry and status.entry.reason == REASON_COOLDOWN)
        ]
        candidates.sort(key=lambda s: s.torrent.created_at or _EPOCH, reverse=True)

        need = settings.min_free_slots - free
        cap = settings.min_free_slots - len(still_waiting)
        for status in candidates[: max(0, min(need, cap))]:
            plan.pause.append((status.torrent, REASON_SLOT))
            free += 1

    plan.free_after = free
    return plan
