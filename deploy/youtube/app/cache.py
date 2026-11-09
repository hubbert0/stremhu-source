"""Egyszerű, lejárati idős gyorsítótár SQLite-ban.

Az Emby-szervereket kíméljük: a keresések eredményét és a plakátokat egy ideig
eltároljuk (az otthoni gépek feltöltését a filmek kapják, ne a plakátok).
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

MAX_BLOB_BYTES = 300 * 1024 * 1024  # a plakátok összesen legfeljebb ennyit foglalnak


class Cache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " key TEXT PRIMARY KEY, value BLOB NOT NULL, kind TEXT NOT NULL,"
            " expires REAL NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL)"
        )
        self._db.commit()

    def _get(self, key: str) -> tuple[bytes, str] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value, kind, expires FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, kind, expires = row
        if expires < time.time():
            self.delete(key)
            return None
        return bytes(value), str(kind)

    def _set(self, key: str, value: bytes, kind: str, ttl: float) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, value, kind, expires, size, created)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (key, value, kind, now + ttl, len(value), now),
            )
            self._db.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._db.commit()

    def get_json(self, key: str) -> Any | None:
        found = self._get(key)
        if found is None or found[1] != "json":
            return None
        return json.loads(found[0].decode("utf-8"))

    def set_json(self, key: str, value: Any, ttl: float) -> None:
        self._set(
            key, json.dumps(value, ensure_ascii=False).encode("utf-8"), "json", ttl
        )

    def get_bytes(self, key: str) -> bytes | None:
        found = self._get(key)
        if found is None or found[1] != "bytes":
            return None
        return found[0]

    def set_bytes(self, key: str, value: bytes, ttl: float) -> None:
        self._set(key, value, "bytes", ttl)
        self._prune()

    def _prune(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cache WHERE expires < ?", (time.time(),))
            total = self._db.execute(
                "SELECT COALESCE(SUM(size), 0) FROM cache WHERE kind = 'bytes'"
            ).fetchone()[0]
            if total > MAX_BLOB_BYTES:
                rows = self._db.execute(
                    "SELECT key, size FROM cache WHERE kind = 'bytes' ORDER BY created"
                ).fetchall()
                for key, size in rows:
                    if total <= MAX_BLOB_BYTES * 0.8:
                        break
                    self._db.execute("DELETE FROM cache WHERE key = ?", (key,))
                    total -= size
            self._db.commit()
