import asyncio
import datetime
import json

import httpx
import pytest

from app.modules.torbox_seed.client import TorboxApiError, TorboxClient
from app.modules.torbox_seed.logic import (
    TorboxTorrent,
    TrackerTorrentRef,
    parse_datetime,
    parse_torbox_torrent,
    select_for_cleanup,
)

NOW = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.timezone.utc)
MIN_SEED = datetime.timedelta(hours=24)

HASH_A = "a" * 40
HASH_B = "b" * 40


def make_torrent(
    torrent_id: int = 1,
    info_hash: str = HASH_A,
    active: bool = True,
    download_finished: bool = True,
    download_state: str = "uploading",
    age_hours: float = 48,
) -> TorboxTorrent:
    return TorboxTorrent(
        id=torrent_id,
        hash=info_hash,
        name=f"torrent-{torrent_id}",
        private=True,
        active=active,
        download_finished=download_finished,
        download_state=download_state,
        created_at=NOW - datetime.timedelta(hours=age_hours),
    )


REFS = {
    HASH_A: TrackerTorrentRef(indexer_id="ncore", torrent_id="111"),
    HASH_B: TrackerTorrentRef(indexer_id="ncore", torrent_id="222"),
}


def select(torrents, not_completed=None, refs=None):
    return select_for_cleanup(
        torrents=torrents,
        refs_by_hash=REFS if refs is None else refs,
        not_completed_by_indexer={"ncore": set()}
        if not_completed is None
        else not_completed,
        now=NOW,
        min_seed=MIN_SEED,
    )


def test_fulfilled_seeding_torrent_is_selected():
    decisions = select([make_torrent()])
    assert [d.torrent.id for d in decisions] == [1]
    assert decisions[0].ref.torrent_id == "111"


def test_torrent_on_hit_and_run_list_is_kept():
    decisions = select([make_torrent()], not_completed={"ncore": {"111"}})
    assert decisions == []


def test_only_listed_torrent_is_kept():
    torrents = [make_torrent(1, HASH_A), make_torrent(2, HASH_B)]
    decisions = select(torrents, not_completed={"ncore": {"222"}})
    assert [d.torrent.id for d in decisions] == [1]


def test_unavailable_hit_and_run_list_means_no_delete():
    decisions = select([make_torrent()], not_completed={})
    assert decisions == []


def test_unknown_torrent_is_kept():
    decisions = select([make_torrent(info_hash="c" * 40)])
    assert decisions == []


def test_unfinished_download_is_kept():
    decisions = select(
        [make_torrent(download_finished=False, download_state="downloading")]
    )
    assert decisions == []


def test_torrent_not_occupying_slot_is_kept():
    decisions = select([make_torrent(active=False, download_state="cached")])
    assert decisions == []


def test_seeding_state_counts_even_if_active_flag_missing():
    decisions = select([make_torrent(active=False, download_state="uploading")])
    assert [d.torrent.id for d in decisions] == [1]


def test_too_young_torrent_is_kept():
    decisions = select([make_torrent(age_hours=5)])
    assert decisions == []


def test_missing_created_at_is_kept():
    torrent = make_torrent()
    torrent = TorboxTorrent(**{**torrent.__dict__, "created_at": None})
    assert select([torrent]) == []


def test_hash_matching_is_case_insensitive():
    decisions = select([make_torrent(info_hash=HASH_A.upper())])
    assert [d.torrent.id for d in decisions] == [1]


def test_parse_datetime_formats():
    assert parse_datetime("2026-10-31T10:00:00Z") == datetime.datetime(
        2026, 10, 31, 10, 0, tzinfo=datetime.timezone.utc
    )
    assert parse_datetime("2026-10-31T10:00:00+00:00") is not None
    naive = parse_datetime("2026-10-31T10:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert parse_datetime("nem dátum") is None
    assert parse_datetime(None) is None


def test_parse_torbox_torrent():
    torrent = parse_torbox_torrent(
        {
            "id": 42,
            "hash": HASH_A.upper(),
            "name": "Film.2026.1080p",
            "private": True,
            "active": True,
            "download_finished": True,
            "download_state": "uploading",
            "created_at": "2026-10-30T10:00:00Z",
        }
    )
    assert torrent.id == 42
    assert torrent.hash == HASH_A
    assert torrent.created_at is not None


def _client(handler) -> TorboxClient:
    return TorboxClient(
        "teszt-kulcs",
        base_url="https://torbox.test",
        transport=httpx.MockTransport(handler),
    )


def test_client_lists_torrents_with_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "hash": HASH_A,
                        "active": True,
                        "download_finished": True,
                        "download_state": "uploading",
                    }
                ],
            },
        )

    torrents = asyncio.run(_client(handler).list_torrents())
    assert seen["auth"] == "Bearer teszt-kulcs"
    assert seen["path"] == "/v1/api/torrents/mylist"
    assert [t.id for t in torrents] == [1]


def test_client_empty_list_on_404():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"success": False, "detail": "not found"})

    assert asyncio.run(_client(handler).list_torrents()) == []


def test_client_delete_sends_delete_operation():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": None})

    asyncio.run(_client(handler).delete_torrent(7))
    assert seen["path"] == "/v1/api/torrents/controltorrent"
    assert seen["body"] == {"torrent_id": 7, "operation": "delete"}


def test_client_raises_on_api_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"success": False, "detail": "BAD_TOKEN"})

    with pytest.raises(TorboxApiError):
        asyncio.run(_client(handler).list_torrents())
