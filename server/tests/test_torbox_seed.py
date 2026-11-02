import asyncio
import datetime
import json

import httpx
import pytest

from app.modules.torbox_seed.alerts import (
    AlertRecord,
    due_alerts,
    mark_sent,
    notifiable_alerts,
    notification_text,
    send_ntfy,
)
from app.modules.torbox_seed.background_tasks import _match_refs
from app.modules.torbox_seed.client import TorboxApiError, TorboxClient
from app.modules.torbox_seed.config import TorboxSeedSettings
from app.modules.torbox_seed.logic import (
    ALERT_EMERGENCY,
    ALERT_HNR_UNAVAILABLE,
    ALERT_NOT_SEEDING,
    ALERT_SLOTS,
    ALERT_STALLED,
    HNR_DONE,
    HNR_NONE,
    HNR_PENDING,
    HNR_UNKNOWN,
    REASON_EMERGENCY,
    REASON_FULFILLED,
    REASON_NOT_REQUIRED,
    Alert,
    HnrList,
    PlannerSettings,
    StateEntry,
    TorboxTorrent,
    TrackerTorrentRef,
    occupies_slot,
    parse_datetime,
    parse_torbox_torrent,
    plan_actions,
    prune_state,
)
from app.modules.torbox_seed.report import build_status, format_status, state_label
from app.modules.torbox_seed.storage import (
    load_alert_records,
    load_state,
    save_alert_records,
    save_state,
)

NOW = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.timezone.utc)

SETTINGS = PlannerSettings(
    slot_limit=10,
    min_free_slots=2,
    min_seed=datetime.timedelta(hours=24),
    seed_trackers=("ncore",),
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
    tracker: str = "",
    name: str | None = None,
) -> TorboxTorrent:
    return TorboxTorrent(
        id=n,
        hash=h(n),
        name=name or f"film-{n}",
        private=private,
        active=active,
        download_finished=finished,
        download_state=state,
        created_at=NOW - datetime.timedelta(hours=age_hours),
        tracker=tracker,
    )


def refs(*ids: int, indexer: str = "ncore") -> dict[str, TrackerTorrentRef]:
    return {h(n): TrackerTorrentRef(indexer_id=indexer, torrent_id=str(n)) for n in ids}


def plan(
    torrents,
    refs_by_hash=None,
    pending=(),
    state=None,
    hnr="ok",
    remaining=None,
    settings=SETTINGS,
):
    hours = {str(n): datetime.timedelta(hours=v) for n, v in (remaining or {}).items()}
    ids = frozenset(str(n) for n in pending)
    if hnr == "ok":
        lists = {"ncore": HnrList(ids, complete=True, remaining=hours)}
    elif hnr == "incomplete":
        lists = {"ncore": HnrList(ids, complete=False, remaining=hours)}
    else:
        lists = {}
    return plan_actions(
        torrents=torrents,
        refs_by_hash=refs_by_hash or {},
        hnr_lists=lists,
        state=state or {},
        now=NOW,
        settings=settings,
    )


def stopped(p, reason=None):
    return sorted(t.id for t, r in p.stop if reason is None or r == reason)


def alert_kinds(p):
    return sorted(a.kind for a in p.alerts)


# --- Mit kell seedelni, mit nem ---------------------------------------------


def test_fulfilled_ncore_seed_is_stopped():
    p = plan([torrent(1)], refs(1))
    assert stopped(p, REASON_FULFILLED) == [1]
    assert p.statuses[0].hnr == HNR_DONE


def test_pending_ncore_seed_is_kept():
    p = plan([torrent(1)], refs(1), pending=[1])
    assert p.stop == []
    assert p.statuses[0].hnr == HNR_PENDING
    assert p.free_after == 9


def test_young_ncore_torrent_counts_as_pending():
    p = plan([torrent(1, age_hours=3)], refs(1))
    assert p.statuses[0].hnr == HNR_PENDING
    assert p.stop == []


def test_other_tracker_known_to_stremhu_is_not_seeded():
    p = plan([torrent(1)], refs(1, indexer="iptorrents"))
    assert p.statuses[0].hnr == HNR_NONE
    assert stopped(p, REASON_NOT_REQUIRED) == [1]


def test_public_unknown_torrent_is_stopped():
    p = plan([torrent(1, private=False)])
    assert p.statuses[0].hnr == HNR_NONE
    assert stopped(p, REASON_NOT_REQUIRED) == [1]


def test_old_private_unknown_torrent_is_stopped():
    p = plan([torrent(1, private=True, age_hours=48)])
    assert stopped(p, REASON_NOT_REQUIRED) == [1]


def test_young_private_unknown_torrent_is_left_alone():
    p = plan([torrent(1, private=True, age_hours=3)])
    assert p.statuses[0].hnr == HNR_UNKNOWN
    assert p.stop == []


def test_unknown_torrents_untouched_while_hnr_list_incomplete():
    p = plan([torrent(1, private=False)], hnr="incomplete")
    assert p.statuses[0].hnr == HNR_UNKNOWN
    assert p.stop == []


def test_unavailable_hnr_list_means_no_action_and_alert():
    torrents = [torrent(1), torrent(2, private=False)]
    p = plan(torrents, refs(1), hnr="missing")
    assert p.stop == []
    assert [s.hnr for s in p.statuses] == [HNR_UNKNOWN, HNR_UNKNOWN]
    assert ALERT_HNR_UNAVAILABLE in alert_kinds(p)


def test_tracker_field_identifies_ncore_without_ref():
    young = torrent(1, private=False, age_hours=2, tracker="https://t.ncore.sh/x")
    old = torrent(2, private=False, age_hours=48, tracker="https://t.ncore.sh/x")
    p = plan([young, old])
    assert [s.seed_tracker for s in p.statuses] == ["ncore", "ncore"]
    assert p.statuses[0].hnr == HNR_PENDING
    assert p.statuses[1].hnr == HNR_DONE
    assert stopped(p, REASON_FULFILLED) == [2]


def test_tracker_field_match_untouched_while_list_incomplete():
    p = plan([torrent(1, tracker="ncore")], hnr="incomplete")
    assert p.statuses[0].hnr == HNR_UNKNOWN
    assert p.stop == []


def test_downloading_torrent_is_never_stopped():
    torrents = [torrent(n, state="downloading", finished=False) for n in range(1, 4)]
    p = plan(torrents, refs(1, 2, 3))
    assert p.stop == []


def test_already_stopped_torrent_is_left_alone():
    p = plan([torrent(1, state="cached", active=False)], refs(1))
    assert p.stop == []


# --- A tegnapi helyzet: 9 aktív, legalább 2 hely kell -----------------------


def test_nine_active_frees_slots_without_touching_pending():
    pending_ids = [1, 2, 3, 4, 5, 6]
    torrents = [torrent(n) for n in pending_ids]
    torrents.append(torrent(7))  # Boxi: nCore, teljesítve
    torrents.append(torrent(8, private=False))  # nyilvános
    torrents.append(torrent(9, private=False))  # nyilvános
    p = plan(torrents, refs(*pending_ids, 7), pending=pending_ids)
    assert stopped(p) == [7, 8, 9]
    assert p.occupied_before == 9
    assert p.free_after == 4
    assert ALERT_SLOTS not in alert_kinds(p)


def test_full_slots_emergency_stops_the_one_closest_to_done():
    torrents = [torrent(n) for n in range(1, 10)]  # 9 aktív → 1 szabad
    remaining = {n: 40 for n in range(1, 10)} | {4: 3, 7: 10}
    p = plan(torrents, refs(*range(1, 10)), pending=range(1, 10), remaining=remaining)
    assert stopped(p, REASON_EMERGENCY) == [4]  # 3 óra van hátra: ez a legrövidebb
    assert p.free_after == 2
    assert alert_kinds(p) == [ALERT_EMERGENCY]
    alert = p.alerts[0]
    assert alert.torrent_id == 4
    assert alert.link == "https://ncore.pro/torrents.php?action=details&id=4"
    assert "hátra: 3 ó 0 p" in alert.message
    assert "fejezd be máshol" in alert.message


def test_full_slots_emergency_stops_as_many_as_needed():
    torrents = [torrent(n) for n in range(1, 11)]  # 10 aktív → 0 szabad
    p = plan(
        torrents,
        refs(*range(1, 11)),
        pending=range(1, 11),
        remaining={n: n for n in range(1, 11)},
    )
    assert stopped(p, REASON_EMERGENCY) == [1, 2]
    assert p.free_after == 2


def test_emergency_without_remaining_time_picks_oldest_and_young_last():
    torrents = [
        torrent(1, age_hours=30),
        torrent(2, age_hours=80),  # a legrégebbi
        torrent(3, age_hours=2),  # friss: még nincs is az nCore listáján
    ] + [torrent(n, age_hours=40) for n in range(4, 10)]
    pending = [1, 2] + list(range(4, 10))
    p = plan(torrents, refs(*range(1, 10)), pending=pending)
    assert stopped(p, REASON_EMERGENCY) == [2]


def test_emergency_disabled_only_alerts():
    settings = PlannerSettings(
        slot_limit=10,
        min_free_slots=2,
        min_seed=datetime.timedelta(hours=24),
        emergency_stop=False,
    )
    torrents = [torrent(n) for n in range(1, 10)]
    p = plan(torrents, refs(*range(1, 10)), pending=range(1, 10), settings=settings)
    assert p.stop == []
    assert p.free_after == 1
    assert alert_kinds(p) == [ALERT_SLOTS]
    assert "9 seedkötelezett torrent seedel" in p.alerts[0].message


def test_downloads_in_progress_are_never_emergency_stopped():
    torrents = [torrent(n, state="downloading", finished=False) for n in range(1, 10)]
    p = plan(torrents, refs(*range(1, 10)), pending=range(1, 10))
    assert p.stop == []
    assert alert_kinds(p) == [ALERT_SLOTS]


def test_emergency_stopped_torrent_gets_reminders_under_same_key():
    torrents = [torrent(1, state="stopped seeding", active=False)]
    state = {1: StateEntry(REASON_EMERGENCY, NOW - datetime.timedelta(hours=1))}
    p = plan(torrents, refs(1), pending=[1], state=state, remaining={1: 5})
    assert [(a.kind, a.key) for a in p.alerts] == [(ALERT_EMERGENCY, "emergency:1")]
    assert "még nincs befejezve" in p.alerts[0].message
    assert "hátra: 5 ó 0 p" in p.alerts[0].message


def test_emergency_alert_only_when_the_stop_happened():
    torrents = [torrent(n) for n in range(1, 10)]
    p = plan(torrents, refs(*range(1, 10)), pending=range(1, 10))
    target = stopped(p, REASON_EMERGENCY)[0]
    assert notifiable_alerts(p, stopped_now=set()) == []  # próbaüzem vagy hiba
    assert [a.torrent_id for a in notifiable_alerts(p, {target})] == [target]
    due, _ = due_alerts(
        notifiable_alerts(p, {target}), {}, NOW, datetime.timedelta(hours=12)
    )
    assert [a.kind for a in due] == [ALERT_EMERGENCY]  # azonnal szól


# --- Figyelmeztetések --------------------------------------------------------


def test_pending_torrent_not_seeding_raises_alert():
    p = plan([torrent(1, state="cached", active=False)], refs(1), pending=[1])
    assert alert_kinds(p) == [ALERT_NOT_SEEDING]
    assert "ncore #1" in p.alerts[0].message


def test_pending_stalled_torrent_raises_alert():
    p = plan(
        [torrent(1, state="stalled (no seeds)", finished=False)], refs(1), pending=[1]
    )
    assert alert_kinds(p) == [ALERT_STALLED]


def test_pending_download_in_progress_is_not_alerted():
    p = plan([torrent(1, state="downloading", finished=False)], refs(1), pending=[1])
    assert p.alerts == []


def test_alert_needs_to_persist_and_repeats_later():
    alert = Alert(key="not_seeding:1", kind=ALERT_NOT_SEEDING, message="x")
    repeat = datetime.timedelta(hours=12)

    due, records = due_alerts([alert], {}, NOW, repeat)
    assert due == []  # első észlelés: még nem szól

    later = NOW + datetime.timedelta(minutes=20)
    due, records = due_alerts([alert], records, later, repeat)
    assert due == [alert]
    records = mark_sent(records, due, later)

    due, records = due_alerts(
        [alert], records, later + datetime.timedelta(hours=1), repeat
    )
    assert due == []  # nemrég ment értesítés

    due, _ = due_alerts([alert], records, later + datetime.timedelta(hours=13), repeat)
    assert due == [alert]


def test_resolved_alert_is_forgotten():
    records = {"slots": AlertRecord(first_seen=NOW)}
    due, updated = due_alerts([], records, NOW, datetime.timedelta(hours=12))
    assert due == [] and updated == {}


def test_notification_text_and_send():
    alerts = [
        Alert(key="a", kind=ALERT_SLOTS, message="kevés a hely"),
        Alert(key="b", kind=ALERT_NOT_SEEDING, message="nem seedel"),
    ]
    title, message = notification_text(alerts)
    assert title == "TorBox-seed: 2 figyelmeztetés"
    assert "• kevés a hely" in message and "• nem seedel" in message

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    ok = asyncio.run(
        send_ntfy("https://ntfy.test", "tema", alerts, httpx.MockTransport(handler))
    )
    assert ok
    assert seen["body"]["topic"] == "tema"
    assert seen["body"]["title"] == title
    assert "click" not in seen["body"]

    linked = [Alert(key="e", kind=ALERT_EMERGENCY, message="m", link="https://x/1")]
    assert asyncio.run(
        send_ntfy("https://ntfy.test", "tema", linked, httpx.MockTransport(handler))
    )
    assert seen["body"]["click"] == "https://x/1"

    def failing(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert not asyncio.run(
        send_ntfy("https://ntfy.test", "tema", alerts, httpx.MockTransport(failing))
    )


# --- Állapot és jelentés ------------------------------------------------------


def test_prune_state_drops_missing_and_legacy_entries():
    torrents = [torrent(1), torrent(2), torrent(3)]
    state = {
        1: StateEntry(REASON_FULFILLED, NOW),
        2: StateEntry("slot", NOW),  # régi változat ideiglenes szünete
        3: StateEntry(REASON_NOT_REQUIRED, NOW),
        99: StateEntry(REASON_FULFILLED, NOW),  # eltűnt a TorBoxról
    }
    assert sorted(prune_state(state, torrents)) == [1, 3]


def test_state_and_alert_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = {7: StateEntry(REASON_FULFILLED, NOW)}
    save_state(path, state)
    assert load_state(path) == state
    assert load_state(tmp_path / "nincs.json") == {}

    alerts_path = tmp_path / "alerts.json"
    records = {
        "a": AlertRecord(first_seen=NOW),
        "b": AlertRecord(first_seen=NOW, last_sent=NOW),
    }
    save_alert_records(alerts_path, records)
    assert load_alert_records(alerts_path) == records


@pytest.mark.parametrize(
    "state,active,expected",
    [
        ("uploading (no peers)", True, True),
        ("Uploading", True, True),
        ("downloading", True, True),
        # A TorBox felülete az elakadt, nem aktív torrentet nem számolja.
        ("stalled (no seeds)", False, False),
        ("uploading (no peers)", False, False),
        ("stopped seeding", True, False),
        ("expired", True, False),
        ("paused", True, False),
        ("cached", False, False),
        ("completed", True, True),
    ],
)
def test_torbox_state_variants(state, active, expected):
    assert occupies_slot(torrent(1, state=state, active=active)) is expected


def test_state_labels():
    assert state_label("uploading (no peers)") == "seedel"
    assert state_label("stopped seeding") == "seed vége"
    assert state_label("expired") == "lejárt"
    assert state_label("valami új") == "valami új"


def test_report_shows_relevant_rows_and_warnings():
    torrents = [
        torrent(1),  # seedel, folyamatban
        torrent(2),  # teljesítve → leállítjuk
        torrent(3, state="cached", active=False),  # folyamatban, de nem seedel
        torrent(4, state="expired", active=False, private=False),
    ]
    p = plan(torrents, refs(1, 2, 3), pending=[1, 3])
    status = build_status(
        p, {}, SETTINGS, NOW, dry_run=False, warnings=[a.message for a in p.alerts]
    )

    short = format_status(status, NOW)
    assert "Szabad hely: 9/10 (cél: legalább 2)" in short
    assert "Seedelni csak ezeket kell: ncore" in short
    assert "film-1" in short
    assert "seed leállítva (Hit'n'Run teljesítve)" in short
    assert "film-3" in short  # seedelni kellene → mindig látszik
    assert "film-4" not in short
    assert "További 1 torrent" in short
    assert "⚠️  Seedelni kellene, de a TorBox nem seedeli" in short

    full = format_status(status, NOW, show_all=True)
    assert "film-4" in full


def test_report_marks_dry_run_and_tracker_field_matches():
    p = plan([torrent(1, private=False, tracker="ncore")])
    status = build_status(p, {}, SETTINGS, NOW, dry_run=True, warnings=[])
    text = format_status(status, NOW)
    assert "(PRÓBAÜZEM)" in text
    assert "leállítanám (Hit'n'Run teljesítve)" in text
    assert "ncore (tracker)" in text
    assert "Még nincs állapotjelentés" in format_status(None, NOW)


def test_report_shows_earlier_stop_as_note():
    torrents = [torrent(1, state="cached", active=False)]
    state = {1: StateEntry(REASON_NOT_REQUIRED, NOW - datetime.timedelta(hours=2))}
    p = plan(torrents, state=state)
    status = build_status(p, state, SETTINGS, NOW, dry_run=False, warnings=[])
    assert "seed leállítva (nem kell seedelni, 2 ó 0 p)" in format_status(
        status, NOW, show_all=True
    )


# --- Azonosítás a Hit'n'Run-lista alapján ------------------------------------


def test_match_refs_by_hash_then_by_name():
    torrents = [
        torrent(1),
        torrent(2, name="The.Breadwinner.2026.720p.HUN"),
        torrent(3),
    ]
    known = refs(3)
    by_hash = {h(1): TrackerTorrentRef("ncore", "101")}
    by_name = {"the.breadwinner.2026.720p.hun": TrackerTorrentRef("ncore", "102")}
    _match_refs(torrents, known, (by_hash, by_name))
    assert known[h(1)].torrent_id == "101"
    assert known[h(2)].torrent_id == "102"
    assert known[h(3)].torrent_id == "3"  # a meglévőt nem írja felül


# --- Segédfüggvények, beállítások, kliens -----------------------------------


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
            "tracker": "https://T.NCORE.SH:2810/announce",
        }
    )
    assert t.id == 42 and t.hash == h(1) and t.created_at is not None
    assert "ncore" in t.tracker

    listed = parse_torbox_torrent({"id": 1, "trackers": ["udp://a", "http://ncore.x"]})
    assert "ncore" in listed.tracker
    assert parse_torbox_torrent({"id": 2}).tracker == ""


def test_seed_tracker_list_parsing():
    settings = TorboxSeedSettings(seed_trackers=" nCore, ,ncore,foo ")
    assert settings.seed_tracker_list == ("ncore", "foo")


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
    "method,operation",
    [("stop_seeding", "stop_seeding"), ("resume_torrent", "resume")],
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


# --- A Hit'n'Run-lista elemeinek azonosítása (letöltéssel) -------------------


class _FakeInfo:
    def __init__(self, name: str):
        self.name = name


class _FakeTorrentFile:
    def __init__(self, n: int, name: str):
        self.info_hash = h(n)
        self.info = _FakeInfo(name)


class _FakeSource:
    def __init__(self, n: int, name: str):
        self.torrent_file = _FakeTorrentFile(n, name)


def test_resolve_hnr_ids_downloads_missing_with_limit(monkeypatch):
    import contextlib

    from fastapi import HTTPException

    from app.modules.torbox_seed import background_tasks as bt

    calls: list[str] = []

    class FakeProvider:
        async def find_one_by_indexer(self, indexer_id: str, torrent_id: str):
            calls.append(torrent_id)
            if torrent_id == "404":
                raise HTTPException(status_code=404)
            if torrent_id == "500":
                raise RuntimeError("nCore nem válaszol")
            return _FakeSource(int(torrent_id), f"Film.{torrent_id}")

    known_maps = ({h(1): TrackerTorrentRef("ncore", "1")}, {})
    monkeypatch.setattr(
        bt,
        "_lookup_known",
        lambda _indexer, _ids: (known_maps, ["2", "404", "500", "7"]),
    )
    monkeypatch.setattr(bt, "db_session", contextlib.nullcontext)
    monkeypatch.setattr(
        bt, "create_torrent_source_provider_service", lambda _db: FakeProvider()
    )
    bt._absent_ids.clear()

    maps, unresolved = asyncio.run(
        bt._resolve_hnr_ids("ncore", {"1", "2", "404", "500", "7"}, 3, NOW)
    )
    by_hash, by_name = maps
    assert calls == ["2", "404", "500"]  # a limit miatt a „7” a következő körre marad
    assert by_hash[h(2)].torrent_id == "2"
    assert by_name["film.2"].torrent_id == "2"
    assert unresolved == 2  # „500” (átmeneti hiba) + „7” (limit)
    assert ("ncore", "404") in bt._absent_ids

    calls.clear()
    _, unresolved = asyncio.run(
        bt._resolve_hnr_ids("ncore", {"1", "2", "404", "500", "7"}, 10, NOW)
    )
    assert "404" not in calls  # a törölt torrentet egy napig nem kérdezi újra
    assert unresolved == 1
    bt._absent_ids.clear()


def test_report_shows_remaining_and_emergency_action():
    torrents = [torrent(n) for n in range(1, 10)]
    remaining = {n: 40 for n in range(1, 10)} | {4: 3}
    p = plan(torrents, refs(*range(1, 10)), pending=range(1, 10), remaining=remaining)
    status = build_status(p, {}, SETTINGS, NOW, dry_run=False, warnings=[])
    text = format_status(status, NOW)
    assert "seed leállítva (helyhiány – fejezd be máshol); hátra: 3 ó 0 p" in text
    assert "hátra: 40 ó 0 p" in text


# --- Az nCore Hit'n'Run-oldala ------------------------------------------------


def test_parse_ncore_hit_and_run_page():
    from app.modules.indexer_definitions.integrations.ncore import (
        parse_hit_and_run_page,
    )

    def row(torrent_id: int, *cells: str) -> str:
        link = f'<a href="torrents.php?action=details&amp;id={torrent_id}">x</a>'
        tail = "".join(f"<div>{cell}</div>" for cell in cells)
        return f'<div class="hnr_all"><div class="hnr_tname">{link}</div>{tail}</div>'

    html = (
        '<div id="main_tartalom"><div class="lista">'
        + row(
            4241578, "2 napja", "1 perce", "Leech", "26.81 GiB", "10ó&nbsp;36p", "0.001"
        )
        + row(1339943, "3 órája", "24 perce", "Seed", "829.18 MiB", "45ó 19p", "0.000")
        + row(7, "1 napja", "2 perce", "Seed", "47p")
        + row(8, "1 napja", "2 perce", "Seed", "-")
        + "</div></div>"
    )
    entries = parse_hit_and_run_page(html, "https://ncore.pro")
    assert entries == {
        "4241578": datetime.timedelta(hours=10, minutes=36),
        "1339943": datetime.timedelta(hours=45, minutes=19),
        "7": datetime.timedelta(minutes=47),
        "8": None,
    }


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1n 2ó 5p", datetime.timedelta(days=1, hours=2, minutes=5)),
        ("5ó 47p", datetime.timedelta(hours=5, minutes=47)),
        ("2 napja 24 perce 3 órája", None),
        ("0.001 23.21 MiB", None),
    ],
)
def test_parse_remaining(text, expected):
    from app.modules.indexer_definitions.integrations.ncore import parse_remaining

    assert parse_remaining(text) == expected
