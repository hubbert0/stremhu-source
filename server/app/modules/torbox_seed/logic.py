"""A TorBox-seedkezelés döntési logikája – mellékhatás nélkül, külön tesztelhető.

Szabályok:
  1. Seedelni csak a „seedköteles” trackerek (alapból: nCore) torrentjeit kell,
     amíg a tracker Hit'n'Run-listáján szerepelnek, illetve az első `min_seed`
     órában (a tracker késve veszi fel őket a listára).
  2. Minden más letöltött, seedelő torrent seedelését leállítjuk: a nem
     seedköteles trackerekét (pl. nyilvános torrentek), és a seedkötelesekét
     is, ha a Hit'n'Run teljesült. Nem törlünk: a fájl a TorBoxon marad, és
     továbbra is lejátszható.
  3. Seedkötelezett torrent seedelését csak vészhelyzetben állítjuk le: ha a
     fentiek után is kevesebb a szabad hely, mint `min_free_slots`. Ilyenkor
     annak a seedelését, amelyiknek a legkevesebb ideje van hátra (ismeretlen
     hátralévő időnél a legrégebbiét), és azonnal szólunk, hogy máshol (pl. a
     laptopon) kell befejezni – a TorBox egy leállított torrentet nem tud
     újraindítani (kipróbálva).
  4. Ha egy seedkötelezett, már letöltött torrent nem seedel (a TorBox
     leállította, vagy elakadt), figyelmeztetünk – erről értesítés is megy.
  5. Óvatosság: folyamatban lévő letöltéshez nem nyúlunk. Ha a seedköteles
     tracker Hit'n'Run-listája nem érhető el, vagy még nincs minden eleme
     azonosítva, az ismeretlen eredetű torrentekhez sem nyúlunk. Egy ismeretlen
     privát torrentet az első `min_seed` órában nem állítunk le.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

# A TorBox állapotszövegei nem mindig egyszavasak (pl. „uploading (no peers)”,
# „stopped seeding”), ezért előtag alapján, kisbetűsen vizsgáljuk őket.
INACTIVE_PREFIXES = ("paused", "stopped", "expired")
SEEDING_PREFIXES = ("uploading", "seeding")

REASON_FULFILLED = "fulfilled"  # Hit'n'Run teljesítve
REASON_NOT_REQUIRED = "not_required"  # nem seedköteles trackerről jött
REASON_EMERGENCY = "emergency"  # helyhiány: máshol kell befejezni
STOP_REASONS = (REASON_FULFILLED, REASON_NOT_REQUIRED, REASON_EMERGENCY)

HNR_DONE = "teljesítve"
HNR_PENDING = "folyamatban"
HNR_UNKNOWN = "ismeretlen"
HNR_NONE = "nincs"  # nem seedköteles tracker

ALERT_NOT_SEEDING = "not_seeding"
ALERT_STALLED = "stalled"
ALERT_SLOTS = "slots"
ALERT_HNR_UNAVAILABLE = "hnr_unavailable"
ALERT_EMERGENCY = "emergency"

DEFAULT_DETAILS_URLS = {
    "ncore": "https://ncore.pro/torrents.php?action=details&id={torrent_id}",
}


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
    tracker: str = ""


@dataclass(frozen=True)
class TrackerTorrentRef:
    """Melyik tracker melyik torrentje (a StremHU torrent_files táblája alapján)."""

    indexer_id: str
    torrent_id: str


@dataclass(frozen=True)
class HnrList:
    """Egy tracker Hit'n'Run-listája: a még nem teljesített torrentek azonosítói.

    `complete`: a lista minden elemét sikerült-e TorBox-hash-re/névre
    azonosítani. Ha nem, egy ismeretlen torrentről nem tudhatjuk biztosan,
    hogy nincs-e a listán, ezért nem nyúlunk hozzá."""

    pending_ids: frozenset[str]
    complete: bool = True
    # A tracker szerint mennyi seedelés van még hátra (ha kiderül az oldalról).
    remaining: dict[str, datetime.timedelta] = field(default_factory=dict)


@dataclass(frozen=True)
class StateEntry:
    """Amit mi csináltunk egy torrenttel, és mikor."""

    reason: str
    at: datetime.datetime


@dataclass(frozen=True)
class PlannerSettings:
    slot_limit: int
    min_free_slots: int
    min_seed: datetime.timedelta
    seed_trackers: tuple[str, ...] = ("ncore",)
    # Vészleállítás: teli helyeknél a seedkötelezettek közül is leállít.
    emergency_stop: bool = True
    # A torrent adatlapja a trackeren (az értesítésben link).
    details_urls: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_DETAILS_URLS)
    )


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    message: str
    torrent_id: int | None = None
    link: str | None = None


@dataclass(frozen=True)
class TorrentStatus:
    torrent: TorboxTorrent
    ref: TrackerTorrentRef | None
    seed_tracker: str | None
    hnr: str
    occupies_slot: bool
    entry: StateEntry | None
    remaining: datetime.timedelta | None = None


@dataclass
class Plan:
    statuses: list[TorrentStatus] = field(default_factory=list)
    stop: list[tuple[TorboxTorrent, str]] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
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


def _tracker_text(item: dict[str, Any]) -> str:
    """A TorBox a trackert különböző formában adhatja vissza (szöveg, lista)."""
    parts: list[str] = []
    for key in ("tracker", "trackers"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(entry) for entry in value if entry)
        elif isinstance(value, dict):
            parts.extend(str(entry) for entry in value.values() if entry)
    return " ".join(parts).lower()


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
        tracker=_tracker_text(item),
    )


def normalized_state(torrent: TorboxTorrent) -> str:
    return torrent.download_state.strip().lower()


def is_seeding(torrent: TorboxTorrent) -> bool:
    return normalized_state(torrent).startswith(SEEDING_PREFIXES)


def occupies_slot(torrent: TorboxTorrent) -> bool:
    """Helyet foglal-e. A TorBox `active` jelzője dönt (a TorBox felülete is ezt
    számolja): egy elakadt („stalled”), de nem aktív torrent nem foglal helyet."""
    if normalized_state(torrent).startswith(INACTIVE_PREFIXES):
        return False
    return torrent.active


def seed_tracker_of(
    torrent: TorboxTorrent,
    ref: TrackerTorrentRef | None,
    seed_trackers: tuple[str, ...],
) -> str | None:
    """Melyik seedköteles trackerhez tartozik a torrent (ha egyáltalán)."""
    if ref is not None:
        return ref.indexer_id if ref.indexer_id in seed_trackers else None
    for tracker in seed_trackers:
        if tracker in torrent.tracker:
            return tracker
    return None


def _is_young(
    torrent: TorboxTorrent, now: datetime.datetime, min_seed: datetime.timedelta
) -> bool:
    return torrent.created_at is None or now - torrent.created_at < min_seed


def hnr_status(
    torrent: TorboxTorrent,
    ref: TrackerTorrentRef | None,
    seed_tracker: str | None,
    hnr_lists: dict[str, HnrList],
    now: datetime.datetime,
    settings: PlannerSettings,
) -> str:
    young = _is_young(torrent, now, settings.min_seed)

    if seed_tracker is not None:
        hnr_list = hnr_lists.get(seed_tracker)
        if hnr_list is None:
            return HNR_UNKNOWN
        if ref is not None and ref.torrent_id in hnr_list.pending_ids:
            return HNR_PENDING
        if young:
            return HNR_PENDING
        if ref is None and not hnr_list.complete:
            return HNR_UNKNOWN
        return HNR_DONE

    if ref is not None:
        # A StremHU ismeri, de nem seedköteles trackerről jött.
        return HNR_NONE

    # Ismeretlen eredetű torrent: csak akkor biztos, hogy nem seedköteles, ha
    # minden seedköteles tracker listája elérhető és teljesen azonosított.
    for tracker in settings.seed_trackers:
        hnr_list = hnr_lists.get(tracker)
        if hnr_list is None or not hnr_list.complete:
            return HNR_UNKNOWN
    if torrent.private and young:
        return HNR_UNKNOWN
    return HNR_NONE


def prune_state(
    state: dict[int, StateEntry], torrents: list[TorboxTorrent]
) -> dict[int, StateEntry]:
    """Kidobja az eltűnt torrentek, és a régi (ideiglenes szünet) bejegyzéseket."""
    existing = {torrent.id for torrent in torrents}
    return {
        torrent_id: entry
        for torrent_id, entry in state.items()
        if torrent_id in existing and entry.reason in STOP_REASONS
    }


def format_remaining(remaining: datetime.timedelta) -> str:
    minutes = max(int(remaining.total_seconds() // 60), 0)
    hours, rest = divmod(minutes, 60)
    return f"{hours} ó {rest} p" if hours else f"{rest} p"


def _label(torrent: TorboxTorrent, status: TorrentStatus) -> str:
    if status.ref is not None:
        return f"{torrent.name} ({status.ref.indexer_id} #{status.ref.torrent_id})"
    return torrent.name


def _details_link(status: TorrentStatus, settings: PlannerSettings) -> str | None:
    if status.ref is None:
        return None
    template = settings.details_urls.get(status.ref.indexer_id)
    return template.format(torrent_id=status.ref.torrent_id) if template else None


def _emergency_message(
    status: TorrentStatus, settings: PlannerSettings, *, reminder: bool
) -> str:
    head = (
        "Helyhiány miatt leállított seed – még nincs befejezve, fejezd be máshol"
        if reminder
        else "Helyhiány miatt leállítottam egy nCore-seedet – fejezd be máshol"
    )
    parts = [f"{head} (pl. a laptopon, qBittorrenttel): {status.torrent.name}"]
    if status.remaining is not None:
        parts.append(f"hátra: {format_remaining(status.remaining)}")
    link = _details_link(status, settings)
    if link:
        parts.append(link)
    return " – ".join(parts)


def _emergency_sort_key(
    status: TorrentStatus,
) -> tuple[bool, datetime.timedelta, datetime.datetime]:
    """Előbb a legkevesebb hátralévő idejű; ismeretlen időnél a legrégebbi."""
    return (
        status.remaining is None,
        status.remaining or datetime.timedelta(0),
        status.torrent.created_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
    )


def plan_actions(
    torrents: list[TorboxTorrent],
    refs_by_hash: dict[str, TrackerTorrentRef],
    hnr_lists: dict[str, HnrList],
    state: dict[int, StateEntry],
    now: datetime.datetime,
    settings: PlannerSettings,
) -> Plan:
    plan = Plan()

    for torrent in torrents:
        ref = refs_by_hash.get(torrent.hash.lower())
        seed_tracker = seed_tracker_of(torrent, ref, settings.seed_trackers)
        hnr_list = hnr_lists.get(seed_tracker) if seed_tracker else None
        remaining = (
            hnr_list.remaining.get(ref.torrent_id)
            if hnr_list is not None and ref is not None
            else None
        )
        plan.statuses.append(
            TorrentStatus(
                torrent=torrent,
                ref=ref,
                seed_tracker=seed_tracker,
                hnr=hnr_status(torrent, ref, seed_tracker, hnr_lists, now, settings),
                occupies_slot=occupies_slot(torrent),
                entry=state.get(torrent.id),
                remaining=remaining,
            )
        )

    occupied = sum(1 for status in plan.statuses if status.occupies_slot)
    plan.occupied_before = occupied

    # 1. Seedkötelezettség nélküli, letöltött torrentek seedelésének leállítása.
    for status in plan.statuses:
        if not (status.torrent.download_finished and status.occupies_slot):
            continue
        if status.hnr == HNR_DONE:
            plan.stop.append((status.torrent, REASON_FULFILLED))
        elif status.hnr == HNR_NONE:
            plan.stop.append((status.torrent, REASON_NOT_REQUIRED))
    occupied -= len(plan.stop)
    plan.free_after = settings.slot_limit - occupied

    # 2. Vészleállítás: ha így is kevés a hely, a seedkötelezettek közül annak a
    # seedelését állítjuk le, amelyiknek a legkevesebb ideje van hátra.
    emergency_ids: set[int] = set()
    if settings.emergency_stop and plan.free_after < settings.min_free_slots:
        candidates = sorted(
            (
                status
                for status in plan.statuses
                if status.hnr == HNR_PENDING
                and status.occupies_slot
                and status.torrent.download_finished
            ),
            key=_emergency_sort_key,
        )
        need = settings.min_free_slots - plan.free_after
        for status in candidates[:need]:
            plan.stop.append((status.torrent, REASON_EMERGENCY))
            emergency_ids.add(status.torrent.id)
            plan.free_after += 1
            plan.alerts.append(
                Alert(
                    key=f"{ALERT_EMERGENCY}:{status.torrent.id}",
                    kind=ALERT_EMERGENCY,
                    message=_emergency_message(status, settings, reminder=False),
                    torrent_id=status.torrent.id,
                    link=_details_link(status, settings),
                )
            )

    # 3. Figyelmeztetések a seedkötelezett torrentekről.
    pending_seeding = 0
    for status in plan.statuses:
        if status.hnr != HNR_PENDING or status.torrent.id in emergency_ids:
            continue
        torrent = status.torrent
        state_text = normalized_state(torrent)
        if is_seeding(torrent):
            pending_seeding += 1
        elif status.entry is not None and status.entry.reason == REASON_EMERGENCY:
            plan.alerts.append(
                Alert(
                    key=f"{ALERT_EMERGENCY}:{torrent.id}",
                    kind=ALERT_EMERGENCY,
                    message=_emergency_message(status, settings, reminder=True),
                    torrent_id=torrent.id,
                    link=_details_link(status, settings),
                )
            )
        elif state_text.startswith("stalled"):
            plan.alerts.append(
                Alert(
                    key=f"{ALERT_STALLED}:{torrent.id}",
                    kind=ALERT_STALLED,
                    message=(
                        "Seedelni kellene, de elakadt (nincs letöltőtárs): "
                        f"{_label(torrent, status)}"
                    ),
                    torrent_id=torrent.id,
                    link=_details_link(status, settings),
                )
            )
        elif torrent.download_finished:
            plan.alerts.append(
                Alert(
                    key=f"{ALERT_NOT_SEEDING}:{torrent.id}",
                    kind=ALERT_NOT_SEEDING,
                    message=(
                        "Seedelni kellene, de a TorBox nem seedeli "
                        f"(állapot: {torrent.download_state or '?'}): "
                        f"{_label(torrent, status)}"
                    ),
                    torrent_id=torrent.id,
                    link=_details_link(status, settings),
                )
            )

    if plan.free_after < settings.min_free_slots:
        plan.alerts.append(
            Alert(
                key=ALERT_SLOTS,
                kind=ALERT_SLOTS,
                message=(
                    f"Kevés a szabad hely a TorBoxon ({max(plan.free_after, 0)}/"
                    f"{settings.slot_limit}, cél: legalább {settings.min_free_slots}). "
                    f"{pending_seeding} seedkötelezett torrent seedel; új film "
                    "indításakor a letöltés sorba állhat."
                ),
            )
        )

    for tracker in settings.seed_trackers:
        if tracker not in hnr_lists:
            plan.alerts.append(
                Alert(
                    key=f"{ALERT_HNR_UNAVAILABLE}:{tracker}",
                    kind=ALERT_HNR_UNAVAILABLE,
                    message=(
                        f"A(z) {tracker} Hit'n'Run-listája nem érhető el, ezért a "
                        "hozzá tartozó és az ismeretlen torrentekhez most nem nyúlok."
                    ),
                )
            )

    return plan
