import asyncio
import datetime
import json

import httpx
import pytest

from app.modules.torbox_seed.client import TorboxApiError, TorboxClient
from app.modules.torbox_seed.logic import (
    HNR_DONE,
    HNR_NONE,
    HNR_PENDING,
    HNR_UNKNOWN,
    REASON_COOLDOWN,
    REASON_FULFILLED,
    REASON_PUBLIC,
    REASON_SLOT,
    PlannerSettings,
    StateEntry,
    TorboxTorrent,
    TrackerTorrentRef,
    parse_datetime,
    parse_torbox_torrent,
    plan_actions,
    prune_state,
)
from app.modules.torbox_seed.report import build_status, format_status
from app.modules.torbox_seed.storage import load_state, save_state

NOW = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.timezone.utc)

SETTINGS = PlannerSettings(
    slot_limit=10,
    min_free_slots=2,
    max_pause=datetime.timedelta(hours=6),
    min_seed=datetime.timedelta(hours=24),
)


def h(n: int) -> str:
    return f"{n:040x}"


def torrent(
    n: int,
    *,
    state: str = "uploading",
    finished: bool = True,
    active: bool = True,
    private: bool = True,
    age_hours: float = 48,
) -> TorboxTorrent:
    return TorboxTorrent(
        id=n,
        hash=h(n),
        name=f"film-{n}",
        private=private,
        active=active,
        download_finished=finished,
        download_state=state,
        created_at=NOW - datetime.timedelta(hours=age_hours),
    )


def ncore_refs(*ids: int) -> dict[str, TrackerTorrentRef]:
    return {h(n): TrackerTorrentRef(indexer_id="ncore", torrent_id=str(n)) for n in ids}


def plan(torrents, refs=None, pending=(), state=None, hnr_available=True):
    not_completed = {"ncore": {str(n) for n in pending}} if hnr_available else {}
    return plan_actions(
        torrents=torrents,
        refs_by_hash=refs or {},
        not_completed_by_indexer=not_completed,
        state=state or {},
        now=NOW,
        settings=SETTINGS,
    )


def paused_ids(p, reason=None):
    return sorted(t.id for t, r in p.pause if reason is None or r == reason)


# --- Seedkötelezettség nélküliek -------------------------------------------


def test_fulfilled_seed_is_paused_not_deleted():
    p = plan([torrent(1)], refs=ncore_refs(1))
    assert paused_ids(p, REASON_FULFILLED) == [1]
    assert p.statuses[0].hnr == HNR_DONE


def test_public_seed_is_paused():
    p = plan([torrent(1, private=False)])
    assert paused_ids(p, REASON_PUBLIC) == [1]
    assert p.statuses[0].hnr == HNR_NONE


def test_pending_seed_with_enough_room_is_kept():
    p = plan([torrent(1)], refs=ncore_refs(1), pending=[1])
    assert p.pause == []
    assert p.free_after == 9


def test_young_torrent_counts_as_pending():
    p = plan([torrent(1, age_hours=3)], refs=ncore_refs(1))
    assert p.statuses[0].hnr == HNR_PENDING
    assert p.pause == []


def test_unknown_private_torrent_is_never_touched():
    torrents = [torrent(n) for n in range(1, 11)]
    p = plan(torrents)  # nincs ref, privát
    assert all(s.hnr == HNR_UNKNOWN for s in p.statuses)
    assert p.pause == []


def test_unavailable_hnr_list_means_no_action():
    torrents = [torrent(n) for n in range(1, 11)]
    p = plan(torrents, refs=ncore_refs(*range(1, 11)), hnr_available=False)
    assert p.pause == []


def test_downloading_torrent_is_never_paused():
    torrents = [torrent(n, state="downloading", finished=False) for n in range(1, 11)]
    p = plan(torrents, refs=ncore_refs(*range(1, 11)), pending=range(1, 11))
    assert p.pause == []


# --- Legalább 2 szabad hely -------------------------------------------------


def test_full_slots_pause_two_newest_pending():
    torrents = [torrent(n, age_hours=100 - n) for n in range(1, 11)]
    p = plan(torrents, refs=ncore_refs(*range(1, 11)), pending=range(1, 11))
    # a legfrissebbek (legkisebb kor): 10 és 9
    assert paused_ids(p, REASON_SLOT) == [9, 10]
    assert p.free_after == 2


def test_nine_occupied_pauses_one():
    torrents = [torrent(n, age_hours=100 - n) for n in range(1, 10)]
    p = plan(torrents, refs=ncore_refs(*range(1, 10)), pending=range(1, 10))
    assert paused_ids(p, REASON_SLOT) == [9]
    assert p.free_after == 2


def test_fulfilled_pauses_reduce_need_for_slot_pauses():
    torrents = [torrent(n, age_hours=100 - n) for n in range(1, 11)]
    # 1 és 2 teljesítve, a többi folyamatban
    p = plan(torrents, refs=ncore_refs(*range(1, 11)), pending=range(3, 11))
    assert paused_ids(p, REASON_FULFILLED) == [1, 2]
    assert paused_ids(p, REASON_SLOT) == []
    assert p.free_after == 2


def test_slot_pauses_are_capped():
    torrents = [torrent(n, age_hours=100 - n) for n in range(1, 11)]
    torrents.append(torrent(11, state="paused", active=False))
    torrents.append(torrent(12, state="paused", active=False))
    state = {
        11: StateEntry(REASON_SLOT, NOW - datetime.timedelta(hours=1)),
        12: StateEntry(REASON_SLOT, NOW - datetime.timedelta(hours=1)),
    }
    p = plan(
        torrents, refs=ncore_refs(*range(1, 13)), pending=range(1, 13), state=state
    )
    # már 2 ideiglenesen szünetel, többet nem szüneteltet
    assert paused_ids(p, REASON_SLOT) == []


def test_cooldown_torrent_is_not_paused_again():
    torrents = [torrent(n, age_hours=100 - n) for n in range(1, 11)]
    state = {10: StateEntry(REASON_COOLDOWN, NOW - datetime.timedelta(hours=1))}
    p = plan(
        torrents, refs=ncore_refs(*range(1, 11)), pending=range(1, 11), state=state
    )
    assert 10 not in paused_ids(p)
    assert paused_ids(p, REASON_SLOT) == [8, 9]


# --- Visszakapcsolás --------------------------------------------------------


def test_slot_paused_is_resumed_when_room():
    torrents = [torrent(1), torrent(2, state="paused", active=False)]
    state = {2: StateEntry(REASON_SLOT, NOW - datetime.timedelta(hours=1))}
    p = plan(torrents, refs=ncore_refs(1, 2), pending=[1, 2], state=state)
    assert [(t.id, why) for t, why in p.resume] == [(2, "van szabad hely")]


def test_slot_paused_waits_when_no_room():
    torrents = [torrent(n) for n in range(1, 9)]  # 8 foglalt → 2 szabad
    torrents.append(torrent(9, state="paused", active=False))
    state = {9: StateEntry(REASON_SLOT, NOW - datetime.timedelta(hours=1))}
    p = plan(
        torrents, refs=ncore_refs(*range(1, 10)), pending=range(1, 10), state=state
    )
    assert p.resume == []


def test_slot_paused_is_force_resumed_after_max_pause():
    torrents = [torrent(n) for n in range(1, 9)]
    torrents.append(torrent(9, state="paused", active=False))
    state = {9: StateEntry(REASON_SLOT, NOW - datetime.timedelta(hours=7))}
    p = plan(
        torrents, refs=ncore_refs(*range(1, 10)), pending=range(1, 10), state=state
    )
    assert [(t.id, why) for t, why in p.resume] == [(9, "időkorlát")]


def test_slot_paused_that_got_fulfilled_stays_paused():
    torrents = [torrent(1, state="paused", active=False)]
    state = {1: StateEntry(REASON_SLOT, NOW - datetime.timedelta(hours=7))}
    p = plan(torrents, refs=ncore_refs(1), pending=[], state=state)
    assert p.resume == []
    assert p.reclassify == {1: REASON_FULFILLED}


def test_fulfilled_paused_is_never_resumed():
    torrents = [torrent(1, state="paused", active=False)]
    state = {1: StateEntry(REASON_FULFILLED, NOW - datetime.timedelta(days=3))}
    p = plan(torrents, refs=ncore_refs(1), state=state)
    assert p.resume == []


# --- Állapot karbantartása --------------------------------------------------


def test_prune_state():
    torrents = [
        torrent(1, state="paused", active=False),
        torrent(2),  # valaki kézzel visszakapcsolta
        torrent(3),
        torrent(4),
    ]
    state = {
        1: StateEntry(REASON_SLOT, NOW),
        2: StateEntry(REASON_SLOT, NOW),
        3: StateEntry(REASON_COOLDOWN, NOW - datetime.timedelta(hours=1)),
        4: StateEntry(REASON_COOLDOWN, NOW - datetime.timedelta(hours=7)),
        99: StateEntry(REASON_FULFILLED, NOW),  # eltűnt a TorBoxról
    }
    pruned = prune_state(state, torrents, NOW, SETTINGS.max_pause)
    assert sorted(pruned) == [1, 3]


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = {7: StateEntry(REASON_SLOT, NOW)}
    save_state(path, state)
    assert load_state(path) == state
    assert load_state(tmp_path / "nincs.json") == {}


def test_status_report_renders():
    torrents = [torrent(n, age_hours=100 - n) for n in range(1, 11)]
    p = plan(torrents, refs=ncore_refs(*range(1, 11)), pending=range(1, 11))
    status = build_status(p, {}, SETTINGS, NOW, dry_run=False, warnings=[])
    text = format_status(status, NOW)
    assert "Szabad hely: 2/10" in text
    assert "helyfelszabadítás" in text
    assert "film-10" in text
    assert "Még nincs állapotjelentés" in format_status(None, NOW)


# --- Segédfüggvények és kliens ----------------------------------------------


def test_parse_datetime_formats():
    assert parse_datetime("2026-10-31T10:00:00Z") == datetime.datetime(
        2026, 10, 31, 10, 0, tzinfo=datetime.timezone.utc
    )
    naive = parse_datetime("2026-10-31T10:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert parse_datetime("nem dátum") is None
    assert parse_datetime(None) is None


def test_parse_torbox_torrent():
    t = parse_torbox_torrent(
        {
            "id": 42,
            "hash": h(1).upper(),
            "name": "Film.2026.1080p",
            "private": True,
            "active": True,
            "download_finished": True,
            "download_state": "uploading",
            "created_at": "2026-10-30T10:00:00Z",
        }
    )
    assert t.id == 42 and t.hash == h(1) and t.created_at is not None


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
            json={"success": True, "data": [{"id": 1, "hash": h(1), "active": True}]},
        )

    torrents = asyncio.run(_client(handler).list_torrents())
    assert seen["auth"] == "Bearer teszt-kulcs"
    assert seen["path"] == "/v1/api/torrents/mylist"
    assert [t.id for t in torrents] == [1]


def test_client_empty_list_on_404():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"success": False, "detail": "not found"})

    assert asyncio.run(_client(handler).list_torrents()) == []


@pytest.mark.parametrize(
    "method,operation", [("pause_torrent", "pause"), ("resume_torrent", "resume")]
)
def test_client_control_operations(method, operation):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": None})

    asyncio.run(getattr(_client(handler), method)(7))
    assert seen["path"] == "/v1/api/torrents/controltorrent"
    assert seen["body"] == {"torrent_id": 7, "operation": operation}


def test_client_raises_on_api_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"success": False, "detail": "BAD_TOKEN"})

    with pytest.raises(TorboxApiError):
        asyncio.run(_client(handler).list_torrents())
