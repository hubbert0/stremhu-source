"""A Stremio → Nuvio átköltöztető tesztje hamis Stremio- és Nuvio-API-val."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "stremio_nuvio.py"
spec = importlib.util.spec_from_file_location("stremio_nuvio", SCRIPT)
assert spec and spec.loader
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)

ADDONS = [
    {
        "transportUrl": "https://v3-cinemeta.strem.io/manifest.json",
        "manifest": {"id": "com.linvo.cinemeta", "name": "Cinemeta"},
        "flags": {"official": True, "protected": True},
    },
    {
        "transportUrl": "http://127.0.0.1:11470/local-addon/manifest.json",
        "manifest": {"id": "org.stremio.local", "name": "Local Files"},
        "flags": {"official": True},
    },
    {
        "transportUrl": "https://aiostreams.pelda.ts.net/stremio/u/titok/manifest.json",
        "manifest": {"id": "aiostreams", "name": "AIOStreams"},
    },
    {
        "transportUrl": "https://tmdb.pelda.ts.net/hu/manifest.json",
        "manifest": {"id": "tmdb-addon", "name": "The Movie Database"},
    },
    {
        "transportUrl": "https://emby.pelda.ts.net/TitkosKulcs/manifest.json",
        "manifest": {"id": "hu.stremhu.emby", "name": "Emby-könyvtár"},
    },
]

LIBRARY = [
    {
        "_id": "tt0110357",
        "name": "Az oroszlánkirály",
        "type": "movie",
        "poster": "https://kep/1.jpg",
        "_ctime": "2026-10-01T10:00:00.000Z",
        "_mtime": "2026-10-02T10:00:00.000Z",
        "state": {
            "lastWatched": "2026-10-02T20:00:00.000Z",
            "timesWatched": 1,
            "watched": True,
            "timeOffset": 0,
        },
    },
    {
        "_id": "tt7678620",
        "name": "Bluey",
        "type": "series",
        "_ctime": "2026-10-03T10:00:00.000Z",
        "state": {
            "lastWatched": "2026-10-04T18:00:00.000Z",
            "timeOffset": 300,
            "duration": 420,
            "video_id": "tt7678620:2:5",
            "timesWatched": 0,
        },
    },
    {
        "_id": "tt0000001",
        "name": "Ideiglenes",
        "type": "movie",
        "temp": True,
        "state": {},
    },
    {
        "_id": "tt0000002",
        "name": "Eltávolított",
        "type": "movie",
        "removed": True,
        "state": {"timesWatched": 2, "lastWatched": "2026-09-01T10:00:00Z"},
    },
    {"_id": "yt_id:abc", "name": "YouTube-csatorna", "type": "channel", "state": {}},
]


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, str]]] = []
        self.nuvio_account = True
        self.nuvio_existing: list[dict[str, Any]] = [
            {
                "url": "https://v3-cinemeta.strem.io/manifest.json",
                "name": "Cinemeta",
                "enabled": True,
            },
            {
                "url": "https://opensubtitles-v3.strem.io/manifest.json",
                "name": "OpenSubtitles v3",
                "enabled": False,
            },
        ]
        self.signup_token: str | None = "uj-token"
        self.fail_rpc: str | None = None

    def __call__(
        self,
        url: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = 60,
    ) -> Any:
        headers = headers or {}
        self.calls.append((url, json.loads(json.dumps(body)), headers))
        if url == f"{sn.STREMIO_API}/api/login":
            if body["password"] != "stremio-jelszo":
                return {"error": {"message": "Wrong passphrase"}}
            return {"result": {"authKey": "stremio-kulcs"}}
        if url == f"{sn.STREMIO_API}/api/addonCollectionGet":
            assert body["authKey"] == "stremio-kulcs"
            return {"result": {"addons": ADDONS}}
        if url == f"{sn.STREMIO_API}/api/datastoreGet":
            return {"result": LIBRARY}
        assert headers.get("apikey") == sn.NUVIO_ANON_KEY
        if url.startswith(f"{sn.NUVIO_API}/auth/v1/token"):
            if not self.nuvio_account or body["password"] != "nuvio-jelszo":
                raise sn.ApiError("HTTP 400 – Invalid login credentials", 400)
            return {"access_token": "nuvio-token"}
        if url == f"{sn.NUVIO_API}/auth/v1/signup":
            return (
                {"access_token": self.signup_token}
                if self.signup_token
                else {"id": "u1"}
            )
        token = headers.get("Authorization")
        assert token in ("Bearer nuvio-token", "Bearer uj-token")
        if url.startswith(f"{sn.NUVIO_API}/rest/v1/addons?"):
            assert "profile_id=eq.1" in url
            return self.nuvio_existing
        if url.startswith(f"{sn.NUVIO_API}/rest/v1/rpc/"):
            name = url.rsplit("/", 1)[-1]
            if name == self.fail_rpc:
                raise sn.ApiError("HTTP 500", 500)
            if name == "sync_pull_library":
                return []
            return None
        raise AssertionError(f"váratlan hívás: {url}")

    def rpc(self, name: str) -> list[Any]:
        return [body for url, body, _ in self.calls if url.endswith(f"/rpc/{name}")]


def run(
    monkeypatch: pytest.MonkeyPatch,
    api: FakeApi,
    answers: list[str],
    passwords: list[str],
    argv: list[str] | None = None,
) -> int:
    monkeypatch.setattr(sn, "http_json", api)
    answer_iter = iter(answers)
    password_iter = iter(passwords)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answer_iter))
    monkeypatch.setattr(sn.getpass, "getpass", lambda prompt="": next(password_iter))
    return sn.main(argv or [])


def test_full_migration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    code = run(
        monkeypatch,
        api,
        ["csalad@pelda.hu", "csalad@pelda.hu", "i"],
        ["stremio-jelszo", "nuvio-jelszo"],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Local Files  (kimarad: csak az adott gépen működik)" in out
    assert "stremio-jelszo" not in out and "nuvio-jelszo" not in out

    (addons,) = api.rpc("sync_push_addons")
    assert addons["p_profile_id"] == 1
    assert [(a["name"], a["sort_order"], a["enabled"]) for a in addons["p_addons"]] == [
        ("Emby-könyvtár", 0, True),
        ("Cinemeta", 1, True),
        ("AIOStreams", 2, True),
        ("The Movie Database", 3, True),
        ("OpenSubtitles v3", 4, False),
    ]

    (library,) = api.rpc("sync_push_library")
    assert [i["content_id"] for i in library["p_items"]] == ["tt0110357", "tt7678620"]
    first = library["p_items"][0]
    assert first["content_type"] == "movie"
    assert first["poster_shape"] == "POSTER"
    assert first["added_at"] == 1790848800000  # 2026-10-01T10:00:00Z

    (history,) = api.rpc("sync_push_watched_items")
    assert [
        (h["content_id"], h["season"], h["episode"]) for h in history["p_items"]
    ] == [
        ("tt0110357", None, None),
        ("tt0000002", None, None),
    ]

    (progress,) = api.rpc("sync_push_watch_progress")
    entry = progress["p_entries"][0]
    assert entry == {
        "content_id": "tt7678620",
        "content_type": "series",
        "video_id": "tt7678620:2:5",
        "season": 2,
        "episode": 5,
        "position": 300000,
        "duration": 420000,
        "last_watched": 1791136800000,
        "progress_key": "tt7678620_s2e5",
    }


def test_dry_run_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    code = run(
        monkeypatch,
        api,
        ["a@b.hu", "a@b.hu"],
        ["stremio-jelszo", "nuvio-jelszo"],
        ["--proba"],
    )
    assert code == 0
    assert "Próbaüzem" in capsys.readouterr().out
    assert not api.rpc("sync_push_addons")
    assert not api.rpc("sync_push_library")


def test_declined_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi()
    assert (
        run(
            monkeypatch,
            api,
            ["a@b.hu", "a@b.hu", "n"],
            ["stremio-jelszo", "nuvio-jelszo"],
        )
        == 0
    )
    assert not api.rpc("sync_push_addons")


def test_wrong_stremio_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    assert run(monkeypatch, api, ["a@b.hu"], ["rossz"]) == 1
    assert "Wrong passphrase" in capsys.readouterr().out


def test_creates_nuvio_account(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi()
    api.nuvio_account = False
    code = run(
        monkeypatch,
        api,
        ["a@b.hu", "uj@b.hu", "i", "i"],
        ["stremio-jelszo", "uj-jelszo"],
    )
    assert code == 0
    assert any(url.endswith("/auth/v1/signup") for url, _, _ in api.calls)
    assert api.rpc("sync_push_addons")


def test_signup_needs_email_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    api.nuvio_account = False
    api.signup_token = None
    code = run(
        monkeypatch, api, ["a@b.hu", "uj@b.hu", "i"], ["stremio-jelszo", "uj-jelszo"]
    )
    assert code == 0
    assert "megerősítő e-mailt" in capsys.readouterr().out
    assert not api.rpc("sync_push_addons")


def test_failed_step_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    api.fail_rpc = "sync_push_library"
    code = run(
        monkeypatch, api, ["a@b.hu", "a@b.hu", "i"], ["stremio-jelszo", "nuvio-jelszo"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "✗ Könyvtár" in out and "✓ Kiegészítők" in out


def test_history_is_pushed_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi()
    many = [
        {"_id": f"tt{n}", "name": str(n), "type": "movie", "state": {"timesWatched": 1}}
        for n in range(1, sn.CHUNK + 3)
    ]
    monkeypatch.setattr(sn, "stremio_library", lambda key: many)
    run(monkeypatch, api, ["a@b.hu", "a@b.hu", "i"], ["stremio-jelszo", "nuvio-jelszo"])
    sizes = [len(body["p_items"]) for body in api.rpc("sync_push_watched_items")]
    assert sizes == [sn.CHUNK, 2]


def test_helpers() -> None:
    assert sn.season_episode("tt1:0:3") == (None, 3)
    assert sn.season_episode("tt1") == (None, None)
    assert sn.to_ms("2026-01-01T00:00:00Z") == 1767225600000
    assert sn.to_ms("rossz", "2026-01-01T00:00:00.000Z") == 1767225600000
