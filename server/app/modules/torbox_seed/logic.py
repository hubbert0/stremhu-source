"""A takarítás döntési logikája – mellékhatás nélküli, külön tesztelhető."""

import datetime
from dataclasses import dataclass
from typing import Any

# Ezek az állapotok jelentik, hogy a torrent seedel (aktív helyet foglal).
SEEDING_STATES = frozenset({"uploading", "seeding"})


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
class CleanupDecision:
    torrent: TorboxTorrent
    ref: TrackerTorrentRef


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


def occupies_slot(torrent: TorboxTorrent) -> bool:
    return torrent.active or torrent.download_state in SEEDING_STATES


def select_for_cleanup(
    torrents: list[TorboxTorrent],
    refs_by_hash: dict[str, TrackerTorrentRef],
    not_completed_by_indexer: dict[str, set[str]],
    now: datetime.datetime,
    min_seed: datetime.timedelta,
) -> list[CleanupDecision]:
    """Kiválasztja a TorBoxról törölhető torrenteket.

    Csak akkor töröl, ha MINDEN feltétel teljesül:
      - a letöltés kész, és a torrent jelenleg aktív helyet foglal (seedel),
      - a StremHU-ból tudjuk, melyik tracker melyik torrentje,
      - az adott tracker Hit'n'Run-listáját sikerült lekérni,
      - a torrent NEM szerepel a tracker „nem teljesített” listáján,
      - a TorBoxra kerülés óta eltelt legalább `min_seed` idő.
    Bármilyen bizonytalanság esetén inkább nem töröl.
    """
    decisions: list[CleanupDecision] = []

    for torrent in torrents:
        if not torrent.download_finished or not occupies_slot(torrent):
            continue

        ref = refs_by_hash.get(torrent.hash.lower())
        if ref is None:
            continue

        not_completed = not_completed_by_indexer.get(ref.indexer_id)
        if not_completed is None:
            continue

        if ref.torrent_id in not_completed:
            continue

        if torrent.created_at is None or now - torrent.created_at < min_seed:
            continue

        decisions.append(CleanupDecision(torrent=torrent, ref=ref))

    return decisions
