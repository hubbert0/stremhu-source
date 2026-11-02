"""Állapot- és jelentésfájlok (JSON) a data/system mappában."""

import datetime
import json
from pathlib import Path
from typing import Any

from app.modules.torbox_seed.alerts import AlertRecord
from app.modules.torbox_seed.logic import StateEntry, parse_datetime

STATE_FILE_NAME = "torbox_seed_state.json"
STATUS_FILE_NAME = "torbox_seed_status.json"
ALERTS_FILE_NAME = "torbox_seed_alerts.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def load_state(path: Path) -> dict[int, StateEntry]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return {}

    state: dict[int, StateEntry] = {}
    for key, value in raw.items():
        at = parse_datetime(value.get("at")) if isinstance(value, dict) else None
        reason = value.get("reason") if isinstance(value, dict) else None
        if at is None or not isinstance(reason, str):
            continue
        try:
            state[int(key)] = StateEntry(reason=reason, at=at)
        except ValueError:
            continue
    return state


def save_state(path: Path, state: dict[int, StateEntry]) -> None:
    data = {
        str(torrent_id): {"reason": entry.reason, "at": entry.at.isoformat()}
        for torrent_id, entry in state.items()
    }
    _write_json(path, data)


def load_alert_records(path: Path) -> dict[str, AlertRecord]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return {}

    records: dict[str, AlertRecord] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        first_seen = parse_datetime(value.get("first_seen"))
        if first_seen is None:
            continue
        records[str(key)] = AlertRecord(
            first_seen=first_seen,
            last_sent=parse_datetime(value.get("last_sent")),
        )
    return records


def save_alert_records(path: Path, records: dict[str, AlertRecord]) -> None:
    data = {
        key: {
            "first_seen": record.first_seen.isoformat(),
            "last_sent": record.last_sent.isoformat() if record.last_sent else None,
        }
        for key, record in records.items()
    }
    _write_json(path, data)


def save_status(path: Path, status: dict[str, Any]) -> None:
    _write_json(path, status)


def load_status(path: Path) -> dict[str, Any] | None:
    raw = _read_json(path)
    return raw if isinstance(raw, dict) else None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)
