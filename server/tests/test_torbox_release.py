import pytest

from app.modules.torbox_play.release import parse_release


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        (
            "X-Faktor.S14E11.HUN.WEB-DL.1080p.H264-LEGION",
            "hdser_hun",
            {
                "title": "X-Faktor",
                "marker": "S14E11",
                "resolution": "1080p",
                "source": "WEB-DL",
                "codec": "H.264",
                "hungarian_audio": True,
                "hungarian_subtitles": False,
            },
        ),
        (
            "It.Follows.2014.720p.BluRay.x264",
            "hd",
            {"title": "It Follows", "marker": "2014", "resolution": "720p"},
        ),
        (
            "Bad.Thoughts.S01.720p.NF.WEB-DL.DDP5.1.HUNSUB",
            "hdser",
            {
                "marker": "S01",
                "hungarian_audio": False,
                "hungarian_subtitles": True,
                "audio": "DD+ 5.1",
            },
        ),
        (
            "1917.2019.2160p.UHD.BluRay.REMUX.HDR.DV.TrueHD.Atmos.7.1",
            "hd",
            {
                "title": "1917",
                "year": 2019,
                "resolution": "4K",
                "source": "REMUX",
                "hdr": "Dolby Vision",
                "audio": "Atmos 7.1",
            },
        ),
        ("Mese.a.farkasrol.DVDRip.XviD", "xvid_hun", {"resolution": "SD"}),
    ],
)
def test_parse_release(name: str, category: str, expected: dict[str, object]) -> None:
    release = parse_release(name, category)
    for field, value in expected.items():
        assert getattr(release, field) == value, field


def test_versions_share_key() -> None:
    a = parse_release("X-Faktor.S14E11.HUN.WEB-DL.1080p.H264-LEGION")
    b = parse_release("X.Faktor.S14E11.720p.HDTV.HUN")
    assert (a.key, a.marker) == (b.key, b.marker)


def test_disc_size_tokens_end_the_title() -> None:
    assert parse_release("A szarvasvadász BD50").title == "A szarvasvadász"
    assert parse_release("Máris.hiányzol.BD25.HUN").title == "Máris hiányzol"
    assert (
        parse_release("Amerikai.kopo.2023.RETAIL.HUN.PAL.DVD9-TRASH").title
        == "Amerikai kopo"
    )
