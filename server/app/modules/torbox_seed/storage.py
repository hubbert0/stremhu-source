"""Állapot- és jelentésfájlok (JSON) a data/system mappában."""

import datetime
import json
from pathlib import Path
from typing import Any

from app.modules.torbox_seed.logic import StateEntry, parse_datetime

STATE_FILE_NAME = "torbox_seed_state.json"
STATUS_FILE_NAME = "torbox_seed_status.json"


def load_state(path: Path) -> dict[int, StateEntry]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
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


def save_status(path: Path, status: dict[str, Any]) -> None:
    _write_json(path, status)


def load_status(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)
