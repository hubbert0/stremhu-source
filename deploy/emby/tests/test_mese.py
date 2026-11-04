"""A Mesék sorok tesztjei egy szimulált TMDB-vel (httpx.MockTransport)."""

import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_emby import BASE, SECRET, FakeEmby, get

from app.config import Settings, load_servers, load_settings
from app.main import create_app
from app.mese import discover_params

RAW_PER_KIND = 140  # 7 TMDB-oldal


def has_imdb(tmdb_id: int) -> bool:
    return tmdb_id % 10 != 0


def has_poster(tmdb_id: int) -> bool:
    return tmdb_id % 17 != 0


class FakeTmdb:
    def __init__(self) -> None:
        self.discover: list[dict[str, str]] = []
        self.lookups: list[str] = []
        self.headers: list[httpx.Headers] = []
        self.down = False
        self.status: int | None = None
        self.limit_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.headers.append(request.headers)
        if self.down:
            raise httpx.ConnectError("nincs kapcsolat", request=request)
        if self.status:
            return httpx.Response(self.status)
        if self.limit_once:
            self.limit_once = False
            return httpx.Response(429, headers={"Retry-After": "0"})
        if path == "/3/configuration":
            return httpx.Response(200, json={"images": {}})
        if path.startswith("/3/discover/"):
            kind = path.rsplit("/", 1)[-1]
            self.discover.append({"kind": kind, **params})
            page = int(params["page"])
            offset = 1000 if kind == "tv" else 0
            results = []
            for number in range((page - 1) * 20 + 1, page * 20 + 1):
                if number > RAW_PER_KIND:
                    break
                tmdb_id = offset + number
                result: dict[str, Any] = {
                    "id": tmdb_id,
                    "poster_path": f"/p{tmdb_id}.jpg" if has_poster(tmdb_id) else None,
                    "backdrop_path": f"/b{tmdb_id}.jpg",
                    "overview": f"Leírás {tmdb_id}",
                }
                if kind == "movie":
                    result.update(
                        {"title": f"Mese {tmdb_id}", "release_date": "2019-11-20"}
                    )
                else:
                    result.update(
                        {"name": f"Sorozat {tmdb_id}", "first_air_date": "2018-10-01"}
                    )
                results.append(result)
            return httpx.Response(
                200,
                json={
                    "page": page,
                    "results": results,
                    "total_pages": 7,
                    "total_results": RAW_PER_KIND,
                },
            )
        if path.endswith("/external_ids"):
            tmdb_id = int(path.split("/")[3])
            self.lookups.append(path)
            if tmdb_id == 13:
                return httpx.Response(404)
            imdb = f"tt{9000000 + tmdb_id}" if has_imdb(tmdb_id) else None
            return httpx.Response(200, json={"id": tmdb_id, "imdb_id": imdb})
        return httpx.Response(404)


@pytest.fixture
def tmdb() -> FakeTmdb:
    return FakeTmdb()


@pytest.fixture
def emby_servers() -> dict[int, FakeEmby]:
    return {1: FakeEmby()}


def make_client(
    tmp_path: Path,
    tmdb: FakeTmdb,
    key: str = "v3kulcs123",
    embys: dict[int, FakeEmby] | None = None,
) -> TestClient:
    env = (
        {
            "EMBY1_NEV": "Saját",
            "EMBY1_URL": "https://sajat.example.com",
            "EMBY1_FELHASZNALO": "stremio",
            "EMBY1_JELSZO": "jelszo",
        }
        if embys
        else {}
    )
    settings = Settings(
        base_url=BASE,
        secret=SECRET,
        data_dir=tmp_path,
        servers=load_servers(env),
        tmdb_api_key=key,
    )
    app = create_app(
        settings,
        (lambda index: httpx.MockTransport(embys[index].handler)) if embys else None,
        httpx.MockTransport(tmdb.handler),
    )
    return TestClient(app)


def catalog(client: TestClient, kind: str, extra: str = "") -> list[dict[str, Any]]:
    path = f"/catalog/{kind}/mese-{kind}" + (f"/{extra}" if extra else "") + ".json"
    return get(client, path)["metas"]


def expected_ids(offset: int) -> list[str]:
    return [
        f"tt{9000000 + offset + n}"
        for n in range(1, RAW_PER_KIND + 1)
        if has_imdb(offset + n) and has_poster(offset + n) and offset + n != 13
    ]


# ------------------------------------------------------------------ alapok


def test_settings_read_tmdb_key() -> None:
    assert load_settings({"TMDB_API_KEY": " abc "}).tmdb_api_key == "abc"
    assert load_settings({}).tmdb_api_key == ""


def test_no_key_no_mese(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb, key="") as client:
        manifest = get(client, "/manifest.json")
        assert not [c for c in manifest["catalogs"] if c["id"].startswith("mese-")]
        assert catalog(client, "movie") == []
        assert client.get("/health").json()["mese"] is False
    assert tmdb.discover == []


def test_manifest_lists_mese_first(
    tmp_path: Path, tmdb: FakeTmdb, emby_servers: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, tmdb, embys=emby_servers) as client:
        manifest = get(client, "/manifest.json")
        assert client.get("/health").json()["mese"] is True
    ids = [(c["id"], c["type"], c["name"]) for c in manifest["catalogs"]]
    assert ids[:2] == [
        ("mese-movie", "movie", "Mesék"),
        ("mese-series", "series", "Mesék"),
    ]
    assert ids[2][0].startswith("emby1-")
    movie = manifest["catalogs"][0]
    genre = next(e for e in movie["extra"] if e["name"] == "genre")
    assert genre["options"][0] == "Népszerű"
    assert "Kicsiknek" in genre["options"] and "Magyar" in genre["options"]
    assert movie["extraSupported"] == ["genre", "skip"]
    assert "Mesék" in manifest["description"]


def test_httpx_request_logging_is_quiet() -> None:
    # A TMDB-kulcs a kérés címében van – nem kerülhet a naplóba.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


# --------------------------------------------------------------- katalógus


def test_movie_catalog_first_page(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb) as client:
        metas = catalog(client, "movie")
    assert [m["id"] for m in metas] == expected_ids(0)[:100]
    first = metas[0]
    assert first == {
        "id": "tt9000001",
        "type": "movie",
        "name": "Mese 1",
        "poster": "https://image.tmdb.org/t/p/w342/p1.jpg",
        "posterShape": "poster",
        "background": "https://image.tmdb.org/t/p/w1280/b1.jpg",
        "releaseInfo": "2019",
        "description": "Leírás 1",
    }
    params = tmdb.discover[0]
    assert params["kind"] == "movie"
    assert params["with_genres"] == "16,10751"
    assert params["certification_country"] == "US"
    assert params["certification.gte"] == "G"
    assert params["certification.lte"] == "PG"
    assert params["language"] == "hu-HU"
    assert params["include_adult"] == "false"
    assert params["api_key"] == "v3kulcs123"
    assert "27" in params["without_genres"].split(",")
    # Csak annyi oldalt kér le, amennyi a 100 elemhez kell.
    assert sorted({int(p["page"]) for p in tmdb.discover}) == [1, 2, 3, 4, 5, 6]


def test_series_catalog_and_second_page(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb) as client:
        first = catalog(client, "series")
        second = catalog(client, "series", "skip=100")
    expected = expected_ids(1000)
    assert [m["id"] for m in first] == expected[:100]
    assert [m["id"] for m in second] == expected[100:]
    assert first[0]["name"] == "Sorozat 1001"
    assert first[0]["releaseInfo"] == "2018"
    params = tmdb.discover[0]
    assert params["kind"] == "tv"
    assert params["with_genres"] == "16,10762"
    assert "certification.lte" not in params


def test_filter_options(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb) as client:
        catalog(client, "movie", "genre=Kicsiknek")
        kicsi = tmdb.discover[-1]
        catalog(client, "movie", "genre=Magyar")
        magyar = tmdb.discover[-1]
        catalog(client, "series", "genre=Legjobbak")
        legjobb = tmdb.discover[-1]
        catalog(client, "movie", "genre=Nincs%20ilyen")
        default = tmdb.discover[-1]
    assert (kicsi["certification.gte"], kicsi["certification.lte"]) == ("G", "G")
    assert magyar["with_origin_country"] == "HU"
    assert magyar["with_genres"] == "16"
    assert "certification.lte" not in magyar
    assert "certification.gte" not in magyar
    assert "vote_count.gte" not in magyar
    assert legjobb["sort_by"] == "vote_average.desc"
    assert default["certification.lte"] == "PG"
    assert default["sort_by"] == "popularity.desc"


def test_newest_is_limited_to_today() -> None:
    today = datetime.date(2026, 11, 4)
    assert (
        discover_params("movie", "Legújabb", today)["primary_release_date.lte"]
        == "2026-11-04"
    )
    assert (
        discover_params("series", "Legújabb", today)["first_air_date.lte"]
        == "2026-11-04"
    )


def test_wrong_type_returns_nothing(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb) as client:
        assert get(client, "/catalog/series/mese-movie.json")["metas"] == []
    assert tmdb.discover == []


def test_catalog_is_cached(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb) as client:
        catalog(client, "movie")
        calls, lookups = len(tmdb.discover), len(tmdb.lookups)
        catalog(client, "movie")
        assert len(tmdb.discover) == calls
        assert len(tmdb.lookups) == lookups
        # Másik szűrő: új lista, de az IMDb-azonosítók már megvannak.
        catalog(client, "movie", "genre=Legjobbak")
        assert len(tmdb.discover) > calls
        assert len(tmdb.lookups) == lookups


def test_keeps_last_list_when_tmdb_down(tmp_path: Path, tmdb: FakeTmdb) -> None:
    with make_client(tmp_path, tmdb) as client:
        assert catalog(client, "movie", "genre=Legjobbak")
        assert catalog(client, "series", "genre=Legjobbak")
    tmdb.down = True
    # Új indítás, a TMDB-oldalak gyorsítótárát kiürítjük.
    db = sqlite3.connect(tmp_path / "cache.sqlite")
    db.execute("DELETE FROM cache WHERE key LIKE 'mese:%'")
    db.commit()
    db.close()
    with make_client(tmp_path, tmdb) as client:
        assert [m["id"] for m in catalog(client, "movie", "genre=Legjobbak")] == (
            expected_ids(0)[:100]
        )
        assert catalog(client, "movie", "genre=Magyar") == []


def test_invalid_key_returns_empty(tmp_path: Path, tmdb: FakeTmdb) -> None:
    tmdb.status = 401
    with make_client(tmp_path, tmdb) as client:
        assert catalog(client, "movie") == []


def test_rate_limit_is_retried(tmp_path: Path, tmdb: FakeTmdb) -> None:
    tmdb.limit_once = True
    with make_client(tmp_path, tmdb) as client:
        assert len(catalog(client, "movie")) == 100


def test_v4_token_uses_bearer_header(tmp_path: Path, tmdb: FakeTmdb) -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.teszt.alairas"
    with make_client(tmp_path, tmdb, key=token) as client:
        catalog(client, "movie")
    assert "api_key" not in tmdb.discover[0]
    assert tmdb.headers[0]["authorization"] == f"Bearer {token}"
