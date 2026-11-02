import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TorboxSeedSettings(BaseSettings):
    """Környezeti változók (TORBOX_ előtaggal)."""

    model_config = SettingsConfigDict(env_prefix="TORBOX_", extra="ignore")

    # Ha üres, a seedkezelés nem fut.
    api_key: str | None = None

    # Milyen gyakran fusson az ellenőrzés.
    cleanup_interval_minutes: int = Field(default=5, ge=2)

    # A TorBox-csomag aktív helyeinek száma (Pro: 10).
    slot_limit: int = Field(default=10, ge=1)

    # Ennyi helynek kellene szabadnak lennie (ha kevesebb, figyelmeztetés jön).
    min_free_slots: int = Field(default=2, ge=0)

    # Biztonsági minimum: a TorBoxra kerülés után legalább ennyi óráig a
    # seedköteles torrentet folyamatban lévőnek tekintjük (a tracker késve
    # regisztrál), és egy ismeretlen privát torrentet sem állítunk le.
    min_seed_hours: float = Field(default=24, ge=0)

    # Vészleállítás: ha a nem kötelezettek leállítása után is kevesebb a szabad
    # hely, mint `min_free_slots`, a seedkötelezettek közül annak a seedelését
    # is leállítja, amelyiknek a legkevesebb ideje van hátra (és szól).
    emergency_stop: bool = True

    # Seedköteles trackerek (vesszővel elválasztva): a StremHU indexer-azonosítója,
    # és egyben a tracker címében keresett szó. Csak ezek torrentjeit seedeljük.
    seed_trackers: str = "ncore"

    # A tracker Hit'n'Run-listáját legfeljebb ilyen gyakran kérdezzük le.
    hnr_cache_minutes: int = Field(default=15, ge=1)

    # Egy ellenőrzés során legfeljebb ennyi ismeretlen Hit'n'Run-torrentet
    # azonosítunk (a .torrent fájl letöltésével), hogy ne terheljük a trackert.
    hnr_resolve_limit: int = Field(default=10, ge=1)

    # Értesítés a telefonra (ntfy). Üres témával nem küld értesítést.
    ntfy_topic: str | None = None
    ntfy_url: str = "https://ntfy.sh"

    # Ugyanarról a problémáról legfeljebb ilyen gyakran megy újra értesítés.
    alert_repeat_hours: float = Field(default=12, gt=0)

    # Próbaüzem: csak naplózza, mit csinálna, de nem nyúl a TorBoxhoz.
    cleanup_dry_run: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @property
    def seed_tracker_list(self) -> tuple[str, ...]:
        trackers = [
            tracker.strip().lower()
            for tracker in self.seed_trackers.split(",")
            if tracker.strip()
        ]
        return tuple(dict.fromkeys(trackers))


@functools.lru_cache(maxsize=1)
def get_torbox_seed_settings() -> TorboxSeedSettings:
    return TorboxSeedSettings()
