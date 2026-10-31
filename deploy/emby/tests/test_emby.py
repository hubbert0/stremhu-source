"""Az Emby-könyvtár tesztjei egy szimulált Emby-szerverrel (httpx.MockTransport)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_servers
from app.main import create_app
from app.service import human_size, parse_extra, resolution

SECRET = "TitkosKulcs1234567890"
BASE = "https://emby.pelda.ts.net"

MOVIES = [
    {
        "Id": "101",
        "Type": "Movie",
        "View": "vfilm",
        "Name": "Dűne: Második rész",
        "ProductionYear": 2024,
        "ProviderIds": {"Imdb": "tt15239678", "Tmdb": "693134"},
        "ImageTags": {"Primary": "a"},
        "Genres": ["Sci-Fi"],
        "Overview": "Paul Atreides...",
    },
    {
        "Id": "102",
        "Type": "Movie",
        "View": "vfilm",
        "Name": "Csak TMDb",
        "ProductionYear": 2020,
        "Genres": ["Dráma"],
        "ProviderIds": {"Tmdb": "555"},
        "ImageTags": {},
    },
    {
        "Id": "103",
        "Type": "Video",
        "View": "vhazi",
        "Name": "Házi videó",
        "ProviderIds": {},
        "ImageTags": {"Primary": "b"},
    },
]
SERIES = [
    {
        "Id": "201",
        "Type": "Series",
        "View": "vsor",
        "Name": "Trónok harca",
        "ProductionYear": 2011,
        "ProviderIds": {"IMDB": "0944947"},
        "ImageTags": {"Primary": "c"},
        "BackdropImageTags": ["d"],
    }
]
ITEMS = MOVIES + SERIES
VIEWS = [
    {"Id": "vfilm", "Name": "Filmek", "CollectionType": "movies"},
    {"Id": "vsor", "Name": "Sorozatok", "CollectionType": "tvshows"},
    {"Id": "vhazi", "Name": "Házi videók", "CollectionType": "homevideos"},
    {"Id": "vzene", "Name": "Zene", "CollectionType": "music"},
    {"Id": "vures", "Name": "Üres", "CollectionType": "movies"},
]
EPISODES = [
    {
        "Id": "301",
        "Name": "Tél közeleg",
        "ParentIndexNumber": 1,
        "IndexNumber": 1,
        "ImageTags": {"Primary": "e"},
    },
    {"Id": "302", "Name": "Királyi út", "ParentIndexNumber": 1, "IndexNumber": 2},
    {
        "Id": "303",
        "Name": "Dupla rész",
        "ParentIndexNumber": 2,
        "IndexNumber": 3,
        "IndexNumberEnd": 4,
    },
]


def media_source(
    item_id: str, width: int, height: int, size: int, container: str = "mkv"
) -> dict[str, Any]:
    return {
        "Id": f"ms{item_id}-{height}",
        "Container": container,
        "Size": size,
        "Path": f"/media/{item_id}/Film.{height}p.{container}",
        "MediaStreams": [
            {
                "Type": "Video",
                "Codec": "hevc",
                "Width": width,
                "Height": height,
                "VideoRange": "HDR" if width > 3000 else "SDR",
            },
            {"Type": "Audio", "Codec": "eac3", "Language": "hun"},
            {"Type": "Audio", "Codec": "ac3", "Language": "eng"},
            {
                "Type": "Subtitle",
                "Index": 3,
                "Language": "hun",
                "IsExternal": True,
                "IsTextSubtitleStream": True,
            },
            {
                "Type": "Subtitle",
                "Index": 4,
                "Language": "eng",
                "IsExternal": False,
                "IsTextSubtitleStream": True,
            },
        ],
    }


class FakeEmby:
    """Egy Emby-szerver minimális utánzata a bővítmény által használt hívásokhoz."""

    def __init__(self, user: str = "stremio", password: str = "jelszo") -> None:
        self.user = user
        self.password = password
        self.tokens = {"token-1"}
        self.next_token = 1
        self.requests: list[str] = []
        self.down = False
        self.expire_once = False
        self.item_params: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if self.down:
            raise httpx.ConnectError("nincs kapcsolat", request=request)
        if path == "/Users/AuthenticateByName":
            body = json.loads(request.content)
            if body != {"Username": self.user, "Pw": self.password}:
                return httpx.Response(401)
            self.next_token += 1
            token = f"token-{self.next_token}"
            self.tokens.add(token)
            return httpx.Response(
                200, json={"AccessToken": token, "User": {"Id": "u1"}}
            )
        token = request.headers.get("X-Emby-Token", "")
        if token not in self.tokens or self.expire_once:
            self.expire_once = False
            self.tokens.discard(token)
            return httpx.Response(401)
        params = request.url.params
        if path == "/Users/u1/Views":
            return httpx.Response(200, json={"Items": VIEWS})
        if path == "/Genres":
            types = (params.get("IncludeItemTypes") or "").split(",")
            names = sorted(
                {
                    genre
                    for i in ITEMS
                    if i["Type"] in types and i["View"] == params.get("ParentId")
                    for genre in i.get("Genres", [])
                }
            )
            return httpx.Response(200, json={"Items": [{"Name": n} for n in names]})
        if path == "/Users/u1/Items":
            self.item_params.append(dict(params))
            types = (params.get("IncludeItemTypes") or "").split(",")
            items = [i for i in ITEMS if i["Type"] in types]
            if params.get("ParentId"):
                items = [i for i in items if i["View"] == params.get("ParentId")]
            if params.get("Genres"):
                items = [i for i in items if params["Genres"] in i.get("Genres", [])]
            if params.get("SortBy") == "SortName":
                items = sorted(items, key=lambda i: i["Name"])
            elif params.get("SortBy", "").startswith("ProductionYear"):
                items = sorted(items, key=lambda i: -i.get("ProductionYear", 0))
            if params.get("Limit") == "0":
                return httpx.Response(
                    200, json={"Items": [], "TotalRecordCount": len(items)}
                )
            wanted = params.get("AnyProviderIdEquals")
            if wanted:
                provider, value = wanted.split(".", 1)
                items = [
                    i
                    for i in items
                    if any(
                        k.lower() == provider
                        and str(v).replace("tt", "") == value.replace("tt", "")
                        for k, v in i["ProviderIds"].items()
                    )
                ]
            search = params.get("SearchTerm")
            if search:
                items = [i for i in items if search.lower() in i["Name"].lower()]
            start = int(params.get("StartIndex", "0"))
            limit = int(params.get("Limit", "100"))
            return httpx.Response(200, json={"Items": items[start : start + limit]})
        if path.startswith("/Users/u1/Items/"):
            item_id = path.rsplit("/", 1)[-1]
            for item in ITEMS:
                if item["Id"] == item_id:
                    return httpx.Response(200, json=item)
            return httpx.Response(404)
        if path == "/Shows/201/Episodes":
            return httpx.Response(200, json={"Items": EPISODES})
        if path.endswith("/PlaybackInfo"):
            item_id = path.split("/")[2]
            sources = [media_source(item_id, 1920, 800, 4 * 1024**3)]
            if item_id == "101":
                sources.append(media_source(item_id, 3840, 1600, 40 * 1024**3))
            return httpx.Response(200, json={"MediaSources": sources})
        if path.startswith("/Items/") and "/Images/" in path:
            return httpx.Response(
                200, content=b"\x89PNG kep", headers={"content-type": "image/png"}
            )
        return httpx.Response(404)


@pytest.fixture
def embys() -> dict[int, FakeEmby]:
    return {1: FakeEmby(), 2: FakeEmby(user="csalad", password="titok")}


def make_client(
    tmp_path: Path, embys: dict[int, FakeEmby], secret: str = SECRET
) -> TestClient:
    env = {
        "EMBY1_NEV": "Saját",
        "EMBY1_URL": "https://sajat.example.com/",
        "EMBY1_FELHASZNALO": "stremio",
        "EMBY1_JELSZO": "jelszo",
        "EMBY2_NEV": "Sógor",
        "EMBY2_URL": "http://sogor.example.com:8096",
        "EMBY2_FELHASZNALO": "csalad",
        "EMBY2_JELSZO": "titok",
    }
    settings = Settings(
        base_url=BASE, secret=secret, data_dir=tmp_path, servers=load_servers(env)
    )
    app = create_app(settings, lambda index: httpx.MockTransport(embys[index].handler))
    return TestClient(app)


def get(client: TestClient, path: str) -> Any:
    response = client.get(f"/{SECRET}{path}")
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------- alapok


def test_settings_skip_incomplete_servers() -> None:
    servers = load_servers(
        {
            "EMBY1_URL": "https://a.example.com",
            "EMBY1_FELHASZNALO": "x",
            "EMBY2_URL": "ftp://rossz",
            "EMBY2_FELHASZNALO": "y",
            "EMBY3_URL": "https://c.example.com",
        }
    )
    assert [(s.index, s.name) for s in servers] == [(1, "Emby 1")]


def test_helpers() -> None:
    assert parse_extra("search=d%C5%B1ne&skip=100") == {"search": "dűne", "skip": "100"}
    assert resolution({"Width": 1920, "Height": 800}) == ("1080p", 3)
    assert resolution({"Width": 3840, "Height": 1600}) == ("4K", 4)
    assert resolution({"Width": 1280, "Height": 536}) == ("720p", 2)
    assert human_size(4 * 1024**3) == "4.0 GB"


def test_secret_protects_everything(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        assert client.get("/health").json()["secret"] is True
        assert client.get("/manifest.json").status_code == 404
        assert client.get("/rossztitok12345678/manifest.json").status_code == 404
        assert (
            client.get("/rossztitok12345678/stream/movie/tt15239678.json").status_code
            == 404
        )
        assert SECRET not in client.get("/").text


def test_short_secret_disables_addon(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys, secret="rovid") as client:
        assert client.get("/rovid/manifest.json").status_code == 404
        assert client.get("/health").json()["secret"] is False


def test_manifest_lists_catalogs_per_server(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        manifest = get(client, "/manifest.json")
    assert [(c["name"], c["type"]) for c in manifest["catalogs"]] == [
        ("Saját: Filmek", "movie"),
        ("Saját: Sorozatok", "series"),
        ("Saját: Házi videók", "movie"),
        ("Sógor: Filmek", "movie"),
        ("Sógor: Sorozatok", "series"),
        ("Sógor: Házi videók", "movie"),
    ]
    first = manifest["catalogs"][0]
    assert first["id"] == "emby1-vfilm-movie"
    genre = next(e for e in first["extra"] if e["name"] == "genre")
    assert genre["options"] == ["Rendezés: ABC", "Rendezés: évszám", "Dráma", "Sci-Fi"]
    assert genre["isRequired"] is False


# --------------------------------------------------------------- katalógus


def test_catalog_uses_imdb_ids_and_proxied_posters(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        metas = get(client, "/catalog/movie/emby1-vfilm-movie.json")["metas"]
        home = get(client, "/catalog/movie/emby1-vhazi-movie.json")["metas"]
    assert [m["id"] for m in metas] == ["tt15239678", "tmdb:555"]
    assert [m["id"] for m in home] == ["emby:1:103"]
    assert metas[0]["poster"] == f"{BASE}/{SECRET}/kep/1/101.jpg"
    assert metas[0]["releaseInfo"] == "2024"
    assert "poster" not in metas[1]
    assert all("token" not in json.dumps(m) for m in metas)


def test_catalog_series_with_numeric_imdb(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        metas = get(client, "/catalog/series/emby2-series.json")["metas"]
    assert metas[0]["id"] == "tt0944947"
    assert metas[0]["type"] == "series"


def test_catalog_search_and_skip(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        found = get(client, "/catalog/movie/emby1-movie/search=d%C5%B1ne.json")["metas"]
        page2 = get(client, "/catalog/movie/emby1-vfilm-movie/skip=1.json")["metas"]
        wrong = get(client, "/catalog/series/emby1-movie.json")["metas"]
    assert [m["name"] for m in found] == ["Dűne: Második rész"]
    assert [m["id"] for m in page2] == ["tmdb:555"]
    assert wrong == []


def test_catalog_is_cached(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        get(client, "/catalog/movie/emby1-movie.json")
        before = len(embys[1].requests)
        get(client, "/catalog/movie/emby1-movie.json")
    assert len(embys[1].requests) == before


# ---------------------------------------------------------------- lejátszás


def test_movie_streams_from_both_servers_sorted(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        streams = get(client, "/stream/movie/tt15239678.json")["streams"]
    assert len(streams) == 4  # mindkét szerveren 4K és 1080p
    assert [s["name"].split("\n")[1] for s in streams] == ["4K", "4K", "1080p", "1080p"]
    assert "4K HEVC HDR" in streams[0]["title"]
    assert "🔊 hun, eng" in streams[0]["title"]
    first = streams[0]["url"]
    assert (
        "/Videos/101/stream.mkv?MediaSourceId=ms101-1600&Static=true&api_key=token-"
        in first
    )
    assert streams[0]["subtitles"][0]["lang"] == "hun"
    assert "/Subtitles/3/Stream.srt" in streams[0]["subtitles"][0]["url"]
    assert len(streams[0]["subtitles"]) == 1  # a beágyazottat a lejátszó magától látja
    names = {s["name"].split("\n")[0] for s in streams}
    assert names == {"Emby · Saját", "Emby · Sógor"}
    assert streams[-1]["behaviorHints"]["notWebReady"] is True


def test_tmdb_id_stream(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        streams = get(client, "/stream/movie/tmdb:555.json")["streams"]
    assert len(streams) == 2
    assert all("/Videos/102/" in s["url"] for s in streams)


def test_episode_streams(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        episode = get(client, "/stream/series/tt0944947:1:2.json")["streams"]
        double = get(client, "/stream/series/tt0944947:2:4.json")["streams"]
        missing = get(client, "/stream/series/tt0944947:9:9.json")["streams"]
    assert episode and all("/Videos/302/" in s["url"] for s in episode)
    assert double and all("/Videos/303/" in s["url"] for s in double)
    assert missing == []


def test_unknown_title_returns_nothing(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        assert get(client, "/stream/movie/tt0000001.json")["streams"] == []
        assert get(client, "/stream/movie/rossz-azonosito.json")["streams"] == []
        assert get(client, "/stream/channel/tt15239678.json")["streams"] == []


def test_one_server_down_other_still_works(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    embys[2].down = True
    with make_client(tmp_path, embys) as client:
        streams = get(client, "/stream/movie/tt15239678.json")["streams"]
        catalog = get(client, "/catalog/movie/emby2-movie.json")["metas"]
    assert len(streams) == 2
    assert all("Saját" in s["name"] for s in streams)
    assert catalog == []


def test_expired_token_relogin(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        get(client, "/catalog/movie/emby1-movie.json")
        embys[1].expire_once = True
        streams = get(client, "/stream/movie/tmdb:555.json")["streams"]
    assert any("sajat.example.com" in s["url"] for s in streams)
    assert embys[1].requests.count("/Users/AuthenticateByName") == 2


def test_wrong_password_is_isolated(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    embys[1].password = "mas"
    with make_client(tmp_path, embys) as client:
        streams = get(client, "/stream/movie/tt15239678.json")["streams"]
    assert {s["name"].split("\n")[0] for s in streams} == {"Emby · Sógor"}


# ------------------------------------------------------- adatlap és képek


def test_meta_for_items_without_imdb(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        movie = get(client, "/meta/movie/emby:1:103.json")["meta"]
        series = get(client, "/meta/series/emby:1:201.json")["meta"]
        none = get(client, "/meta/movie/tt15239678.json")["meta"]
        stream = get(client, "/stream/series/emby:1:301.json")["streams"]
    assert movie["name"] == "Házi videó"
    assert movie["id"] == "emby:1:103"
    assert series["background"] == f"{BASE}/{SECRET}/hatter/1/201.jpg"
    assert [(v["id"], v["season"], v["episode"]) for v in series["videos"]] == [
        ("emby:1:301", 1, 1),
        ("emby:1:302", 1, 2),
        ("emby:1:303", 2, 3),
    ]
    assert series["videos"][0]["thumbnail"] == f"{BASE}/{SECRET}/kep/1/301.jpg"
    assert none is None
    assert stream and "/Videos/301/" in stream[0]["url"]


def test_image_proxy_and_cache(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        first = client.get(f"/{SECRET}/kep/1/101.jpg")
        before = len(embys[1].requests)
        second = client.get(f"/{SECRET}/kep/1/101.jpg")
        bad = client.get(f"/{SECRET}/kep/1/..%2F..%2Fetc.jpg")
        unknown_server = client.get(f"/{SECRET}/kep/7/101.jpg")
    assert first.status_code == 200 and first.content == b"\x89PNG kep"
    assert first.headers["content-type"] == "image/png"
    assert second.content == first.content
    assert len(embys[1].requests) == before
    assert bad.status_code == 404
    assert unknown_server.status_code == 404


# ------------------------------------------------------------- könyvtárak


def test_genre_filter_and_sorting(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        drama = get(client, "/catalog/movie/emby1-vfilm-movie/genre=Dr%C3%A1ma.json")
        abc = get(
            client, "/catalog/movie/emby1-vfilm-movie/genre=Rendez%C3%A9s%3A%20ABC.json"
        )
        year = get(
            client,
            "/catalog/movie/emby1-vfilm-movie/genre=Rendez%C3%A9s%3A%20%C3%A9vsz%C3%A1m.json",
        )
    assert [m["name"] for m in drama["metas"]] == ["Csak TMDb"]
    assert [m["name"] for m in abc["metas"]] == ["Csak TMDb", "Dűne: Második rész"]
    assert [m["releaseInfo"] for m in year["metas"]] == ["2024", "2020"]
    sent = [p for p in embys[1].item_params if p.get("ParentId") == "vfilm"]
    assert {"Genres": "Dráma"}.items() <= sent[0].items()
    assert sent[1]["SortBy"] == "SortName"
    assert sent[2]["SortBy"] == "ProductionYear,SortName"


def test_series_library_catalog(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        metas = get(client, "/catalog/series/emby2-vsor-series.json")["metas"]
        wrong_type = get(client, "/catalog/movie/emby2-vsor-series.json")["metas"]
    assert [m["id"] for m in metas] == ["tt0944947"]
    assert wrong_type == []


def test_manifest_keeps_last_libraries_when_server_down(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    with make_client(tmp_path, embys) as client:
        get(client, "/manifest.json")
        service = client.app.state.service  # type: ignore[attr-defined]
        service.cache.delete("library:2")
        embys[2].down = True
        names = [c["name"] for c in get(client, "/manifest.json")["catalogs"]]
    assert "Sógor: Házi videók" in names


def test_manifest_falls_back_when_never_reached(
    tmp_path: Path, embys: dict[int, FakeEmby]
) -> None:
    embys[2].down = True
    with make_client(tmp_path, embys) as client:
        catalogs = get(client, "/manifest.json")["catalogs"]
        legacy = get(client, "/catalog/series/emby1-series.json")["metas"]
    sogor = [(c["id"], c["name"]) for c in catalogs if c["id"].startswith("emby2")]
    assert sogor == [
        ("emby2-movie", "Sógor: Filmek"),
        ("emby2-series", "Sógor: Sorozatok"),
    ]
    assert [m["id"] for m in legacy] == ["tt0944947"]


def test_library_list_is_cached(tmp_path: Path, embys: dict[int, FakeEmby]) -> None:
    with make_client(tmp_path, embys) as client:
        get(client, "/manifest.json")
        before = embys[1].requests.count("/Users/u1/Views")
        get(client, "/manifest.json")
    assert before == 1
    assert embys[1].requests.count("/Users/u1/Views") == 1
