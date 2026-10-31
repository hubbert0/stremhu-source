"""A széf titkosított tárolása.

A titkosítás és a visszafejtés kizárólag a böngészőben történik
(PBKDF2-SHA256 + AES-GCM, WebCrypto). A szerver csak a titkosított
adatcsomagot tárolja, a mesterjelszót és a tartalmat soha nem látja.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

MAX_DATA_LENGTH = 2_000_000  # base64 karakter


class EncryptedVault(BaseModel):
    v: int = Field(ge=1, le=1)
    salt: str = Field(min_length=16, max_length=64)
    iv: str = Field(min_length=12, max_length=64)
    iter: int = Field(ge=100_000, le=5_000_000)
    data: str = Field(min_length=1, max_length=MAX_DATA_LENGTH)
    updated_at: str | None = None


def vault_path(data_dir: Path) -> Path:
    return data_dir / "vault.json"


def load_vault(data_dir: Path) -> EncryptedVault | None:
    try:
        raw = json.loads(vault_path(data_dir).read_text(encoding="utf-8"))
        return EncryptedVault.model_validate(raw)
    except (FileNotFoundError, ValueError):
        return None


def save_vault(data_dir: Path, vault: EncryptedVault) -> None:
    path = vault_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(vault.model_dump_json(), encoding="utf-8")
    tmp.replace(path)
