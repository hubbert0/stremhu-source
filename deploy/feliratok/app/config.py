"""A felirat-addon beállításai környezeti változókból."""

import os
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.0.0"


@dataclass(frozen=True)
class Settings:
    base_url: str
    data_dir: Path
    feliratok_url: str
    cinemeta_url: str
    max_results: int
    user_agent: str


def load_settings() -> Settings:
    return Settings(
        base_url=os.environ.get("FELIRAT_BASE_URL", "http://127.0.0.1:7000").rstrip(
            "/"
        ),
        data_dir=Path(os.environ.get("FELIRAT_DATA_DIR", "/data")),
        feliratok_url=os.environ.get("FELIRATOK_URL", "https://feliratok.eu/index.php"),
        cinemeta_url=os.environ.get(
            "CINEMETA_URL", "https://v3-cinemeta.strem.io"
        ).rstrip("/"),
        max_results=int(os.environ.get("FELIRAT_MAX_RESULTS", "6")),
        user_agent=os.environ.get(
            "FELIRAT_USER_AGENT",
            f"stremhu-feliratok/{VERSION} (Stremio subtitle addon)",
        ),
    )
