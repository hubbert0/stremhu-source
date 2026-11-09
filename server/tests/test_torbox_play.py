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
PACK = "X-Faktor.S14.HUN.1080p.WEB-DL.H264"
PACK_720 = "X-Faktor.S14.HUN.720p.WEB-DL.H264"
FILM = "X-Faktor.Film.2020.1080p.BluRay.x264.DTS.5.1"
FILM_SUB = "X-Faktor.Film.2020.720p.WEB-DL.HUNSUB"


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


def series_files(pack: str, quality: str) -> list[tuple[str, int]]:
    return [
        (f"{pack}/X-Faktor.S14E01.HUN.{quality}.mkv", 900_000_000),
        (f"{pack}/X-Faktor.S14E02.HUN.{quality}.mkv", 950_000_000),
        (f"{pack}/Sample/x-faktor-sample.mkv", 20_000_000),
        (f"{pack}/info.nfo", 2_000),
    ]


TORRENTS = {
    "111": make_torrent(series_files(PACK, "1080p")),
    "112": make_torrent(series_files(PACK_720, "720p")),
    "222": make_torrent(
        [(f"{FILM}/film.nfo", 1_000), (f"{FILM}/{FILM}.mkv", 8_000_000_000)]
    ),
    "223": make_torrent([(f"{FILM_SUB}/{FILM_SUB}.mkv", 2_000_000_000)]),
}
INFOS = {tid: parse_torrent_info(data) for tid, data in TORRENTS.items()}
TORRENT = TORRENTS["111"]


FILM_NFO = """
\u2588\u2588   Bitrate........: 15 900 Kbps   \u2588\u2588
\u2588\u2588     AUDIO 1                         AUDIO 2          \u2588\u2588
\u2588\u2588   Language.......: Hungarian      Language.......: English  \u2588\u2588
\u2588\u2588   Channels.......: 6 (5.1)        Channels.......: 8 (7.1)  \u2588\u2588
\u2588\u2588   Subtitles......: Hungarian Forced & Full   \u2588\u2588
"""


class FakeNcore:
    def __init__(self) -> None:
        self.searches: list[str] = []
        self.downloads: list[str] = []
        self.nfo_requests: list[str] = []

    async def fetch_nfo(self, torrent_id: str) -> str:
        self.nfo_requests.append(torrent_id)
        if torrent_id == "223":
            raise RuntimeError("nCore nem válaszol")
        return FILM_NFO if torrent_id == "222" else "csak egy logó"

    async def search_by_name(self, query: str) -> list[IndexerDefinitionSearchHit]:
        self.searches.append(query)
        hit = IndexerDefinitionSearchHit
        return [
            hit(torrent_id="111", name=PACK, category="hdser_hun", seeders=20),
            hit(
                torrent_id="222", name=FILM, category="hd_hun", imdb_id="tt1", seeders=9
            ),
            hit(torrent_id="112", name=PACK_720, category="hdser_hun", seeders=5),
            hit(
                torrent_id="223", name=FILM_SUB, category="hd", imdb_id="tt1", seeders=3
            ),
            hit(torrent_id="333", name="Valami.XXX", category="xxx_hd"),
        ]

    async def find_torrent_by_id(self, torrent_id: str) -> IndexerDefinitionTorrent:
        return IndexerDefinitionTorrent(
            torrent_id=torrent_id,
            download_url=f"https://ncore.pro/torrents.php?action=download&id={torrent_id}",
        )

    async def download_torrent(self, download_url: str) -> bytes:
        torrent_id = download_url.rsplit("=", 1)[1]
        self.downloads.append(torrent_id)
        return TORRENTS[torrent_id]


class FakeDefinitions:
    def __init__(self) -> None:
        self.ncore = FakeNcore()

    def get_by_id(self, indexer_id: str) -> FakeNcore:
        assert indexer_id == "ncore"
        return self.ncore


class FakeTorbox:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.created: list[str] = []

    def finish(self, torrent_id: str) -> None:
        info = INFOS[torrent_id]
        for item in self.items:
            if item["hash"] == info.info_hash:
                item.update(
                    download_finished=True,
                    download_present=True,
                    progress=1,
                    download_state="uploading",
                    files=[
                        {"id": 100 + f.index, "name": f.path, "size": f.size}
                        for f in info.files
                    ],
                )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/mylist"):
            return httpx.Response(200, json={"success": True, "data": self.items})
        if path.endswith("/createtorrent"):
            body = request.read()
            torrent_id = next(tid for tid, data in TORRENTS.items() if data in body)
            self.created.append(torrent_id)
            self.items.append(
                {
                    "id": 70 + len(self.items),
                    "hash": INFOS[torrent_id].info_hash,
                    "name": INFOS[torrent_id].name,
                    "download_finished": False,
                    "progress": 0.42,
                    "download_state": "downloading",
                    "files": [],
                }
            )
            return httpx.Response(200, json={"success": True, "data": {}})
        if path.endswith("/requestdl"):
            file_id = request.url.params["file_id"]
            torbox_id = request.url.params["torrent_id"]
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": f"https://cdn.torbox.app/{torbox_id}/{file_id}.mkv",
                },
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


SERIES_ID = "stremhu-tb:ncore:111h_112h"
MOVIE_ID = "stremhu-tb:ncore:222h_223"


def search(client: TestClient, kind: str) -> list[dict[str, Any]]:
    response = client.get(base(f"/catalog/{kind}/ncore-kereses/search=X-Faktor.json"))
    return response.json()["metas"]


def test_catalog_merges_versions_into_one_tile(client: TestClient) -> None:
    series = search(client, "series")
    assert [m["id"] for m in series] == [SERIES_ID]
    assert series[0]["name"] == "X-Faktor S14"
    assert "2 verzió: 1080p, 720p" in series[0]["description"]
    assert "🇭🇺 magyar hang" in series[0]["description"]

    movies = search(client, "movie")
    assert [m["id"] for m in movies] == [MOVIE_ID]  # a felnőtt kategória kimarad
    assert movies[0]["name"] == "X-Faktor Film 2020"
    assert movies[0]["poster"].endswith("/tt1/img")
    assert "magyar és eredeti hang" in movies[0]["description"]
    assert "💬 magyar felirat" in movies[0]["description"]


def test_series_meta_lists_episodes_without_prior_search(client: TestClient) -> None:
    # Újraindítás után nincs keresési adat: a .torrent fájllistájából dolgozik.
    meta = client.get(base(f"/meta/series/{SERIES_ID}.json")).json()["meta"]
    assert meta["name"] == "X-Faktor S14"
    assert [(v["season"], v["episode"]) for v in meta["videos"]] == [(14, 1), (14, 2)]
    assert meta["videos"][1]["id"] == f"{SERIES_ID}:14:2"
    # Dátum nélkül a Nuvio nem jelöli „UNAVAILABLE”-nek.
    assert "released" not in meta["videos"][0]
    assert meta["videos"][0]["available"] is True


def test_stream_list_shows_details_and_does_not_upload(
    client: TestClient, torbox: FakeTorbox
) -> None:
    search(client, "series")
    streams = client.get(base(f"/stream/series/{SERIES_ID}:14:2.json")).json()[
        "streams"
    ]
    assert torbox.created == []
    assert [s["name"] for s in streams] == ["nCore ⬇️\n1080p", "nCore ⬇️\n720p"]
    first = streams[0]["description"]
    assert "🗣️ magyar hang" in first
    assert "🎞️ WEB-DL · H.264" in first
    assert "🌱 20" in first
    assert "Elindításkor feltölti a TorBoxra" in first
    assert first.endswith(f"📦 {PACK}")
    assert streams[0]["url"] == f"{APP_URL}/api/{API_KEY}/ncore/play/111/1"
    assert streams[1]["url"] == f"{APP_URL}/api/{API_KEY}/ncore/play/112/1"


def test_play_uploads_only_the_chosen_version(
    client: TestClient, torbox: FakeTorbox
) -> None:
    search(client, "series")
    wait = client.get(base("/play/112/1"), follow_redirects=False)
    assert wait.status_code == 302
    assert wait.headers["location"] == f"{APP_URL}/api/{API_KEY}/ncore/varj.mp4"
    assert torbox.created == ["112"]

    # Újra elindítva nem tölti fel még egyszer; a lista a töltést mutatja.
    client.get(base("/play/112/1"), follow_redirects=False)
    assert torbox.created == ["112"]
    streams = client.get(base(f"/stream/series/{SERIES_ID}:14:2.json")).json()[
        "streams"
    ]
    assert streams[0]["name"] == "nCore ⏳ 42%\n720p"
    assert "A TorBox tölti: 42%" in streams[0]["description"]

    torbox.finish("112")
    streams = client.get(base(f"/stream/series/{SERIES_ID}:14:2.json")).json()[
        "streams"
    ]
    assert [s["name"] for s in streams] == ["nCore ▶️\n720p", "nCore ⬇️\n1080p"]
    played = client.get(base("/play/112/1"), follow_redirects=False)
    assert played.status_code == 302
    assert played.headers["location"] == "https://cdn.torbox.app/70/101.mkv"
    assert torbox.created == ["112"]


def test_movie_streams_use_nfo_and_fall_back_to_name(
    client: TestClient, service: TorboxPlayService
) -> None:
    search(client, "movie")
    streams = client.get(base(f"/stream/movie/{MOVIE_ID}.json")).json()["streams"]
    assert [s["name"] for s in streams] == ["nCore ⬇️\n1080p", "nCore ⬇️\n720p"]
    assert streams[0]["url"].endswith("/ncore/play/222/1")
    # 222: az NFO-ból (minden hangsáv, felirat, bitráta).
    first = streams[0]["description"]
    assert "🗣️ magyar 5.1 · angol 7.1" in first
    assert "💬 magyar (kényszerített + teljes)" in first
    assert "🎞️ BluRay · H.264 · 15,9 Mbps" in first
    # 223: az NFO nem érhető el → a kiadás neve marad.
    second = streams[1]["description"]
    assert "🗣️ eredeti hang" in second
    assert "💬 magyar felirat" in second

    # Másodszorra a sikeres NFO-t nem kéri le újra.
    client.get(base(f"/stream/movie/{MOVIE_ID}.json"))
    requests = service._definitions.ncore.nfo_requests  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert requests.count("222") == 1
    assert requests.count("223") == 2


def test_legacy_ids_still_work(client: TestClient) -> None:
    streams = client.get(base("/stream/series/stremhu-tb:ncore:111:1.json")).json()[
        "streams"
    ]
    assert len(streams) == 1
    assert streams[0]["url"].endswith("/ncore/play/111/1")


def test_play_unknown_file_is_503(client: TestClient) -> None:
    response = client.get(base("/play/222/9"), follow_redirects=False)
    assert response.status_code == 503


def test_wait_video_is_served(client: TestClient) -> None:
    response = client.get(base("/varj.mp4"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert len(response.content) > 10_000
