"""Szerverpult – egyszerű webes kezelőfelület a Contabo-szerverhez.

Csak a tailnetről érhető el (Tailscale Serve, Funnel nélkül), és HTTP Basic
hitelesítést kér (PULT_USER / PULT_PASSWORD).
"""

import datetime
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app import docker_ops
from app.services import ServiceList, check_services, load_services, save_services
from app.settings import Settings, load_settings
from app.vault import EncryptedVault, load_vault, save_vault

STATIC_DIR = Path(__file__).parent / "static"

security = HTTPBasic(realm="Szerverpult")


def get_settings() -> Settings:
    return load_settings()


def require_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not settings.password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A PULT_PASSWORD nincs beállítva.",
        )
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.user.encode("utf-8")
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.password.encode("utf-8")
    )
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Hibás felhasználónév vagy jelszó.",
            headers={"WWW-Authenticate": 'Basic realm="Szerverpult"'},
        )


SettingsDep = Annotated[Settings, Depends(get_settings)]

app = FastAPI(
    title="Szerverpult",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    dependencies=[Depends(require_auth)],
)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"}
    )


@app.get("/api/info")
def info(settings: SettingsDep) -> dict[str, object]:
    return {
        "tailnet": settings.tailnet,
        "terminal_url": (
            f"https://{settings.hostnames['pult']}.{settings.tailnet}:8443"
            if settings.tailnet
            else None
        ),
        "actions": docker_ops.ACTIONS,
    }


@app.get("/api/services")
def get_services(settings: SettingsDep) -> ServiceList:
    return ServiceList(services=load_services(settings))


@app.put("/api/services")
def put_services(payload: ServiceList, settings: SettingsDep) -> ServiceList:
    save_services(settings, payload.services)
    return payload


@app.get("/api/status")
async def get_status(settings: SettingsDep) -> list[dict[str, object]]:
    return await check_services(load_services(settings))


@app.get("/api/containers")
async def get_containers() -> list[dict[str, object]]:
    try:
        return await docker_ops.list_containers()
    except docker_ops.CommandError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/containers/{name}/logs")
async def get_logs(name: str, tail: Annotated[int, Query()] = 200) -> dict[str, str]:
    try:
        return {"logs": await docker_ops.container_logs(name, tail)}
    except docker_ops.CommandError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/containers/{name}/restart")
async def post_restart(name: str) -> dict[str, str]:
    try:
        return {"output": await docker_ops.restart_container(name)}
    except docker_ops.CommandError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/actions/{action}")
async def post_action(action: str, settings: SettingsDep) -> StreamingResponse:
    if action not in docker_ops.ACTIONS:
        raise HTTPException(status_code=404, detail="Ismeretlen művelet.")
    return StreamingResponse(
        docker_ops.run_action(action, settings),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/vault")
def get_vault(settings: SettingsDep) -> EncryptedVault | None:
    return load_vault(settings.data_dir)


@app.put("/api/vault")
def put_vault(payload: EncryptedVault, settings: SettingsDep) -> dict[str, str]:
    payload.updated_at = datetime.datetime.now(datetime.UTC).isoformat()
    save_vault(settings.data_dir, payload)
    return {"updated_at": payload.updated_at}
