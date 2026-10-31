import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TorboxSeedSettings(BaseSettings):
    """Környezeti változók (TORBOX_ előtaggal)."""

    model_config = SettingsConfigDict(env_prefix="TORBOX_", extra="ignore")

    # Ha üres, a takarítás nem fut.
    api_key: str | None = None

    # Milyen gyakran fusson a takarítás.
    cleanup_interval_minutes: int = Field(default=30, ge=5)

    # Biztonsági minimum: a TorBoxra kerülés után legalább ennyi óráig
    # biztosan nem törlünk, akkor sem, ha a tracker listáján már nem szerepel
    # (a tracker kis késéssel regisztrálja az új letöltéseket).
    min_seed_hours: float = Field(default=24, ge=0)

    # Próbaüzem: csak naplózza, mit törölne, de nem töröl.
    cleanup_dry_run: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


@functools.lru_cache(maxsize=1)
def get_torbox_seed_settings() -> TorboxSeedSettings:
    return TorboxSeedSettings()
