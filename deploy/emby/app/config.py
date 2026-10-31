"""Az Emby-könyvtár beállításai környezeti változókból (a .env-ből).

Szerverenként négy sor, N = 1, 2, ... (legfeljebb 9):
    EMBYN_NEV=Saját            – így jelenik meg a Stremióban
    EMBYN_URL=https://...       – ahogy kívülről, a böngészőből is eléred
    EMBYN_FELHASZNALO=stremio   – külön, csak lejátszásra jogosult felhasználó
    EMBYN_JELSZO=...
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.0.0"
MAX_SERVERS = 9
MIN_SECRET_LENGTH = 16

logger = logging.getLogger("emby")


@dataclass(frozen=True)
class EmbyServer:
    index: int
    name: str
    url: str
    username: str
    password: str


@dataclass(frozen=True)
class Settings:
    base_url: str
    secret: str
    data_dir: Path
    servers: tuple[EmbyServer, ...]
    page_size: int = 100
    timeout: float = 12.0

    @property
    def secret_ok(self) -> bool:
        return len(self.secret) >= MIN_SECRET_LENGTH and self.secret.isalnum()

    @property
    def addon_base(self) -> str:
        return f"{self.base_url}/{self.secret}"


def load_servers(env: Mapping[str, str]) -> tuple[EmbyServer, ...]:
    servers: list[EmbyServer] = []
    for index in range(1, MAX_SERVERS + 1):
        url = env.get(f"EMBY{index}_URL", "").strip().rstrip("/")
        username = env.get(f"EMBY{index}_FELHASZNALO", "").strip()
        if not url and not username:
            continue
        if not url.startswith(("http://", "https://")):
            logger.warning(
                "EMBY%d_URL hiányzik vagy nem http(s)://-sel kezdődik – kihagyom.",
                index,
            )
            continue
        if not username:
            logger.warning("EMBY%d_FELHASZNALO hiányzik – kihagyom.", index)
            continue
        name = env.get(f"EMBY{index}_NEV", "").strip() or f"Emby {index}"
        servers.append(
            EmbyServer(
                index=index,
                name=name,
                url=url,
                username=username,
                password=env.get(f"EMBY{index}_JELSZO", ""),
            )
        )
    return tuple(servers)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        base_url=env.get("EMBY_BASE_URL", "http://127.0.0.1:7000").rstrip("/"),
        secret=env.get("EMBY_ADDON_SECRET", "").strip(),
        data_dir=Path(env.get("EMBY_DATA_DIR", "/data")),
        servers=load_servers(env),
    )
