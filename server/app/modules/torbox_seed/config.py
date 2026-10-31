import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TorboxSeedSettings(BaseSettings):
    """Környezeti változók (TORBOX_ előtaggal)."""

    model_config = SettingsConfigDict(env_prefix="TORBOX_", extra="ignore")

    # Ha üres, a helykezelés nem fut.
    api_key: str | None = None

    # Milyen gyakran fusson az ellenőrzés.
    cleanup_interval_minutes: int = Field(default=5, ge=2)

    # A TorBox-csomag aktív helyeinek száma (Pro: 10).
    slot_limit: int = Field(default=10, ge=1)

    # Ennyi helynek kell mindig szabadnak lennie.
    min_free_slots: int = Field(default=2, ge=0)

    # Egy még kötelezett seed legfeljebb ennyi óráig szünetelhet egyszerre.
    max_pause_hours: float = Field(default=6, gt=0)

    # Biztonsági minimum: a TorBoxra kerülés után legalább ennyi óráig a
    # torrentet folyamatban lévőnek tekintjük (a tracker késve regisztrál).
    min_seed_hours: float = Field(default=24, ge=0)

    # A tracker Hit'n'Run-listáját legfeljebb ilyen gyakran kérdezzük le.
    hnr_cache_minutes: int = Field(default=15, ge=1)

    # Próbaüzem: csak naplózza, mit csinálna, de nem nyúl a TorBoxhoz.
    cleanup_dry_run: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


@functools.lru_cache(maxsize=1)
def get_torbox_seed_settings() -> TorboxSeedSettings:
    return TorboxSeedSettings()
