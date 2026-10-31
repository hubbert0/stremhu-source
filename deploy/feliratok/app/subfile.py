"""A letöltött felirat feldolgozása: kicsomagolás, a jó rész kiválasztása,
karakterkódolás javítása (ő, ű) és átalakítás SRT-re."""

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pysubs2

SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
EPISODE_PATTERNS = (
    re.compile(r"s(?P<s>\d{1,2})[ ._-]*e(?P<e>\d{1,3})", re.IGNORECASE),
    re.compile(r"(?<!\d)(?P<s>\d{1,2})x(?P<e>\d{1,3})(?!\d)", re.IGNORECASE),
)


class SubtitleError(Exception):
    pass


def archive_kind(data: bytes) -> str | None:
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if data.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    return None


def extract_archive(data: bytes) -> dict[str, bytes]:
    """Visszaadja a tömörített fájlban lévő feliratfájlokat (név → tartalom)."""
    kind = archive_kind(data)
    if kind is None:
        raise SubtitleError("Nem tömörített fájl.")
    files: dict[str, bytes] = {}
    bsdtar = shutil.which("bsdtar")
    if bsdtar:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f"felirat.{kind}"
            out = Path(tmp) / "ki"
            out.mkdir()
            archive.write_bytes(data)
            result = subprocess.run(
                [bsdtar, "-xf", str(archive), "-C", str(out)],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise SubtitleError("A tömörített felirat nem bontható ki.")
            for path in out.rglob("*"):
                if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS:
                    files[str(path.relative_to(out))] = path.read_bytes()
    elif kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if not info.is_dir() and info.filename.lower().endswith(
                    SUBTITLE_EXTENSIONS
                ):
                    files[info.filename] = archive.read(info)
    else:
        raise SubtitleError(f"A {kind} formátumhoz bsdtar kell.")
    return files


def episode_of(filename: str) -> tuple[int, int] | None:
    name = Path(filename).name
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(name)
        if match:
            return int(match.group("s")), int(match.group("e"))
    return None


def choose_file(
    files: dict[str, bytes], season: int, episode: int
) -> tuple[str, bytes]:
    if not files:
        raise SubtitleError("A csomagban nincs feliratfájl.")
    if episode > 0:
        matching = [
            (name, content)
            for name, content in files.items()
            if (found := episode_of(name)) is not None
            and found[1] == episode
            and (season <= 0 or found[0] == season)
        ]
        if matching:
            return min(matching, key=_preference)
        if len(files) > 1:
            raise SubtitleError("A csomagban nincs felirat ehhez a részhez.")
    return min(files.items(), key=_preference)


def _preference(item: tuple[str, bytes]) -> tuple[int, str]:
    name = item[0].lower()
    order = {".srt": 0, ".ass": 1, ".ssa": 1, ".vtt": 2, ".sub": 3}
    return order.get(Path(name).suffix, 9), name


def decode(data: bytes) -> str:
    """UTF-8, vagy a magyar Windows-kódolás (cp1250), végül ISO-8859-2."""
    for encoding in ("utf-8-sig", "cp1250", "iso-8859-2"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        # A régi feliratokban az ő/ű helyén gyakran õ/û áll (latin-1 hiba): javítjuk.
        return (
            text.replace("õ", "ő").replace("û", "ű").replace("Õ", "Ő").replace("Û", "Ű")
        )
    return data.decode("utf-8", errors="replace")


def to_srt(name: str, data: bytes) -> str:
    text = decode(data)
    suffix = Path(name).suffix.lower()
    if suffix == ".srt" or (not suffix and "-->" in text):
        return text.replace("\r\n", "\n").strip() + "\n"
    try:
        subs = pysubs2.SSAFile.from_string(text, fps=25.0)
    except Exception as error:
        raise SubtitleError(
            f"Ismeretlen feliratformátum: {suffix or 'nincs kiterjesztés'}"
        ) from error
    return subs.to_string("srt")


def build_srt(raw: bytes, season: int, episode: int, filename_hint: str = "") -> str:
    """A letöltött nyers fájlból kész, UTF-8 kódolású SRT."""
    if archive_kind(raw):
        name, content = choose_file(extract_archive(raw), season, episode)
        return to_srt(name, content)
    return to_srt(filename_hint or "felirat.srt", raw)
