"""A staff-ajánlások 7 napos gyűjtése (az nCore véletlenül mutat 6-ot)."""

import asyncio
from pathlib import Path

import pytest

from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionRecommendedItem,
)
from app.modules.torbox_play import service as service_module
from app.modules.torbox_play.service import STAFF_KEEP, TorboxPlayService
from app.modules.torbox_seed.config import TorboxSeedSettings


def staff(tid: str, section: str = "film") -> IndexerDefinitionRecommendedItem:
    return IndexerDefinitionRecommendedItem(
        torrent_id=tid,
        name=f"Film.{tid}.2020.1080p",
        section=section,
        group="staff",
        rank=0,
    )


def popular(tid: str) -> IndexerDefinitionRecommendedItem:
    return IndexerDefinitionRecommendedItem(
        torrent_id=tid,
        name=f"Pop.{tid}.2024.1080p",
        section="film",
        group="nepszeru",
        rank=0,
    )


class FakeNcore:
    def __init__(self) -> None:
        self.pages: list[list[IndexerDefinitionRecommendedItem]] = []

    async def fetch_recommended(self) -> list[IndexerDefinitionRecommendedItem]:
        return self.pages.pop(0)


class FakeDefinitions:
    def __init__(self) -> None:
        self.ncore = FakeNcore()

    def get_by_id(self, _: str) -> FakeNcore:
        return self.ncore


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(service_module.time, "time", clock.time)
    return clock


def make(path: Path | None) -> tuple[TorboxPlayService, FakeNcore]:
    definitions = FakeDefinitions()
    service = TorboxPlayService(
        TorboxSeedSettings(api_key="k"),
        definitions,  # type: ignore[arg-type]
        staff_pool_path=path,
    )
    return service, definitions.ncore


def refresh(service: TorboxPlayService) -> list[IndexerDefinitionRecommendedItem]:
    service._recommended_at = -1e12  # pyright: ignore[reportPrivateUsage]
    asyncio.run(service._refresh_recommended())  # pyright: ignore[reportPrivateUsage]
    return service._recommended  # pyright: ignore[reportPrivateUsage]


def staff_ids(items: list[IndexerDefinitionRecommendedItem]) -> list[str]:
    return [i.torrent_id for i in items if i.group == "staff"]


def test_staff_accumulates_newest_upload_first(clock: Clock) -> None:
    service, ncore = make(None)
    ncore.pages = [
        [popular("1"), staff("10"), staff("30")],
        [popular("2"), staff("20"), staff("30")],
    ]
    refresh(service)
    items = refresh(service)
    assert staff_ids(items) == ["30", "20", "10"]
    assert [i.rank for i in items if i.group == "staff"] == [0, 1, 2]
    # A népszerű lista mindig csak a legutóbbi.
    assert [i.torrent_id for i in items if i.group == "nepszeru"] == ["2"]


def test_staff_ranks_per_section(clock: Clock) -> None:
    service, ncore = make(None)
    ncore.pages = [[staff("10"), staff("20", "sorozat"), staff("5")]]
    items = refresh(service)
    ranks = {i.torrent_id: i.rank for i in items}
    assert ranks == {"10": 0, "5": 1, "20": 0}


def test_staff_expires_after_seven_days(clock: Clock) -> None:
    service, ncore = make(None)
    ncore.pages = [[staff("10"), staff("20")], [staff("20")]]
    refresh(service)
    clock.now += STAFF_KEEP + 60
    assert staff_ids(refresh(service)) == ["20"]


def test_staff_pool_survives_restart(clock: Clock, tmp_path: Path) -> None:
    path = tmp_path / "system" / "pool.json"
    service, ncore = make(path)
    ncore.pages = [[staff("10")]]
    refresh(service)
    assert path.exists()

    again, ncore = make(path)
    ncore.pages = [[staff("20")]]
    assert staff_ids(refresh(again)) == ["20", "10"]

    clock.now += STAFF_KEEP + 60
    expired, _ = make(path)
    assert expired._staff_pool == {}  # pyright: ignore[reportPrivateUsage]


def test_broken_pool_file_is_ignored(clock: Clock, tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text("nem json", encoding="utf-8")
    service, ncore = make(path)
    ncore.pages = [[staff("10")]]
    assert staff_ids(refresh(service)) == ["10"]
