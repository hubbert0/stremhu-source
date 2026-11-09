"""A kezdőlap szolgáltatáslistája és az állapotellenőrzés."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl

from app.config import Settings, write_private


class Service(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl
    group: str = Field(default="Egyéb", max_length=40)
    note: str = Field(default="", max_length=500)
    check: bool = True


class ServiceList(BaseModel):
    services: list[Service] = Field(max_length=100)


def _service(
    name: str, url: str, group: str, note: str = "", check: bool = True
) -> Service:
    return Service.model_validate(
        {"name": name, "url": url, "group": group, "note": note, "check": check}
    )


def default_services(settings: Settings) -> list[Service]:
    services: list[Service] = []
    if settings.tailnet:
        host = settings.hostnames
        tn = settings.tailnet
        services += [
            _service(
                "StremHU",
                f"https://{host.stremhu}.{tn}",
                "Szerver",
                "nCore-keresés, Torznab, TorBox-helykezelés",
            ),
            _service(
                "AIOStreams",
                f"https://{host.aiostreams}.{tn}/stremio/configure",
                "Szerver",
                "Ezt használja a Stremio",
            ),
            _service(
                "TMDB-addon",
                f"https://{host.tmdb}.{tn}/configure",
                "Szerver",
                "Magyar címek és katalógusok",
            ),
            _service(
                "LeLibrary",
                f"https://{host.lelibrary}.{tn}/configure",
                "Szerver",
                "A TorBoxon lévő filmek és sorozatok a Stremio kezdőlapján",
            ),
            _service(
                "Magyar feliratok",
                f"https://{host.felirat}.{tn}/",
                "Szerver",
                "Felirataddon a feliratok.eu-hoz",
            ),
            _service(
                "Emby-könyvtár",
                f"https://{host.emby}.{tn}/",
                "Szerver",
                "A saját Emby-szerverek a Stremio kezdőlapján",
            ),
            _service(
                "YouTube-mesék",
                f"https://{host.youtube}.{tn}/",
                "Szerver",
                "Magyar mesék a hivatalos YouTube-csatornákról",
            ),
            _service(
                "Állapotfigyelő (tailnet)",
                f"https://{host.pult}.{tn}:8090",
                "Szerver",
                "Gatus – csak Tailscale-lel bekapcsolt eszközről",
                check=False,
            ),
            _service(
                "Mentés (tailnet)",
                f"https://{host.pult}.{tn}:9898",
                "Szerver",
                "Backrest – csak Tailscale-lel bekapcsolt eszközről",
                check=False,
            ),
            _service(
                "Szerverpult (tailnet)",
                f"https://{host.pult}.{tn}",
                "Szerver",
                "A szerveren futó pult – csak Tailscale-lel bekapcsolt eszközről",
                check=False,
            ),
        ]
    services += [
        _service("TorBox", "https://torbox.app/dashboard", "Fiókok", check=False),
        _service(
            "Tailscale admin",
            "https://login.tailscale.com/admin/machines",
            "Fiókok",
            check=False,
        ),
        _service("Contabo", "https://my.contabo.com", "Fiókok", check=False),
        _service(
            "GitHub (fork)",
            "https://github.com/hubbert0/stremhu-source",
            "Fiókok",
            check=False,
        ),
    ]
    return services


def _services_path(directory: Path) -> Path:
    return directory / "services.json"


def load_services(directory: Path, settings: Settings) -> list[Service]:
    try:
        raw = json.loads(_services_path(directory).read_text(encoding="utf-8"))
        return ServiceList.model_validate(raw).services
    except (FileNotFoundError, ValueError):
        return default_services(settings)


def save_services(directory: Path, services: list[Service]) -> None:
    data = ServiceList(services=services).model_dump(mode="json")
    write_private(
        _services_path(directory), json.dumps(data, ensure_ascii=False, indent=2)
    )


def reset_services(directory: Path) -> None:
    _services_path(directory).unlink(missing_ok=True)


async def _check_one(client: httpx.AsyncClient, service: Service) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = await client.get(str(service.url))
        return {
            "name": service.name,
            "ok": response.status_code < 500,
            "code": response.status_code,
            "ms": int((time.monotonic() - started) * 1000),
        }
    except httpx.HTTPError as error:
        return {
            "name": service.name,
            "ok": False,
            "code": None,
            "ms": int((time.monotonic() - started) * 1000),
            "error": type(error).__name__,
        }


async def check_services(
    services: list[Service],
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, Any]]:
    targets = [service for service in services if service.check]
    async with httpx.AsyncClient(
        timeout=8.0, follow_redirects=True, transport=transport
    ) as client:
        return list(await asyncio.gather(*(_check_one(client, s) for s in targets)))
