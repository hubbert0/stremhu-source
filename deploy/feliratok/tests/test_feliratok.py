import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.feliratok import (
    parse_autoname,
    parse_movie_html,
    parse_series_results,
    release_from_name,
)
from app.main import create_app
from app.matching import pick_show, rank_series, release_score
from app.service import parse_extra, parse_stremio_id
from app.subfile import (
    SubtitleError,
    build_srt,
    choose_file,
    decode,
    episode_of,
    to_srt,
)

SRT_HU = "1\n00:00:01,000 --> 00:00:02,000\nÁrvíztűrő tükörfúrógép, őszintén.\n"

AUTONAME = [
    {"name": "Loki (2015)", "ID": "4100"},
    {"name": "Loki (2021)", "ID": "5001"},
    {"name": "Lokis Adventures (2021)", "ID": "5002"},
]

XBMC = {
    "1": {
        "language": "Magyar",
        "nev": "Loki - 1x02 (WEB.2160p-KOGi)",
        "fnev": "Loki.S01E02.2160p.WEB.H265-KOGi.srt",
        "felirat": "1700000002",
        "evad": "1",
        "ep": "2",
        "feltolto": "fordito",
        "pontos_talalat": "111",
        "evadpakk": "0",
    },
    "2": {
        "language": "Magyar",
        "nev": "Loki - 1x02 (WEB-DL.1080p-NTb)",
        "fnev": "Loki.S01E02.1080p.WEB-DL-NTb.srt",
        "felirat": "1700000003",
        "evad": "1",
        "ep": "2",
        "feltolto": "masik",
        "pontos_talalat": "011",
        "evadpakk": "0",
    },
    "3": {
        "language": "Angol",
        "nev": "Loki - 1x02 (WEB.2160p-KOGi)",
        "fnev": "Loki.S01E02.eng.srt",
        "felirat": "1700000004",
        "evad": "1",
        "ep": "2",
        "feltolto": "x",
        "pontos_talalat": "111",
        "evadpakk": "0",
    },
    "4": {
        "language": "Magyar",
        "nev": "Loki (Season 1) (WEB)",
        "fnev": "Loki.S01.zip",
        "felirat": "1700000009",
        "evad": "1",
        "ep": "-1",
        "feltolto": "csapat",
        "pontos_talalat": "101",
        "evadpakk": "1",
    },
    "5": {
        "language": "Magyar",
        "nev": "Loki - 1x03 (WEB.2160p-KOGi)",
        "fnev": "Loki.S01E03.srt",
        "felirat": "1700000005",
        "evad": "1",
        "ep": "3",
        "feltolto": "fordito",
        "pontos_talalat": "111",
        "evadpakk": "0",
    },
    "6": {
        "language": "Magyar",
        "nev": "Loki - 1x02 (WEB.1080p-KOGi)",
        "fnev": "Loki.S01E02.2160p.WEB.H265-KOGi.srt",
        "felirat": "1700000002",
        "evad": "1",
        "ep": "2",
        "feltolto": "fordito",
        "pontos_talalat": "111",
        "evadpakk": "0",
    },
}

MOVIE_HTML = """<html><body><table>
<tr><th>Nyelv</th><th>Cím</th></tr>
<tr><td>fejléc</td></tr>
<tr id="vilagit"><td><small>Magyar</small></td>
  <td><div class="magyar">Csillagok között</div><div class="eredeti">Interstellar (2014) (BluRay.1080p-SPARKS)</div></td>
  <td>feltöltő</td>
  <td><a href="/index.php?action=adatlap">adatlap</a> <a href="/index.php?action=letolt&amp;fnev=Interstellar.2014.1080p.BluRay-SPARKS.srt&amp;felirat=1415000001">Letöltés</a></td></tr>
<tr id="vilagit"><td><small>Angol</small></td>
  <td><div class="eredeti">Interstellar (2014) (WEB)</div></td><td>x</td>
  <td><a href="/index.php?action=letolt&amp;fnev=a.srt&amp;felirat=1415000002">Letöltés</a></td></tr>
<tr id="vilagit"><td><small>Magyar</small></td>
  <td><div class="eredeti">Interstellar (1999) (DVDRip)</div></td><td>x</td>
  <td><a href="/index.php?action=letolt&amp;fnev=b.srt&amp;felirat=1415000003">Letöltés</a></td></tr>
</table></body></html>"""


def episode_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Loki.S01E02.WEB.srt", SRT_HU.encode("cp1250"))
        archive.writestr(
            "Loki.S01E03.WEB.srt",
            SRT_HU.replace("őszintén", "harmadik").encode("cp1250"),
        )
        archive.writestr("olvass_el.txt", b"x")
    return buffer.getvalue()


# ------------------------------------------------------------------ elemzők
def test_parse_autoname_and_pick_show() -> None:
    shows = parse_autoname(AUTONAME)
    assert [(s.id, s.title, s.year) for s in shows][1] == ("5001", "Loki", 2021)
    assert pick_show(shows, "Loki", 2021).id == "5001"
    assert pick_show(shows, "Loki", 2015).id == "4100"
    assert pick_show(shows, "Loki", None).id == "5001"
    assert pick_show(shows, "Nincs ilyen", 2021) is None
    assert parse_autoname({"hiba": 1}) == []


def test_parse_series_results_merges_releases() -> None:
    subs = {s.id: s for s in parse_series_results(XBMC)}
    assert len(subs) == 5
    assert (
        "KOGi" in subs["1700000002"].release and "1080p" in subs["1700000002"].release
    )
    assert subs["1700000009"].season_pack and subs["1700000009"].episode == -1
    assert parse_series_results([]) == []


def test_release_from_name() -> None:
    assert release_from_name("Loki - 1x02 (WEB.2160p-KOGi)") == "WEB.2160p-KOGi"
    assert release_from_name("Valami") == ""


def test_rank_series_prefers_matching_release() -> None:
    subs = parse_series_results(XBMC)
    ranked = rank_series(subs, 1, 2, "Loki.S01E02.2160p.WEB.H265-KOGi.mkv", 6)
    ids = [s.id for s in ranked]
    assert ids[0] == "1700000002"  # magyar, pontos rész, egyező kiadás
    assert "1700000004" not in ids  # angol
    assert "1700000005" not in ids  # másik rész
    assert ids[-1] == "1700000009"  # évadpakk a végén
    other = rank_series(subs, 1, 2, "Loki.S01E02.1080p.WEB-DL-NTb.mkv", 6)
    assert other[0].id == "1700000003"


def test_release_score() -> None:
    sub = parse_series_results(XBMC)[0]
    assert release_score(sub, "") == 0
    assert release_score(sub, "Loki.S01E02.2160p.WEB.H265-KOGi.mkv") > release_score(
        sub, "Loki.S01E02.720p.HDTV-XYZ.mkv"
    )


def test_parse_movie_html() -> None:
    hits = parse_movie_html(MOVIE_HTML)
    assert [h.subtitle.id for h in hits] == ["1415000001", "1415000002", "1415000003"]
    first = hits[0]
    assert first.title == "Interstellar" and first.year == 2014
    assert first.subtitle.language == "Magyar"
    assert first.subtitle.release == "BluRay.1080p-SPARKS"


def test_ids_and_extra() -> None:
    assert parse_stremio_id("tt0944947") == ("tt0944947", 0, 0)
    assert parse_stremio_id("tt0944947:1:2") == ("tt0944947", 1, 2)
    assert parse_stremio_id("kitsu:123") is None
    assert parse_stremio_id("tt1:a:b") is None
    assert parse_extra("filename=Loki.S01E02.mkv&videoSize=1") == {
        "filename": "Loki.S01E02.mkv",
        "videoSize": "1",
    }


# ---------------------------------------------------------------- fájlok
def test_decode_hungarian_encodings() -> None:
    assert decode(SRT_HU.encode("cp1250")) == SRT_HU
    assert decode(b"\xef\xbb\xbf" + SRT_HU.encode("utf-8")) == SRT_HU
    assert decode("õszintén, tûz".encode("latin-1")) == "őszintén, tűz"


def test_episode_detection() -> None:
    assert episode_of("Show.S01E02.srt") == (1, 2)
    assert episode_of("show 1x05 web.srt") == (1, 5)
    assert episode_of("film.2014.srt") is None


def test_zip_season_pack_picks_episode() -> None:
    assert "őszintén" in build_srt(episode_zip(), 1, 2)
    assert "harmadik" in build_srt(episode_zip(), 1, 3)
    with pytest.raises(SubtitleError):
        build_srt(episode_zip(), 1, 9)


@pytest.mark.skipif(not shutil.which("bsdtar"), reason="nincs bsdtar")
def test_7z_archive(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Film.2014.srt").write_bytes(SRT_HU.encode("cp1250"))
    archive = tmp_path / "f.7z"
    subprocess.run(
        ["bsdtar", "--format", "7zip", "-cf", str(archive), "-C", str(src), "."],
        check=True,
    )
    assert "Árvíztűrő" in build_srt(archive.read_bytes(), 0, 0)


def test_ass_converted_to_srt() -> None:
    ass = (
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Szép napot, Ödön!\n"
    )
    srt = to_srt("a.ass", ass.encode("utf-8"))
    assert "-->" in srt and "Ödön" in srt


def test_choose_file_single_without_episode() -> None:
    name, _ = choose_file({"film.srt": b"x"}, 0, 0)
    assert name == "film.srt"


# -------------------------------------------------------------- végponttól végpontig
class Counter:
    def __init__(self) -> None:
        self.calls: list[str] = []


def make_app(tmp_path: Path, counter: Counter) -> TestClient:
    def feliratok(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        counter.calls.append(params.get("action") or "search")
        if params.get("action") == "autoname":
            return httpx.Response(200, json=AUTONAME)
        if params.get("action") == "xbmc":
            assert params["sid"] == "5001" and params["ev"] == "1"
            return httpx.Response(200, json=XBMC)
        if params.get("action") == "letolt":
            if params["felirat"] == "1700000009":
                return httpx.Response(200, content=episode_zip())
            return httpx.Response(200, content=SRT_HU.encode("cp1250"))
        if params.get("tab") == "film":
            return httpx.Response(200, text=MOVIE_HTML)
        return httpx.Response(404)

    def cinemeta(request: httpx.Request) -> httpx.Response:
        counter.calls.append("cinemeta")
        if request.url.path.endswith("/series/tt9140554.json"):
            return httpx.Response(
                200, json={"meta": {"name": "Loki", "year": "2021–2023"}}
            )
        if request.url.path.endswith("/movie/tt0816692.json"):
            return httpx.Response(
                200, json={"meta": {"name": "Interstellar", "year": "2014"}}
            )
        return httpx.Response(404)

    settings = Settings(
        base_url="https://felirat.pelda.ts.net",
        data_dir=tmp_path,
        feliratok_url="https://feliratok.eu/index.php",
        cinemeta_url="https://v3-cinemeta.strem.io",
        max_results=6,
        user_agent="teszt",
    )
    app = create_app(
        settings, httpx.MockTransport(feliratok), httpx.MockTransport(cinemeta)
    )
    return TestClient(app)


def test_manifest_and_health(tmp_path: Path) -> None:
    with make_app(tmp_path, Counter()) as client:
        manifest = client.get("/manifest.json").json()
        assert manifest["resources"] == ["subtitles"] and manifest["idPrefixes"] == [
            "tt"
        ]
        assert client.get("/health").json()["status"] == "ok"
        assert "manifest.json" in client.get("/").text
        assert (
            client.get(
                "/manifest.json", headers={"Origin": "https://web.stremio.com"}
            ).headers["access-control-allow-origin"]
            == "*"
        )


def test_series_flow(tmp_path: Path) -> None:
    counter = Counter()
    with make_app(tmp_path, counter) as client:
        url = "/subtitles/series/tt9140554:1:2/filename=Loki.S01E02.2160p.WEB.H265-KOGi.mkv&videoSize=1.json"
        subs = client.get(url).json()["subtitles"]
        assert subs[0]["url"] == "https://felirat.pelda.ts.net/sub/1700000002/1/2.srt"
        assert all(s["lang"] == "hun" for s in subs)
        assert subs[-1]["url"].startswith(
            "https://felirat.pelda.ts.net/sub/1700000009/"
        )

        srt = client.get("/sub/1700000002/1/2.srt")
        assert srt.status_code == 200 and "őszintén" in srt.text
        assert "charset=utf-8" in srt.headers["content-type"]
        pack = client.get("/sub/1700000009/1/2.srt")
        assert pack.status_code == 200 and "őszintén" in pack.text
        assert client.get("/sub/1700000009/1/9.srt").status_code == 404
        assert client.get("/sub/abc/1/1.srt").status_code == 400

        calls_before = len(counter.calls)
        client.get(url)
        client.get("/sub/1700000002/1/2.srt")
        assert len(counter.calls) == calls_before  # minden a gyorsítótárból jön


def test_movie_flow(tmp_path: Path) -> None:
    with make_app(tmp_path, Counter()) as client:
        subs = client.get("/subtitles/movie/tt0816692.json").json()["subtitles"]
        assert [s["id"] for s in subs] == ["feliratok-1415000001"]
        assert subs[0]["url"].endswith("/sub/1415000001/0/0.srt")


def test_unknown_titles_return_empty(tmp_path: Path) -> None:
    with make_app(tmp_path, Counter()) as client:
        assert client.get("/subtitles/series/tt0000001:1:1.json").json() == {
            "subtitles": []
        }
        assert client.get("/subtitles/movie/kitsu:1.json").json() == {"subtitles": []}
        assert client.get("/subtitles/channel/tt0816692.json").json() == {
            "subtitles": []
        }


def test_json_fixture_is_valid() -> None:
    json.dumps(XBMC)
