"""A Torznab-találatok hang- és feliratnyelvei (language / subs attribútum)."""

from types import SimpleNamespace

import pytest

from app.modules.media_attributes.constants import MediaAttributeKey
from app.modules.torznab.items import _language_names, _subtitle_names


def source(name: str, *category: str) -> SimpleNamespace:
    return SimpleNamespace(
        torrent_file=SimpleNamespace(info=SimpleNamespace(name=name)),
        indexer_torrent=SimpleNamespace(
            media_attributes=[SimpleNamespace(id=attribute) for attribute in category]
        ),
    )


@pytest.mark.parametrize(
    ("name", "category", "audio", "subs"),
    [
        # magyar kategória: van magyar hang
        (
            "Film.2024.1080p.WEB-DL.HUN.ENG-GRP",
            [MediaAttributeKey.HUN],
            ["Hungarian", "English"],
            [],
        ),
        ("Film.2024.1080p.WEB-DL-GRP", [MediaAttributeKey.HUN], ["Hungarian"], []),
        # külföldi kategória, nyelv nélkül: eredeti nyelv, nincs magyar hang
        ("Film.2024.1080p.WEB-DL-GRP", [MediaAttributeKey.ENG], ["Original"], []),
        ("Film.2024.1080p.WEB-DL.ENG-GRP", [MediaAttributeKey.ENG], ["English"], []),
        # a feliratjelölés nem hang
        (
            "Film.2024.1080p.WEB-DL.HUN.SUB-GRP",
            [MediaAttributeKey.ENG],
            ["Original"],
            ["Hungarian"],
        ),
        ("Film.2024.1080p.HUNSUB.WEB-DL-GRP", [], [], ["Hungarian"]),
        ("Film 2024 1080p magyar felirattal", [], [], ["Hungarian"]),
        (
            "Film.2024.HUN.ENG.SUB.1080p-GRP",
            [MediaAttributeKey.HUN],
            ["Hungarian"],
            ["English"],
        ),
        # kategória nélküli tracker: csak a név számít
        ("Film.2024.1080p.WEB-DL-GRP", [], [], []),
    ],
)
def test_languages(
    name: str, category: list[str], audio: list[str], subs: list[str]
) -> None:
    item = source(name, *category)
    assert _language_names(item) == audio  # type: ignore[arg-type]
    assert _subtitle_names(item) == subs  # type: ignore[arg-type]
