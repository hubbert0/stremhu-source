"""A beolvasott csatornák helyi tára (SQLite) és a beolvasás.

Csatornánként eltároljuk a feltöltött videókat (cím, hossz, bélyegkép) és a
lejátszási listákat a sorrendjükkel. A Stremio-kérések csak ebből a tárból
dolgoznak, a YouTube API-t nem hívják.
"""

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.matching import norm
from app.youtube import YouTubeClient, YouTubeError

logger = logging.getLogger("youtube")

MIN_PLAYLIST_ITEMS = 2


@dataclass(frozen=True)
class Video:
    id: str
    channel_id: str
    channel_title: str
    title: str
    norm: str
    duration: int
    published: str
    thumb: str


@dataclass(frozen=True)
class PlaylistRow:
    id: str
    channel_id: str
    channel_title: str
    title: str
    norm: str
    count: int
    thumb: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY, ref TEXT NOT NULL, title TEXT NOT NULL, thumb TEXT NOT NULL,
  sort INTEGER NOT NULL, updated REAL NOT NULL, videos INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, title TEXT NOT NULL,
  norm TEXT NOT NULL, duration INTEGER NOT NULL, published TEXT NOT NULL,
  thumb TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS videos_channel ON videos (channel_id);
CREATE TABLE IF NOT EXISTS playlists (
  id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, title TEXT NOT NULL,
  norm TEXT NOT NULL, count INTEGER NOT NULL, thumb TEXT NOT NULL,
  sort INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_items (
  playlist_id TEXT NOT NULL, position INTEGER NOT NULL, video_id TEXT NOT NULL,
  PRIMARY KEY (playlist_id, position)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Index:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ------------------------------------------------------------ írás
    def replace_channel(
        self,
        *,
        ref: str,
        sort: int,
        channel_id: str,
        title: str,
        thumb: str,
        videos: list[tuple[str, str, int, str, str]],
        playlists: list[tuple[str, str, str, list[str]]],
    ) -> None:
        """Egy csatorna teljes tartalmának cseréje egy tranzakcióban.

        videos: (id, cím, hossz, közzététel, bélyegkép)
        playlists: (id, cím, bélyegkép, [videó-azonosítók sorrendben])
        """
        with self._lock:
            db = self._db
            old = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM playlists WHERE channel_id = ?", (channel_id,)
                )
            ]
            db.executemany(
                "DELETE FROM playlist_items WHERE playlist_id = ?",
                [(pid,) for pid in old],
            )
            db.execute("DELETE FROM playlists WHERE channel_id = ?", (channel_id,))
            db.execute("DELETE FROM videos WHERE channel_id = ?", (channel_id,))
            db.executemany(
                "INSERT OR REPLACE INTO videos VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (vid, channel_id, vtitle, norm(vtitle), duration, published, vthumb)
                    for vid, vtitle, duration, published, vthumb in videos
                ],
            )
            for order, (pid, ptitle, pthumb, items) in enumerate(playlists):
                db.execute(
                    "INSERT OR REPLACE INTO playlists VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, channel_id, ptitle, norm(ptitle), len(items), pthumb, order),
                )
                db.executemany(
                    "INSERT OR REPLACE INTO playlist_items VALUES (?, ?, ?)",
                    [(pid, pos, vid) for pos, vid in enumerate(items)],
                )
            db.execute(
                "INSERT OR REPLACE INTO channels VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_id, ref, title, thumb, sort, time.time(), len(videos)),
            )
            db.commit()

    def remove_other_channels(self, keep_refs: tuple[str, ...]) -> None:
        """A beállításból kivett csatornák törlése."""
        with self._lock:
            rows = self._db.execute("SELECT id, ref FROM channels").fetchall()
            for channel_id, ref in rows:
                if ref in keep_refs:
                    continue
                pids = [
                    r[0]
                    for r in self._db.execute(
                        "SELECT id FROM playlists WHERE channel_id = ?", (channel_id,)
                    )
                ]
                self._db.executemany(
                    "DELETE FROM playlist_items WHERE playlist_id = ?",
                    [(pid,) for pid in pids],
                )
                for table in ("playlists", "videos"):
                    self._db.execute(
                        f"DELETE FROM {table} WHERE channel_id = ?", (channel_id,)
                    )
                self._db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            self._db.commit()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
            self._db.commit()

    # ------------------------------------------------------------ olvasás
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row else None

    def channel_updated(self, ref: str) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT updated FROM channels WHERE ref = ?", (ref,)
            ).fetchone()
        return float(row[0]) if row else None

    def channels(self) -> list[tuple[str, str, int, float]]:
        """(id, cím, videók száma, frissítés ideje) a beállított sorrendben."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, videos, updated FROM channels ORDER BY sort"
            ).fetchall()
        return [(str(a), str(b), int(c), float(d)) for a, b, c, d in rows]

    def videos(self) -> list[Video]:
        with self._lock:
            rows = self._db.execute(
                "SELECT v.id, v.channel_id, c.title, v.title, v.norm, v.duration,"
                " v.published, v.thumb FROM videos v JOIN channels c ON c.id = v.channel_id"
                " ORDER BY c.sort, v.published"
            ).fetchall()
        return [Video(*row) for row in rows]

    def video(self, video_id: str) -> Video | None:
        with self._lock:
            row = self._db.execute(
                "SELECT v.id, v.channel_id, c.title, v.title, v.norm, v.duration,"
                " v.published, v.thumb FROM videos v JOIN channels c ON c.id = v.channel_id"
                " WHERE v.id = ?",
                (video_id,),
            ).fetchone()
        return Video(*row) if row else None

    def playlists(self, min_items: int = MIN_PLAYLIST_ITEMS) -> list[PlaylistRow]:
        with self._lock:
            rows = self._db.execute(
                "SELECT p.id, p.channel_id, c.title, p.title, p.norm, p.count, p.thumb"
                " FROM playlists p JOIN channels c ON c.id = p.channel_id"
                " WHERE p.count >= ? ORDER BY c.sort, p.sort",
                (min_items,),
            ).fetchall()
        return [PlaylistRow(*row) for row in rows]

    def playlist(self, playlist_id: str) -> PlaylistRow | None:
        with self._lock:
            row = self._db.execute(
                "SELECT p.id, p.channel_id, c.title, p.title, p.norm, p.count, p.thumb"
                " FROM playlists p JOIN channels c ON c.id = p.channel_id WHERE p.id = ?",
                (playlist_id,),
            ).fetchone()
        return PlaylistRow(*row) if row else None

    def playlist_videos(self, playlist_id: str) -> list[Video]:
        """A lista videói sorrendben (csak a beolvasott, nyilvános videók)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT v.id, v.channel_id, c.title, v.title, v.norm, v.duration,"
                " v.published, v.thumb FROM playlist_items i"
                " JOIN videos v ON v.id = i.video_id"
                " JOIN channels c ON c.id = v.channel_id"
                " WHERE i.playlist_id = ? ORDER BY i.position",
                (playlist_id,),
            ).fetchall()
        return [Video(*row) for row in rows]


class Indexer:
    """A beállított csatornák beolvasása a YouTube API-ból a helyi tárba."""

    def __init__(self, client: YouTubeClient, index: Index) -> None:
        self._client = client
        self._index = index
        self.running = False

    async def refresh(self, refs: tuple[str, ...], max_age_hours: float = 0) -> int:
        """Beolvassa a régebben frissített csatornákat; a beolvasottak számát adja."""
        if self.running:
            return 0
        self.running = True
        done = 0
        try:
            self._index.remove_other_channels(refs)
            for sort, ref in enumerate(refs):
                updated = self._index.channel_updated(ref)
                if updated and time.time() - updated < max_age_hours * 3600:
                    continue
                try:
                    if await self._refresh_channel(ref, sort):
                        done += 1
                except YouTubeError as error:
                    logger.warning(
                        "A(z) %s csatorna beolvasása nem sikerült: %s", ref, error
                    )
                    if error.status == 403:
                        # Kvótatúllépés vagy rossz kulcs: a többit sem érdemes próbálni.
                        self._index.set_meta("last_error", str(error))
                        break
            self._index.set_meta("last_refresh", str(time.time()))
        finally:
            self.running = False
        return done

    async def _refresh_channel(self, ref: str, sort: int) -> bool:
        channel = await self._client.channel(ref)
        if channel is None or not channel.uploads:
            logger.warning("Nincs ilyen YouTube-csatorna: %s", ref)
            return False
        uploads = await self._client.playlist_items(channel.uploads)
        durations = await self._client.durations([e.video_id for e in uploads])
        videos = [
            (e.video_id, e.title, durations[e.video_id], e.published, e.thumb)
            for e in uploads
            if e.video_id in durations
        ]
        known = {vid for vid, *_ in videos}
        playlists: list[tuple[str, str, str, list[str]]] = []
        for playlist in await self._client.channel_playlists(channel.id):
            if playlist.count < MIN_PLAYLIST_ITEMS:
                continue
            items = await self._client.playlist_items(playlist.id)
            ids = [e.video_id for e in items if e.video_id in known]
            if len(ids) >= MIN_PLAYLIST_ITEMS:
                playlists.append((playlist.id, playlist.title, playlist.thumb, ids))
        self._index.replace_channel(
            ref=ref,
            sort=sort,
            channel_id=channel.id,
            title=channel.title,
            thumb=channel.thumb,
            videos=videos,
            playlists=playlists,
        )
        logger.info(
            "Beolvasva: %s – %d videó, %d lejátszási lista (kvóta eddig: %d egység)",
            channel.title,
            len(videos),
            len(playlists),
            self._client.units,
        )
        return True
