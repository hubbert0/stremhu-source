from app.modules.indexer_definitions.integrations.ncore import parse_recommended

PAGE = """
<div class="fobox_fej"><a name="film"></a>Film</div><div class="fobox_tartalom">
<div>Staff által jelölt</div>
<div style="float:left; width:33%"> <a href="torrents.php?action=details&amp;id=3819832"
 target="_blank"><img src="https://nc-img.burst.sh/covers/a?1" width="120"
 title="Amerikai.kopo.2023.RETAIL.HUN.PAL.DVD9-TRASH"></a> <br><br>
 <img src="https://static.ncore.pro/static/images/flags/hu.gif" height="10">&nbsp;&nbsp;DVD9:
 6.76 GiB<br> Seederek: 4<br> Leecherek: 1 </div>
<div>Legakt&iacute;vabb torrentek</div>
<div style="float:left; width:33%"> <a href="torrents.php?action=details&id=4000001"><img
 src="https://nc-img.burst.sh/covers/b?2" title="Runner.2026.1080p.WEB-DL.ENG"></a> <br><br>
 <img src="https://static.ncore.pro/static/images/flags/gb.gif">&nbsp;&nbsp;HD: 6.33 GiB<br>
 Seederek: 836<br></div>
<div style="height:24px"> <a style="x" href="torrents.php?action=details&id=4264267"
 target="_blank">Heart.of.the.Beast.2026.AMZN.WEBRip.x264.HUN-FULCRUM</a></div>
</div>
<div class="fobox_fej">Sorozat | <a href="#top">A lap tetejére</a></div>
<div><a href="torrents.php?action=details&amp;id=5000001">MobLand.S02E07.720p.HUN</a></div>
<div class="fobox_fej">Játék</div>
<div><a href="torrents.php?action=details&id=6000001">Game.Name-GOG</a></div>
"""


def test_parse_recommended_sections_and_groups() -> None:
    items = parse_recommended(PAGE)
    assert [(i.torrent_id, i.section, i.group, i.rank) for i in items] == [
        ("3819832", "film", "staff", 0),
        ("4000001", "film", "aktiv", 0),
        ("4264267", "film", "nepszeru", 0),
        ("5000001", "sorozat", "nepszeru", 0),
    ]  # a Játék doboz kimarad
    staff, active = items[0], items[1]
    assert staff.name == "Amerikai.kopo.2023.RETAIL.HUN.PAL.DVD9-TRASH"
    assert (staff.hungarian, staff.quality, staff.seeders) == (True, "DVD9", 4)
    assert staff.poster == "https://nc-img.burst.sh/covers/a?1"
    assert (active.hungarian, active.quality, active.seeders) == (False, "HD", 836)
    assert items[2].poster is None and items[2].hungarian is None
