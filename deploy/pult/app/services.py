"""A kezdőlap szolgáltatáslistája és az állapotellenőrzés."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl

from app.settings import Settings


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
                f"https://{host['stremhu']}.{tn}",
                "Szerver",
                "nCore-keresés, Torznab, TorBox-helykezelés",
            ),
            _service(
                "AIOStreams",
                f"https://{host['aiostreams']}.{tn}/stremio/configure",
                "Szerver",
                "Ezt használja a Stremio",
            ),
            _service(
                "TMDB-addon",
                f"https://{host['tmdb']}.{tn}/configure",
                "Szerver",
                "Magyar címek és katalógusok",
            ),
            _service(
                "LeLibrary",
                f"https://{host['lelibrary']}.{tn}/configure",
                "Szerver",
                "A TorBoxon lévő filmek és sorozatok a Stremio kezdőlapján",
            ),
            _service(
                "Magyar feliratok",
                f"https://{host['felirat']}.{tn}/",
                "Szerver",
                "Felirataddon a feliratok.eu-hoz",
            ),
            _service(
                "Emby-könyvtár",
                f"https://{host['emby']}.{tn}/",
                "Szerver",
                "A saját Emby-szerverek a Stremio kezdőlapján",
            ),
            _service(
                "Állapotfigyelő",
                f"https://{host['pult']}.{tn}:8090",
                "Szerver",
                "Gatus – mi működik, mi nem; értesítés a telefonra",
                check=False,
            ),
            _service(
                "Mentés",
                f"https://{host['pult']}.{tn}:9898",
                "Szerver",
                "Backrest – titkosított napi mentés",
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


def _services_path(settings: Settings) -> Path:
    return settings.data_dir / "services.json"


def load_services(settings: Settings) -> list[Service]:
    path = _services_path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ServiceList.model_validate(raw).services
    except (FileNotFoundError, ValueError):
        return default_services(settings)


def save_services(settings: Settings, services: list[Service]) -> None:
    path = _services_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ServiceList(services=services).model_dump(mode="json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def _check_one(client: httpx.AsyncClient, service: Service) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = await client.get(str(service.url))
        ok = response.status_code < 500
        return {
            "name": service.name,
            "ok": ok,
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
