from app.modules.torbox_play.nfo import parse_nfo

BOX = "██"

TABLE = f"""
{BOX}         iMDb link................: https://www.imdb.com/title/tt1517268/  {BOX}
{BOX}                         Codec..........: x264                             {BOX}
{BOX}                         Bitrate........: 15 900 Kbps                      {BOX}
{BOX}                                                                           {BOX}
{BOX}           AUDIO 1                                     AUDIO 2             {BOX}
{BOX}   Format.........: AC3                       Format.........: E-AC3       {BOX}
{BOX}   Language.......: Hungarian                 Language.......: English     {BOX}
{BOX}   Bitrate........: 640 Kbps                  Bitrate........: 1536 Kbps   {BOX}
{BOX}   Channels.......: 6 (5.1)                   Channels.......: 8 (7.1)     {BOX}
{BOX}                                                                           {BOX}
{BOX}                  Subtitles......: Hungarian Forced & Full                 {BOX}
"""

MEDIAINFO = """General
Complete name                            : X.mkv
Overall bit rate                         : 6 012 kb/s

Video
Format                                   : AVC
Bit rate                                 : 5 500 kb/s
Language                                 : English

Audio #1
Format                                   : E-AC-3
Channel(s)                               : 6 channels
Language                                 : Hungarian

Audio #2
Format                                   : AAC LC
Channel(s)                               : 2 channels
Language                                 : English

Text #1
Language                                 : Hungarian
Title                                    : Forced
Forced                                   : Yes

Text #2
Language                                 : Hungarian
Forced                                   : No

Text #3
Language                                 : English
"""

ONE_LINE = """Video: 1920x1080 x264
Audio: Hungarian DD 5.1 / English DTS 5.1
Subs: Hungarian, English
"""


def labels(text: str) -> tuple[list[str], list[str], str]:
    info = parse_nfo(text)
    return (
        [a.label for a in info.audio],
        [s.label for s in info.subtitles],
        info.bitrate,
    )


def test_two_column_table() -> None:
    assert labels(TABLE) == (
        ["magyar 5.1", "angol 7.1"],
        ["magyar (kényszerített + teljes)"],
        "15,9 Mbps",
    )


def test_mediainfo_sections() -> None:
    assert labels(MEDIAINFO) == (
        ["magyar 5.1", "angol 2.0"],
        ["magyar (kényszerített + teljes)", "angol"],
        "5,5 Mbps",
    )


def test_one_line_summary() -> None:
    assert labels(ONE_LINE) == (["magyar 5.1", "angol 5.1"], ["magyar", "angol"], "")


def test_logo_only_is_empty() -> None:
    assert parse_nfo(f"{BOX}{BOX}\n  Greetings to all  \n").empty


def test_ncore_fetch_nfo_strips_html() -> None:
    import asyncio

    import httpx

    from app.modules.indexer_definitions.base_indexer_definition import IndexerClient
    from app.modules.indexer_definitions.integrations.ncore import (
        NcoreIndexerDefinition,
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, text="<pre>Language: Hungarian<br>Subs: Hungarian &amp; English</pre>"
        )

    definition = NcoreIndexerDefinition()
    definition._client = IndexerClient(  # pyright: ignore[reportPrivateUsage]
        definition=definition,
        base_url=definition.url,
        transport=httpx.MockTransport(handler),
    )
    text = asyncio.run(definition.fetch_nfo("3622495"))
    assert requests[0].url.path == "/ajax.php"
    assert dict(requests[0].url.params) == {
        "action": "nfo",
        "id": "3622495",
        "details": "1",
    }
    assert text == "Language: Hungarian\nSubs: Hungarian & English"


# Valós nCore-NFO-k szerkezete (rövidítve).
MULTILINE = """
       TiTLE.........: Barbie (2023)
       HUN.TiTLE.....:
       ViDEO.CODEC...: AVC
       BiTRATE.......: 27.6 Mbps
       AUDiO.........: Hungarian             DD   5.1  @   640 kbps  / #1 /
                       English     TrueHD.Atmos   7.1  @  2883 kbps
                       English        DDP.Atmos   7.1  @   768 kbps
       SUBTiTLES.....: Hungarian + Forced / #1 / (SRT)
                       English (SRT) + SDH (SRT+PGS)
       AUDiO.SOURCE..: No1 (THX!)
       NOTES.........:
           Tested with Panasonic TX-65JZ1500E & Dune HD Real Vision 4K
"""

COMPACT = """RELEASE NAME.: Barbie.2023.MA.WEBRiP.DD5.1.x264.HUN-GS88
BITRATE......: 1 294 kb/s
AUDIO........: DD5.1 630 kb/s Hungarian
SUBTITLES....: Hungarian Forced,Hungarian
SOURCE.......: 2160p MA WEB-DL + No1 /THX/
"""


def test_multiline_values() -> None:
    assert labels(MULTILINE) == (
        ["magyar 5.1", "angol 7.1"],
        ["magyar (kényszerített + teljes)", "angol"],
        "27,6 Mbps",
    )


def test_compact_values() -> None:
    assert labels(COMPACT) == (
        ["magyar 5.1"],
        ["magyar (kényszerített + teljes)"],
        "1,3 Mbps",
    )
