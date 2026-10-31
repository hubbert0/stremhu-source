"""A szerverpult beállításai környezeti változókból."""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Ezeket a szolgáltatásokat a pult frissítéskor nem indítja újra, mert
# saját magát nem tudja biztonságosan lecserélni futás közben.
SELF_SERVICES = frozenset({"pult", "terminal", "ts-pult"})


@dataclass(frozen=True)
class Settings:
    user: str
    password: str
    tailnet: str
    repo_dir: Path
    compose_dir: Path
    data_dir: Path
    pult_image: str
    hostnames: dict[str, str] = field(default_factory=dict)

    @property
    def compose_file(self) -> Path:
        return self.compose_dir / "compose.yaml"


def load_settings() -> Settings:
    repo_dir = Path(os.environ.get("REPO_DIR", "/root/stremhu-source"))
    return Settings(
        user=os.environ.get("PULT_USER", "admin"),
        password=os.environ.get("PULT_PASSWORD", ""),
        tailnet=os.environ.get("TAILNET", ""),
        repo_dir=repo_dir,
        compose_dir=Path(os.environ.get("COMPOSE_DIR", str(repo_dir / "deploy"))),
        data_dir=Path(os.environ.get("PULT_DATA_DIR", "/data")),
        pult_image=os.environ.get("PULT_IMAGE", "ghcr.io/hubbert0/stremhu-pult:latest"),
        hostnames={
            "stremhu": os.environ.get("STREMHU_HOSTNAME", "stremhu-tb"),
            "aiostreams": os.environ.get("AIOSTREAMS_HOSTNAME", "aiostreams"),
            "tmdb": os.environ.get("TMDB_HOSTNAME", "tmdb"),
            "pult": os.environ.get("PULT_HOSTNAME", "pult"),
            "lelibrary": os.environ.get("LELIBRARY_HOSTNAME", "lelibrary"),
        },
    )
