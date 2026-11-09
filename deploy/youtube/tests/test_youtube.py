import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_CHANNELS, Settings, parse_channels
from app.main import create_app
from app.matching import episode_name, episode_numbers, norm, title_score
from app.youtube import parse_duration

CHANNEL = "UCtesztcsatorna0000000000"
UPLOADS = "UUtesztcsatorna0000000000"
PLAYLIST = "PLmagyarnepmesek"

VIDEOS = {
    # azonosító: (cím, hossz)
    "vidKoleves1": ("Magyar népmesék: A kőleves", "PT6M14S"),
    "vidNyul0001": ("Magyar népmesék: Nyúlpásztor", "PT8M1S"),
    "vidVukFilm1": ("Vuk – teljes film", "PT1H18M40S"),
    "vidVukTrlr1": ("Vuk – előzetes", "PT2M10S"),
    "vidShort001": ("Vuk #shorts", "PT40S"),
}


def youtube_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path.rsplit("/", 1)[-1]
    params = request.url.params
    assert params.get("key") == "yt-kulcs"
    if path == "channels":
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": CHANNEL,
                        "snippet": {"title": "Teszt Mesék", "thumbnails": {}},
                        "contentDetails": {"relatedPlaylists": {"uploads": UPLOADS}},
                    }
                ]
            },
        )
    if path == "playlistItems":
        ids = (
            list(VIDEOS)
            if params["playlistId"] == UPLOADS
            else ["vidNyul0001", "vidKoleves1"]
        )
        items = [
            {
                "snippet": {
                    "title": VIDEOS[vid][0],
                    "position": pos,
                    "thumbnails": {
                        "high": {"url": f"https://i.ytimg.com/vi/{vid}/hq.jpg"}
                    },
                },
                "contentDetails": {
                    "videoId": vid,
                    "videoPublishedAt": "2020-01-01T00:00:00Z",
                },
            }
            for pos, vid in enumerate(ids)
        ]
        items.append(
            {"snippet": {"title": "Deleted video"}, "contentDetails": {"videoId": "x"}}
        )
        return httpx.Response(200, json={"items": items})
    if path == "videos":
        ids = params["id"].split(",")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": vid,
                        "contentDetails": {"duration": VIDEOS[vid][1]},
                        "status": {"privacyStatus": "public", "embeddable": True},
                    }
                    for vid in ids
                    if vid in VIDEOS
                ]
            },
        )
    if path == "playlists":
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": PLAYLIST,
                        "snippet": {"title": "Magyar népmesék", "thumbnails": {}},
                        "contentDetails": {"itemCount": 2},
                    }
                ]
            },
        )
    return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})


def tmdb_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path.replace("/3", "", 1)
    if path == "/find/tt0083295":
        return httpx.Response(
            200, json={"movie_results": [{"id": 34584}], "tv_results": []}
        )
    if path == "/find/tt0215400":
        return httpx.Response(
            200, json={"movie_results": [], "tv_results": [{"id": 30903}]}
        )
    if path == "/movie/34584":
        return httpx.Response(
            200,
            json={
                "title": "Vuk",
                "original_title": "Vuk",
                "runtime": 79,
                "alternative_titles": {"titles": []},
            },
        )
    if path == "/tv/30903":
        return httpx.Response(
            200,
            json={
                "name": "Magyar népmesék",
                "original_name": "Magyar népmesék",
                "alternative_titles": {"results": []},
                "seasons": [
                    {"season_number": 1, "episode_count": 13},
                    {"season_number": 2, "episode_count": 13},
                ],
            },
        )
    if path == "/tv/30903/season/1":
        return httpx.Response(
            200,
            json={
                "episodes": [
                    {"episode_number": 5, "name": "A kőleves"},
                    {"episode_number": 6, "name": "6. epizód"},
                ]
            },
        )
    if path == "/tv/30903/season/2":
        return httpx.Response(
            200, json={"episodes": [{"episode_number": 1, "name": "2. epizód"}]}
        )
    return httpx.Response(404, json={})


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        base_url="https://youtube.example.ts.net",
        data_dir=tmp_path,
        youtube_api_key="yt-kulcs",
        tmdb_api_key="tmdb-kulcs",
        channels=(CHANNEL,),
    )
    app = create_app(
        settings,
        youtube_transport=httpx.MockTransport(youtube_handler),
        tmdb_transport=httpx.MockTransport(tmdb_handler),
        start_refresh=False,
    )
    with TestClient(app) as test_client:
        done = test_client.portal.call(app.state.indexer.refresh, settings.channels, 0)
        assert done == 1
        yield test_client


def test_parse_channels() -> None:
    assert parse_channels("") == DEFAULT_CHANNELS
    assert parse_channels("alap,@valami") == (*DEFAULT_CHANNELS, "@valami")
    assert parse_channels("UCx, hibas, @y") == ("UCx", "@y")


def test_helpers() -> None:
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("P1D") == 86400
    assert parse_duration("ismeretlen") == 0
    assert norm("Magyar népmesék: A kőleves!") == "magyar nepmesek a koleves"
    assert title_score("vuk teljes film", ["vuk"]) == 1.0
    assert title_score("vukovar", ["vuk"]) == 0.0
    assert episode_name("6. epizód") == ""
    assert episode_name("A kőleves") == "a koleves"
    assert episode_numbers(norm("Mekk Elek S01E06")) == ({(1, 6)}, set())
    assert 5 in episode_numbers(norm("Pom Pom 5. rész"))[1]


def test_health_and_manifest(client: TestClient) -> None:
    health = client.get("/health").json()
    assert health["channels"] == 1 and health["videos"] == 5
    manifest = client.get("/manifest.json").json()
    assert manifest["id"] == "hu.stremhu.youtube-mesek"
    assert manifest["catalogs"][0]["extra"][0]["options"] == ["Teszt Mesék"]
    assert "https://youtube.example.ts.net/manifest.json" in client.get("/").text


def test_movie_stream_skips_trailer_and_shorts(client: TestClient) -> None:
    streams = client.get("/stream/movie/tt0083295.json").json()["streams"]
    assert [s["ytId"] for s in streams] == ["vidVukFilm1"]
    assert "1:18:40" in streams[0]["title"]


def test_series_stream_by_episode_title(client: TestClient) -> None:
    streams = client.get("/stream/series/tt0215400:1:5.json").json()["streams"]
    assert streams[0]["ytId"] == "vidKoleves1"


def test_series_stream_tmdb_id_and_playlist_order(client: TestClient) -> None:
    # Az 1. évad 1. részének nincs címe a TMDB-n: a lejátszási lista sorrendje dönt.
    streams = client.get("/stream/series/tmdb:30903:1:1.json").json()["streams"]
    assert streams[0]["ytId"] == "vidNyul0001"
    assert "sorrendje" in streams[0]["title"]
    # A 2. évad 1. része folyamatos sorszámmal a 14. lenne: nincs ilyen.
    assert client.get("/stream/series/tmdb:30903:2:1.json").json()["streams"] == []


def test_catalog_meta_and_playlist_stream(client: TestClient) -> None:
    metas = client.get("/catalog/series/ythu-mesek.json").json()["metas"]
    assert [m["id"] for m in metas] == [f"ytpl:{PLAYLIST}"]
    filtered = client.get(
        "/catalog/series/ythu-mesek/genre=Teszt%20Mes%C3%A9k.json"
    ).json()
    assert len(filtered["metas"]) == 1
    assert (
        client.get("/catalog/series/ythu-mesek/search=koleves.json").json()["metas"]
        == []
    )
    meta = client.get(f"/meta/series/ytpl:{PLAYLIST}.json").json()["meta"]
    assert [v["title"] for v in meta["videos"]] == [
        "Magyar népmesék: Nyúlpásztor",
        "Magyar népmesék: A kőleves",
    ]
    stream = client.get(f"/stream/series/ytpl:{PLAYLIST}:1:2.json").json()["streams"]
    assert stream[0]["ytId"] == "vidKoleves1"
    assert (
        client.get(f"/stream/series/ytpl:{PLAYLIST}:1:9.json").json()["streams"] == []
    )


def test_unknown_ids_are_empty(client: TestClient) -> None:
    assert client.get("/stream/movie/tt9999999.json").json()["streams"] == []
    assert client.get("/meta/series/ytpl:ismeretlen.json").json()["meta"] is None
    assert json.loads(client.get("/catalog/movie/egyeb.json").text)["metas"] == []
