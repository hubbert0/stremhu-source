from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.exceptions import setup_exception_handlers
from app.modules.torbox_seed.config import TorboxSeedSettings
from app.modules.torbox_upload.router import get_torbox_upload_service, router
from app.modules.torbox_upload.service import (
    TorboxUploadService,
    find_magnet,
    looks_like_torrent,
)
from app.modules.users.dependencies import get_users_service

TORRENT = b"d8:announce30:https://tracker.example/announce4:infod4:name8:Film.mkv6:lengthi1ee6:pieces0:ee"
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Film"


class FakeUser:
    id = "user-id"
    role_id = "user"


class FakeUsersService:
    def find_by_id(self, user_id: str):
        return FakeUser() if user_id == "user-id" else None


class TorboxFake:
    def __init__(self) -> None:
        self.created: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer torbox-kulcs"
        if request.url.path.endswith("/createtorrent"):
            self.created.append(request)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "detail": "Torrent added successfully",
                    "data": {"torrent_id": 7, "name": "Film", "hash": "abc"},
                },
            )
        if request.url.path.endswith("/mylist"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "name": "Régi",
                            "download_state": "uploading",
                            "progress": 1,
                            "download_finished": True,
                            "size": 10,
                            "created_at": "2026-11-01T10:00:00Z",
                        },
                        {
                            "name": "Új",
                            "download_state": "downloading",
                            "progress": 0.5,
                            "download_finished": False,
                            "size": 20,
                            "created_at": "2026-11-09T10:00:00Z",
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"success": False, "detail": "nincs"})


@pytest.fixture
def torbox() -> TorboxFake:
    return TorboxFake()


def make_client(torbox: TorboxFake, api_key: str | None = "torbox-kulcs") -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.add_middleware(SessionMiddleware, secret_key="teszt")
    app.include_router(router, prefix="/api")

    @app.get("/teszt-belepes")
    def login(request: Request) -> dict[str, bool]:
        request.session["user_id"] = "user-id"
        return {"ok": True}

    settings = TorboxSeedSettings(api_key=api_key)
    app.dependency_overrides[get_users_service] = FakeUsersService
    app.dependency_overrides[get_torbox_upload_service] = lambda: TorboxUploadService(
        settings, transport=httpx.MockTransport(torbox)
    )
    return TestClient(app)


@pytest.fixture
def client(torbox: TorboxFake) -> Iterator[TestClient]:
    test_client = make_client(torbox)
    test_client.get("/teszt-belepes")
    yield test_client


def test_helpers() -> None:
    assert looks_like_torrent(TORRENT)
    assert not looks_like_torrent(b"<html>nem torrent</html>")
    assert find_magnet(None, f"Nézd meg: {MAGNET} köszi") == MAGNET
    assert find_magnet("magnet:?xt=urn:btih:rovid") is None


def test_requires_login(torbox: TorboxFake) -> None:
    response = make_client(torbox).post("/api/torbox/torrents", data={"magnet": MAGNET})
    assert response.status_code == 401
    assert torbox.created == []


def test_add_torrent_file(client: TestClient, torbox: TorboxFake) -> None:
    response = client.post(
        "/api/torbox/torrents",
        files={"file": ("Film.torrent", TORRENT, "application/x-bittorrent")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "name": "Film",
        "hash": "abc",
        "message": "Torrent added successfully",
    }
    body = torbox.created[0].content
    assert b'filename="Film.torrent"' in body and b"4:info" in body
    assert b'name="seed"' in body


def test_add_magnet(client: TestClient, torbox: TorboxFake) -> None:
    response = client.post("/api/torbox/torrents", data={"magnet": f"  {MAGNET}  "})
    assert response.status_code == 200
    body = torbox.created[0].content
    assert b'name="magnet"' in body and MAGNET.encode() in body
    assert torbox.created[0].headers["content-type"].startswith("multipart/form-data")


def test_rejects_invalid_input(client: TestClient, torbox: TorboxFake) -> None:
    bad_file = client.post(
        "/api/torbox/torrents", files={"file": ("x.torrent", b"<html>", "text/html")}
    )
    assert bad_file.status_code == 400
    assert "nem érvényes" in bad_file.json()["detail"]
    empty = client.post("/api/torbox/torrents", data={"magnet": "valami"})
    assert empty.status_code == 400
    assert torbox.created == []


def test_disabled_without_key(torbox: TorboxFake) -> None:
    test_client = make_client(torbox, api_key=None)
    test_client.get("/teszt-belepes")
    assert test_client.get("/api/torbox/status").json() == {
        "enabled": False,
        "error": None,
        "torrents": [],
    }
    response = test_client.post("/api/torbox/torrents", data={"magnet": MAGNET})
    assert response.status_code == 400
    assert "TORBOX_API_KEY" in response.json()["detail"]


def test_status_lists_newest_first(client: TestClient) -> None:
    body = client.get("/api/torbox/status").json()
    assert body["enabled"] is True
    assert [t["name"] for t in body["torrents"]] == ["Új", "Régi"]
    assert body["torrents"][0]["createdAt"].startswith("2026-11-09")


def test_share_target_redirects(client: TestClient, torbox: TorboxFake) -> None:
    response = client.post(
        "/api/torbox/share",
        data={"text": f"Megosztva: {MAGNET}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/torbox?ok=Film"
    shared_file = client.post(
        "/api/torbox/share",
        files={"torrent": ("Film.torrent", TORRENT, "application/x-bittorrent")},
        follow_redirects=False,
    )
    assert shared_file.headers["location"] == "/torbox?ok=Film"
    assert len(torbox.created) == 2


def test_share_target_without_login(torbox: TorboxFake) -> None:
    response = make_client(torbox).post(
        "/api/torbox/share", data={"text": MAGNET}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/torbox?hiba=")
    assert torbox.created == []
