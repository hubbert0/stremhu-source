"""UltraVNC-néző indítása a széfben tárolt címmel és jelszóval."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATES = (
    r"C:\Program Files\uvnc bvba\UltraVNC\vncviewer.exe",
    r"C:\Program Files\UltraVNC\vncviewer.exe",
    r"C:\Program Files (x86)\uvnc bvba\UltraVNC\vncviewer.exe",
    r"C:\Program Files (x86)\UltraVNC\vncviewer.exe",
)

ADDRESS_RE = re.compile(
    r"^(?P<host>[A-Za-z0-9][A-Za-z0-9.-]{0,252})(?::{1,2}(?P<port>\d{1,5}))?$"
)


class VNCError(Exception):
    pass


def find_viewer(configured: str = "") -> Path | None:
    if configured:
        path = Path(configured.strip().strip('"'))
        return path if path.is_file() else None
    for candidate in CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    found = shutil.which("vncviewer")
    return Path(found) if found else None


def build_args(viewer: Path, address: str, password: str) -> list[str]:
    """Az UltraVNC-néző parancssora.

    A Contabo IP:port formában adja meg a VNC-címet; az UltraVNC a portot
    dupla kettősponttal várja (host::port), az egyszeres kettőspont nála
    kijelzőszámot jelentene.
    """
    address = address.strip()
    for prefix in ("vnc://", "VNC://"):
        address = address.removeprefix(prefix)
    match = ADDRESS_RE.match(address)
    if not match:
        raise VNCError("A VNC-cím formája IP:port legyen (pl. 203.0.113.10:63100).")
    target = match.group("host")
    port = match.group("port")
    if port:
        if not 1 <= int(port) <= 65535:
            raise VNCError("Érvénytelen portszám.")
        target = f"{target}::{port}"
    args = [str(viewer), target]
    if password:
        if any(ch in password for ch in "\r\n\x00"):
            raise VNCError("A jelszó nem tartalmazhat sortörést.")
        args += ["-password", password]
    return args


def launch(configured_viewer: str, address: str, password: str) -> str:
    viewer = find_viewer(configured_viewer)
    if viewer is None:
        raise VNCError(
            "Nem találom az UltraVNC-nézőt (vncviewer.exe). Telepítsd az UltraVNC-t, "
            "vagy add meg a vncviewer.exe helyét a Beállításokban."
        )
    args = build_args(viewer, address, password)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            args,
            close_fds=True,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise VNCError(f"Nem sikerült elindítani: {error}") from error
    return str(viewer)
