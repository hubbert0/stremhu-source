"""Az állapotjelentés összeállítása és szöveges megjelenítése."""

import datetime
from typing import Any

from app.modules.torbox_seed.logic import (
    HNR_PENDING,
    REASON_FULFILLED,
    REASON_NOT_REQUIRED,
    Plan,
    PlannerSettings,
    StateEntry,
    parse_datetime,
)

REASON_LABELS = {
    REASON_FULFILLED: "Hit'n'Run teljesítve",
    REASON_NOT_REQUIRED: "nem kell seedelni",
    # Régebbi változat bejegyzései (olvasáshoz):
    "public": "nem kell seedelni",
    "slot": "helyfelszabadítás",
    "cooldown": "pihentetés",
}

# Előtag → magyar felirat (a TorBox állapotszövegei nem mindig egyszavasak).
_STATE_PREFIX_LABELS = (
    ("uploading", "seedel"),
    ("seeding", "seedel"),
    ("downloading", "tölt"),
    ("metadl", "tölt"),
    ("stalled", "elakadt"),
    ("checking", "ellenőriz"),
    ("paused", "szünetel"),
    ("stopped", "seed vége"),
    ("expired", "lejárt"),
    ("cached", "kész"),
    ("completed", "kész"),
)


def state_label(raw: str) -> str:
    normalized = raw.strip().lower()
    for prefix, label in _STATE_PREFIX_LABELS:
        if normalized.startswith(prefix):
            return label
    return raw


def build_status(
    plan: Plan,
    state: dict[int, StateEntry],
    settings: PlannerSettings,
    now: datetime.datetime,
    dry_run: bool,
    warnings: list[str],
) -> dict[str, Any]:
    stop_reasons = {torrent.id: reason for torrent, reason in plan.stop}

    rows: list[dict[str, Any]] = []
    for status in plan.statuses:
        torrent = status.torrent
        action = ""
        if torrent.id in stop_reasons:
            verb = "leállítanám" if dry_run else "seed leállítva"
            action = f"{verb} ({REASON_LABELS[stop_reasons[torrent.id]]})"

        if status.ref is not None:
            tracker = f"{status.ref.indexer_id} #{status.ref.torrent_id}"
        elif status.seed_tracker is not None:
            tracker = f"{status.seed_tracker} (tracker)"
        else:
            tracker = None

        entry = state.get(torrent.id)
        rows.append(
            {
                "name": torrent.name,
                "tracker": tracker,
                "hnr": status.hnr,
                "state": torrent.download_state,
                "occupies": status.occupies_slot,
                "action": action,
                "entry_reason": entry.reason if entry else None,
                "entry_at": entry.at.isoformat() if entry else None,
            }
        )

    return {
        "updated_at": now.isoformat(),
        "dry_run": dry_run,
        "slot_limit": settings.slot_limit,
        "min_free_slots": settings.min_free_slots,
        "seed_trackers": list(settings.seed_trackers),
        "occupied_before": plan.occupied_before,
        "free_after": plan.free_after,
        "rows": rows,
        "warnings": warnings,
    }


def _ago(at: datetime.datetime | None, now: datetime.datetime) -> str:
    if at is None:
        return "?"
    minutes = int((now - at).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} perce"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ó {rest} p"


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _is_relevant(row: dict[str, Any]) -> bool:
    """Ami helyet foglal, amivel most történt valami, vagy amit seedelni kell."""
    return bool(
        row.get("occupies") or row.get("action") or row.get("hnr") == HNR_PENDING
    )


def format_status(
    status: dict[str, Any] | None,
    now: datetime.datetime,
    show_all: bool = False,
) -> str:
    if not status:
        return (
            "Még nincs állapotjelentés. A TorBox-seedkezelés pár percen belül "
            "lefut (ha a TORBOX_API_KEY be van állítva)."
        )

    updated_at = parse_datetime(status.get("updated_at"))
    rows: list[dict[str, Any]] = status.get("rows", [])
    shown = rows if show_all else [row for row in rows if _is_relevant(row)]
    hidden = len(rows) - len(shown)
    seed_trackers = ", ".join(status.get("seed_trackers") or []) or "-"

    lines = [
        f"TorBox – utolsó ellenőrzés: {_ago(updated_at, now)}"
        + (" (PRÓBAÜZEM)" if status.get("dry_run") else ""),
        f"Szabad hely: {max(status.get('free_after', 0), 0)}/"
        f"{status.get('slot_limit', 0)} "
        f"(cél: legalább {status.get('min_free_slots')})",
        f"Seedelni csak ezeket kell: {seed_trackers} (amíg a Hit'n'Run folyamatban)",
        "",
    ]

    if shown:
        lines.append(
            f"{'Állapot':<10} {'Hit-n-Run':<12} {'Tracker':<16} {'Név':<44} "
            "Művelet / megjegyzés"
        )
        for row in shown:
            note = row.get("action") or ""
            if not note and row.get("entry_reason"):
                label = REASON_LABELS.get(row["entry_reason"], row["entry_reason"])
                note = (
                    f"seed leállítva ({label}, "
                    f"{_ago(parse_datetime(row.get('entry_at')), now)})"
                )
            lines.append(
                f"{_cut(state_label(str(row.get('state') or '')), 10):<10} "
                f"{_cut(str(row.get('hnr') or ''), 12):<12} "
                f"{_cut(row.get('tracker') or '-', 16):<16} "
                f"{_cut(str(row.get('name') or ''), 44):<44} {note}"
            )
    else:
        lines.append("Jelenleg egyik torrent sem foglal helyet.")

    if hidden:
        lines.append("")
        lines.append(
            f"További {hidden} torrent nem foglal helyet (kész / lejárt / seed vége). "
            "Mind: a parancs végére írd: --all"
        )

    warnings = status.get("warnings") or []
    if warnings:
        lines.append("")
    for warning in warnings:
        lines.append(f"⚠️  {warning}")

    return "\n".join(lines)
