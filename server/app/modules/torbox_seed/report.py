"""Az állapotjelentés összeállítása és szöveges megjelenítése."""

import datetime
from typing import Any

from app.modules.torbox_seed.logic import (
    REASON_COOLDOWN,
    REASON_FULFILLED,
    REASON_PUBLIC,
    REASON_SLOT,
    Plan,
    PlannerSettings,
    StateEntry,
    parse_datetime,
)

REASON_LABELS = {
    REASON_FULFILLED: "Hit'n'Run teljesítve",
    REASON_PUBLIC: "nyilvános torrent",
    REASON_SLOT: "helyfelszabadítás",
    REASON_COOLDOWN: "pihentetés",
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
    pause_reasons = {torrent.id: reason for torrent, reason in plan.pause}
    resume_reasons = {torrent.id: why for torrent, why in plan.resume}

    rows: list[dict[str, Any]] = []
    for status in plan.statuses:
        torrent = status.torrent
        action = ""
        if torrent.id in pause_reasons:
            action = f"szüneteltetve ({REASON_LABELS[pause_reasons[torrent.id]]})"
        elif torrent.id in resume_reasons:
            action = f"visszakapcsolva ({resume_reasons[torrent.id]})"
        elif torrent.id in plan.reclassify:
            action = "marad szüneteltetve (Hit'n'Run teljesítve)"

        entry = state.get(torrent.id)
        rows.append(
            {
                "name": torrent.name,
                "tracker": (
                    f"{status.ref.indexer_id} #{status.ref.torrent_id}"
                    if status.ref
                    else None
                ),
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
        "max_pause_hours": settings.max_pause.total_seconds() / 3600,
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
    """Ami helyet foglal, amivel most történt valami, vagy amit mi kezelünk."""
    return bool(row.get("occupies") or row.get("action") or row.get("entry_reason"))


def format_status(
    status: dict[str, Any] | None,
    now: datetime.datetime,
    show_all: bool = False,
) -> str:
    if not status:
        return (
            "Még nincs állapotjelentés. A TorBox-helykezelés pár percen belül "
            "lefut (ha a TORBOX_API_KEY be van állítva)."
        )

    updated_at = parse_datetime(status.get("updated_at"))
    rows: list[dict[str, Any]] = status.get("rows", [])
    shown = rows if show_all else [row for row in rows if _is_relevant(row)]
    hidden = len(rows) - len(shown)

    lines = [
        f"TorBox – utolsó ellenőrzés: {_ago(updated_at, now)}"
        + (" (PRÓBAÜZEM)" if status.get("dry_run") else ""),
        f"Szabad hely: {status.get('free_after', 0)}/{status.get('slot_limit', 0)} "
        f"(cél: legalább {status.get('min_free_slots')}, "
        f"ideiglenes szünet max. {status.get('max_pause_hours'):g} óra)",
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
                note = f"{label} ({_ago(parse_datetime(row.get('entry_at')), now)})"
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

    for warning in status.get("warnings", []):
        lines.append(f"⚠️  {warning}")

    return "\n".join(lines)
