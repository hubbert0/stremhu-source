"""A helyi Szerverpult beállításai és adatkönyvtára.

Minden adat a felhasználó saját gépén marad (Windowson: %APPDATA%\\Szerverpult).
"""

import json
import os
import re
import secrets
import sys
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

APP_NAME = "Szerverpult"
DEFAULT_PORT = 8765


def data_dir() -> Path:
    override = os.environ.get("SZERVERPULT_DATA")
    if override:
        path = Path(override)
    elif sys.platform == "win32" and os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / APP_NAME
    else:
        path = Path.home() / ".szerverpult"
    path.mkdir(parents=True, exist_ok=True)
    return path


def static_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "app" / "static"
    return Path(__file__).parent / "static"


def write_private(path: Path, text: str) -> None:
    """Fájl írása úgy, hogy félbeszakadáskor se maradjon félkész."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if sys.platform != "win32":
        tmp.chmod(0o600)
    tmp.replace(path)


def load_token(directory: Path) -> str:
    """A böngészős belépéshez használt titkos token (gépenként egy)."""
    path = directory / "token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    write_private(path, token)
    return token


HOST_RE = r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
USER_RE = r"^[a-z_][a-z0-9_-]{0,31}$"
PATH_RE = r"^/[A-Za-z0-9._/-]{0,250}$"


class Hostnames(BaseModel):
    stremhu: str = Field(default="stremhu-tb", pattern=r"^[a-z0-9-]{1,63}$")
    aiostreams: str = Field(default="aiostreams", pattern=r"^[a-z0-9-]{1,63}$")
    tmdb: str = Field(default="tmdb", pattern=r"^[a-z0-9-]{1,63}$")
    pult: str = Field(default="pult", pattern=r"^[a-z0-9-]{1,63}$")
    lelibrary: str = Field(default="lelibrary", pattern=r"^[a-z0-9-]{1,63}$")
    felirat: str = Field(default="felirat", pattern=r"^[a-z0-9-]{1,63}$")


class Settings(BaseModel):
    ssh_host: str = Field(default="", max_length=253)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", pattern=USER_RE)
    # A szerver SSH-gépkulcsa (az első kapcsolódáskor rögzítjük).
    host_key: str = Field(default="", max_length=4000)
    repo_dir: str = Field(default="/root/stremhu-source", pattern=PATH_RE)
    tailnet: str = Field(default="", pattern=r"^([a-z0-9-]+\.ts\.net)?$")
    hostnames: Hostnames = Field(default_factory=Hostnames)
    # Opcionális: az UltraVNC-néző (vncviewer.exe) teljes elérési útja.
    vnc_viewer: str = Field(default="", max_length=500)

    @field_validator("ssh_host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(HOST_RE, value):
            raise ValueError("Érvénytelen szervercím.")
        return value

    @property
    def configured(self) -> bool:
        return bool(self.ssh_host and self.host_key)


class SettingsStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "settings.json"

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Settings.model_validate(raw)
        except (FileNotFoundError, ValueError):
            return Settings()

    def save(self, settings: Settings) -> None:
        write_private(self.path, settings.model_dump_json(indent=2))
