"""Élő próba a feliratok.eu ellen, a szerveren:

docker compose exec feliratok python -m app.selftest            # beépített példák
docker compose exec feliratok python -m app.selftest tt0944947:1:1
docker compose exec feliratok python -m app.selftest tt0816692
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import httpx

from app.cache import Cache
from app.config import load_settings
from app.feliratok import FeliratokClient
from app.service import SubtitleService

EXAMPLES = [
    "tt0944947:1:1",
    "tt0903747:2:3",
    "tt0816692",
]  # Trónok harca, Breaking Bad, Csillagok között


async def run(ids: list[str]) -> int:
    settings = load_settings()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        client = FeliratokClient(settings.feliratok_url, settings.user_agent)
        http = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent}, follow_redirects=True
        )
        service = SubtitleService(settings, Cache(Path(tmp) / "c.sqlite"), client, http)
        try:
            for stremio_id in ids:
                kind = "series" if stremio_id.count(":") == 2 else "movie"
                print(f"\n=== {stremio_id} ({kind})")
                found = await service.find(kind, stremio_id, {})
                if not found:
                    print(
                        "  Nincs magyar felirat (vagy a cím nem található a feliratok.eu-n)."
                    )
                    failures += 1
                    continue
                for item in found:
                    print(f"  {item['url']}")
                parts = found[0]["url"].rsplit("/", 3)
                srt = await service.srt(
                    parts[1], int(parts[2]), int(parts[3].split(".")[0])
                )
                preview = " / ".join(
                    line for line in srt.splitlines()[:8] if line.strip()
                )
                print(f"  Első felirat eleje: {preview[:200]}")
        finally:
            await client.close()
            await http.aclose()
    print("\nKész." if not failures else f"\n{failures} példánál nem volt találat.")
    return 1 if failures == len(ids) else 0


def main() -> None:
    ids = sys.argv[1:] or EXAMPLES
    raise SystemExit(asyncio.run(run(ids)))


if __name__ == "__main__":
    main()
