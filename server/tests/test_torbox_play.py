import asyncio
import warnings
from collections.abc import Iterator
from typing import Any

import httpx
import libtorrent as lt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import api_router
from app.common.torrent_info import parse_torrent_info
from app.exceptions import setup_exception_handlers
from app.modules.auth.dependencies import get_auth_service
from app.modules.indexer_definitions.base_indexer_definition import IndexerClient
from app.modules.indexer_definitions.integrations.ncore import NcoreIndexerDefinition
from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionSearchHit,
    IndexerDefinitionTorrent,
)
from app.modules.settings.dependencies import get_settings_service
from app.modules.torbox_play import stremio as torbox_play
from app.modules.torbox_play.service import TorboxPlayService, video_files
from app.modules.torbox_seed.config import TorboxSeedSettings

API_KEY = "9f8f3c2a-0b1d-4f5e-8a7b-6c5d4e3f2a1b"
APP_URL = "https://stremhu.local"
PACK = "X-Faktor.S14.HUN.1080p.WEB-DL"
FILES = [
    (f"{PACK}/X-Faktor.S14E01.HUN.1080p.mkv", 900_000_000),
    (f"{PACK}/X-Faktor.S14E02.HUN.1080p.mkv", 950_000_000),
    (f"{PACK}/Sample/x-faktor-sample.mkv", 20_000_000),
    (f"{PACK}/info.nfo", 2_000),
]


def make_torrent(files: list[tuple[str, int]]) -> bytes:
    storage = lt.file_storage()
    for name, size in files:
        storage.add_file(name, size)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        torrent = lt.create_torrent(
            storage, 4 * 1024 * 1024, getattr(lt.create_torrent, "v1_only", 0)
        )
    for piece in range(torrent.num_pieces()):
        torrent.set_hash(piece, b"\x01" * 20)
    return lt.bencode(torrent.generate())


TORRENT = make_torrent(FILES)
INFO_HASH = parse_torrent_info(TORRENT).info_hash


class FakeNcore:
    def __init__(self) -> None:
        self.searches: list[str] = []

    async def search_by_name(self, query: str) -> list[IndexerDefinitionSearchHit]:
        self.searches.append(query)
        return [
            IndexerDefinitionSearchHit(
                torrent_id="111", name=PACK, category="hdser_hun", size=1_870_000_000
            ),
            IndexerDefinitionSearchHit(
                torrent_id="222", name="X-Faktor.Film", category="hd_hun", imdb_id="tt1"
            ),
            IndexerDefinitionSearchHit(
                torrent_id="333", name="Valami.XXX", category="xxx_hd"
            ),
        ]

    async def find_torrent_by_id(self, torrent_id: str) -> IndexerDefinitionTorrent:
        return IndexerDefinitionTorrent(
            torrent_id=torrent_id,
            download_url=f"https://ncore.pro/torrents.php?action=download&id={torrent_id}",
        )

    async def download_torrent(self, download_url: str) -> bytes:
        return TORRENT


class FakeDefinitions:
    def __init__(self) -> None:
        self.ncore = FakeNcore()

    def get_by_id(self, indexer_id: str) -> FakeNcore:
        assert indexer_id == "ncore"
        return self.ncore


class FakeTorbox:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.created = 0

    def finish(self) -> None:
        self.items[0].update(
            download_finished=True,
            download_present=True,
            progress=1,
            download_state="uploading",
            files=[
                {
                    "id": 0,
                    "name": f"{PACK}/X-Faktor.S14E01.HUN.1080p.mkv",
                    "size": 900_000_000,
                },
                {
                    "id": 1,
                    "name": f"{PACK}/X-Faktor.S14E02.HUN.1080p.mkv",
                    "size": 950_000_000,
                },
            ],
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/mylist"):
            return httpx.Response(200, json={"success": True, "data": self.items})
        if path.endswith("/createtorrent"):
            self.created += 1
            self.items.append(
                {
                    "id": 77,
                    "hash": INFO_HASH,
                    "name": PACK,
                    "download_finished": False,
                    "progress": 0.42,
                    "download_state": "downloading",
                    "files": [],
                }
            )
            return httpx.Response(
                200, json={"success": True, "data": {"torrent_id": 77}}
            )
        if path.endswith("/requestdl"):
            assert request.url.params["torrent_id"] == "77"
            file_id = request.url.params["file_id"]
            return httpx.Response(
                200,
                json={"success": True, "data": f"https://cdn.torbox.app/{file_id}.mkv"},
            )
        return httpx.Response(404, json={"success": False, "detail": "nincs"})


@pytest.fixture
def torbox() -> FakeTorbox:
    return FakeTorbox()


@pytest.fixture
def service(torbox: FakeTorbox) -> TorboxPlayService:
    return TorboxPlayService(
        TorboxSeedSettings(api_key="torbox-kulcs"),
        FakeDefinitions(),  # type: ignore[arg-type]
        transport=httpx.MockTransport(torbox),
    )


class FakeUser:
    id = "user-id"
    api_key = API_KEY


class FakeAuthService:
    def verify_api_key(self, api_key: str | None, allowed_roles=None) -> FakeUser:
        assert api_key == API_KEY
        return FakeUser()


class FakeSettingsService:
    def get_app_url(self) -> str:
        return APP_URL


@pytest.fixture
def client(service: TorboxPlayService) -> Iterator[TestClient]:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(api_router)
    app.dependency_overrides[get_auth_service] = FakeAuthService
    app.dependency_overrides[get_settings_service] = FakeSettingsService
    app.dependency_overrides[torbox_play.get_torbox_play_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def base(path: str) -> str:
    return f"/api/{API_KEY}/ncore{path}"


def test_ncore_search_by_name_parses_json() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "torrent_id": "4000001",
                        "release_name": "X-Faktor.S14E11.HUN.WEB-DL.1080p.H264-LEGION",
                        "category": "hdser_hun",
                        "size": "7408000000",
                        "seeders": "12",
                        "leechers": "3",
                        "imdb_id": "",
                        "download_url": "https://ncore.pro/torrents.php?action=download&id=4000001&key=x",
                    }
                ],
                "total_results": "1",
                "perpage": "25",
            },
        )

    definition = NcoreIndexerDefinition()
    definition._client = IndexerClient(  # pyright: ignore[reportPrivateUsage]
        definition=definition,
        base_url=definition.url,
        transport=httpx.MockTransport(handler),
    )
    hits = asyncio.run(definition.search_by_name("X-Faktor S14E11"))
    assert len(requests) == 1
    assert requests[0].url.params["miben"] == "name"
    assert requests[0].url.params["mire"] == "X-Faktor S14E11"
    assert hits[0].name.startswith("X-Faktor.S14E11")
    assert hits[0].size == 7_408_000_000 and hits[0].seeders == 12
    assert hits[0].imdb_id is None


def test_video_files_skip_samples_and_number_episodes() -> None:
    files = video_files(parse_torrent_info(TORRENT))
    assert [(f.season, f.episode) for f in files] == [(14, 1), (14, 2)]
    assert all("sample" not in f.name for f in files)


def test_manifest(client: TestClient) -> None:
    manifest = client.get(base("/manifest.json")).json()
    assert manifest["id"] == "hu.stremhu-source.ncore-torbox"
    assert manifest["idPrefixes"] == ["stremhu-tb:"]
    assert [(c["id"], c["type"]) for c in manifest["catalogs"]] == [
        ("ncore-kereses", "movie"),
        ("ncore-kereses", "series"),
    ]
    assert manifest["catalogs"][0]["extra"][0]["isRequired"] is True


def test_foreign_ids_are_ignored(client: TestClient) -> None:
    assert client.get(base("/stream/movie/tt0111161.json")).json() == {"streams": []}
    assert client.get(base("/meta/movie/tt0111161.json")).json() == {"meta": {}}


def test_catalog_filters_by_type_and_skips_adult(client: TestClient) -> None:
    series = client.get(base("/catalog/series/ncore-kereses/search=X-Faktor.json"))
    assert [m["id"] for m in series.json()["metas"]] == ["stremhu-tb:ncore:111"]
    movies = client.get(base("/catalog/movie/ncore-kereses/search=X-Faktor.json"))
    metas = movies.json()["metas"]
    assert [m["id"] for m in metas] == ["stremhu-tb:ncore:222"]
    assert metas[0]["poster"].endswith("/tt1/img")


def test_series_meta_has_episodes(client: TestClient) -> None:
    meta = client.get(base("/meta/series/stremhu-tb:ncore:111.json")).json()["meta"]
    assert meta["name"] == PACK
    assert [(v["season"], v["episode"]) for v in meta["videos"]] == [(14, 1), (14, 2)]
    assert meta["videos"][1]["id"] == "stremhu-tb:ncore:111:1"


def test_stream_uploads_then_waits_then_plays(
    client: TestClient, torbox: FakeTorbox
) -> None:
    waiting = client.get(base("/stream/series/stremhu-tb:ncore:111:1.json")).json()
    assert torbox.created == 1
    assert waiting["streams"][0]["name"] == "⏳ TorBox tölti: 42%"
    assert waiting["streams"][0]["url"] == f"{APP_URL}/api/{API_KEY}/ncore/varj.mp4"

    # Újra megnyitva nem tölti fel még egyszer.
    client.get(base("/stream/series/stremhu-tb:ncore:111:1.json"))
    assert torbox.created == 1

    torbox.finish()
    ready = client.get(base("/stream/series/stremhu-tb:ncore:111:1.json")).json()
    stream = ready["streams"][0]
    assert stream["name"] == "▶️ TorBox"
    assert stream["url"] == f"{APP_URL}/api/{API_KEY}/ncore/play/111/1"

    played = client.get(base("/play/111/1"), follow_redirects=False)
    assert played.status_code == 302
    assert played.headers["location"] == "https://cdn.torbox.app/1.mkv"


def test_movie_stream_picks_largest_file(
    client: TestClient, torbox: FakeTorbox
) -> None:
    client.get(base("/stream/movie/stremhu-tb:ncore:222.json"))
    torbox.finish()
    stream = client.get(base("/stream/movie/stremhu-tb:ncore:222.json")).json()
    assert stream["streams"][0]["url"].endswith("/ncore/play/222/1")


def test_play_before_finished_is_503(client: TestClient) -> None:
    client.get(base("/stream/movie/stremhu-tb:ncore:222.json"))
    response = client.get(base("/play/222/1"), follow_redirects=False)
    assert response.status_code == 503


def test_wait_video_is_served(client: TestClient) -> None:
    response = client.get(base("/varj.mp4"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert len(response.content) > 10_000
