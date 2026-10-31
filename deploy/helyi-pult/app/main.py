"""Helyi Szerverpult – a saját gépeden fut (127.0.0.1), a szervert SSH-n éri el."""

import asyncio
import contextlib
import datetime
import json
import secrets
import shlex
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

import asyncssh
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from app import docker_ops, vnc
from app.config import Settings, SettingsStore, data_dir, load_token, static_dir
from app.services import (
    ServiceList,
    check_services,
    load_services,
    reset_services,
    save_services,
)
from app.ssh import SSHError, SSHManager
from app.vault import EncryptedVault, load_vault, save_vault


def _read_version() -> str:
    try:
        from app import _version  # pyright: ignore[reportAttributeAccessIssue]
    except ImportError:  # fejlesztői futtatás
        return "fejlesztői"
    return str(getattr(_version, "VERSION", "ismeretlen"))


VERSION = _read_version()

COOKIE = "szerverpult"
OPEN_PATHS = {"/health", "/login"}

NOT_LOGGED_IN = """<!doctype html><html lang="hu"><meta charset="utf-8">
<title>Szerverpult</title>
<body style="font-family:system-ui;max-width:520px;margin:15vh auto;padding:0 16px">
<h2>Szerverpult</h2>
<p>Ezt az oldalt a <b>Szerverpult.exe</b> nyitja meg. Indítsd el (dupla kattintás) –
ha már fut, csak újra megnyitja ezt a lapot, bejelentkezve.</p></body></html>"""


class LocalGuard:
    """Csak a saját gépről, a programtól kapott tokennel lehet belépni.

    - Host-fejléc: csak 127.0.0.1 / localhost (DNS-rebinding ellen).
    - Süti: a token, amit a program indításkor ad át a böngészőnek.
    - Origin: más weboldal nem küldhet kérést a pultnak.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        state = scope["app"].state
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        allowed_hosts: set[str] = state.allowed_hosts
        if headers.get("host", "") not in allowed_hosts:
            await self._deny(scope, receive, send, 400, "Ismeretlen cím.")
            return
        origin = headers.get("origin")
        if origin and origin not in {f"http://{host}" for host in allowed_hosts}:
            await self._deny(
                scope, receive, send, 403, "Idegen oldalról érkezett kérés."
            )
            return
        if scope["path"] not in OPEN_PATHS:
            cookie = cookie_parser(headers.get("cookie", "")).get(COOKIE, "")
            if not secrets.compare_digest(cookie.encode(), str(state.token).encode()):
                await self._deny(scope, receive, send, 401, "Nincs bejelentkezve.")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(
        scope: Scope, receive: Receive, send: Send, code: int, text: str
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["path"] == "/" and code == 401:
            response: Any = HTMLResponse(NOT_LOGGED_IN, status_code=401)
        else:
            response = JSONResponse({"detail": text}, status_code=code)
        await response(scope, receive, send)


def create_app(directory: Path | None = None, port: int = 8765) -> FastAPI:
    directory = directory or data_dir()
    store = SettingsStore(directory)
    ssh = SSHManager(store)

    app = FastAPI(title="Szerverpult", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = load_token(directory)
    app.state.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app.state.ssh = ssh
    app.state.store = store
    app.add_middleware(LocalGuard)
    app.mount("/static", StaticFiles(directory=static_dir()), name="static")

    def ssh_error(error: SSHError) -> HTTPException:
        return HTTPException(status_code=502, detail=str(error))

    # ------------------------------------------------------------ alap
    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"app": "szerverpult", "version": VERSION}

    @app.get("/login", include_in_schema=False)
    def login(t: str = "") -> RedirectResponse:
        if not secrets.compare_digest(t.encode(), str(app.state.token).encode()):
            raise HTTPException(status_code=403, detail="Érvénytelen belépési link.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE,
            str(app.state.token),
            max_age=400 * 24 * 3600,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            static_dir() / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        settings = store.load()
        return {
            "version": VERSION,
            "configured": settings.configured,
            "ssh": f"{settings.ssh_user}@{settings.ssh_host}"
            if settings.ssh_host
            else "",
            "actions": docker_ops.ACTIONS,
            "data_dir": str(directory),
        }

    # ------------------------------------------------------ beállítások
    class SetupRequest(BaseModel):
        ssh_host: str
        ssh_port: int = Field(default=22, ge=1, le=65535)
        ssh_user: str = "root"
        repo_dir: str = "/root/stremhu-source"
        password: str = Field(default="", max_length=500)

    class LocalSettings(BaseModel):
        vnc_viewer: str = Field(default="", max_length=500)

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        settings = store.load()
        viewer = vnc.find_viewer(settings.vnc_viewer)
        return {
            **settings.model_dump(exclude={"host_key"}),
            "configured": settings.configured,
            "host_key_fingerprint": _fingerprint(settings.host_key),
            "public_key": ssh.public_key(),
            "vnc_found": str(viewer) if viewer else "",
        }

    @app.post("/api/setup")
    async def setup(payload: SetupRequest) -> dict[str, Any]:
        current = store.load()
        try:
            candidate = Settings.model_validate(
                {
                    **current.model_dump(),
                    "ssh_host": payload.ssh_host,
                    "ssh_port": payload.ssh_port,
                    "ssh_user": payload.ssh_user,
                    "repo_dir": payload.repo_dir,
                    "host_key": "",
                }
            )
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=_first_error(error)) from error
        try:
            saved = await ssh.setup(candidate, payload.password or None)
        except SSHError as error:
            raise ssh_error(error) from error
        return {
            "ok": True,
            "tailnet": saved.tailnet,
            "fingerprint": _fingerprint(saved.host_key),
        }

    @app.put("/api/settings/local")
    def put_local_settings(payload: LocalSettings) -> dict[str, str]:
        settings = store.load()
        settings.vnc_viewer = payload.vnc_viewer.strip().strip('"')
        store.save(settings)
        viewer = vnc.find_viewer(settings.vnc_viewer)
        return {"vnc_found": str(viewer) if viewer else ""}

    @app.get("/api/ssh/check")
    async def ssh_check() -> dict[str, Any]:
        try:
            code, output = await ssh.run("hostname && uptime -p", timeout=20)
        except SSHError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": code == 0, "output": output.strip()}

    # ---------------------------------------------------- szolgáltatások
    @app.get("/api/services")
    def get_services() -> ServiceList:
        return ServiceList(services=load_services(directory, store.load()))

    @app.put("/api/services")
    def put_services(payload: ServiceList) -> ServiceList:
        save_services(directory, payload.services)
        return payload

    @app.delete("/api/services")
    def delete_services() -> ServiceList:
        reset_services(directory)
        return ServiceList(services=load_services(directory, store.load()))

    @app.get("/api/status")
    async def get_status() -> list[dict[str, Any]]:
        return await check_services(load_services(directory, store.load()))

    # ------------------------------------------------------------ docker
    @app.get("/api/containers")
    async def get_containers() -> list[dict[str, Any]]:
        try:
            return await docker_ops.list_containers(ssh)
        except SSHError as error:
            raise ssh_error(error) from error

    @app.get("/api/containers/{name}/logs")
    async def get_logs(
        name: str, tail: Annotated[int, Query()] = 300
    ) -> dict[str, str]:
        try:
            return {"logs": await docker_ops.container_logs(ssh, name, tail)}
        except SSHError as error:
            raise ssh_error(error) from error

    @app.post("/api/containers/{name}/restart")
    async def post_restart(name: str) -> dict[str, str]:
        try:
            return {"output": await docker_ops.restart_container(ssh, name)}
        except SSHError as error:
            raise ssh_error(error) from error

    action_lock = asyncio.Lock()

    @app.post("/api/actions/{action}")
    async def post_action(action: str) -> StreamingResponse:
        if action not in docker_ops.ACTIONS:
            raise HTTPException(status_code=404, detail="Ismeretlen művelet.")

        async def body() -> AsyncIterator[str]:
            if action_lock.locked():
                yield "Már fut egy művelet, várd meg, amíg befejeződik.\n"
                return
            async with action_lock:
                async for line in docker_ops.run_action(ssh, action, store.load()):
                    yield line

        return StreamingResponse(
            body(),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ----------------------------------------------------------- terminál
    @app.websocket("/ws/terminal")
    async def terminal(websocket: WebSocket) -> None:
        await websocket.accept()
        cols = _clamp(websocket.query_params.get("cols"), 20, 500, 120)
        rows = _clamp(websocket.query_params.get("rows"), 5, 200, 32)
        settings = store.load()
        deploy = f"{settings.repo_dir}/deploy"
        command = f"cd {shlex.quote(deploy)} 2>/dev/null; exec bash -l"
        try:
            process = await ssh.open_terminal(command, cols, rows)
        except SSHError as error:
            await websocket.send_text(f"\r\n\x1b[31m{error}\x1b[0m\r\n")
            await websocket.close()
            return

        async def pump() -> None:
            # A kapcsolat bármelyik oldalon bármikor megszakadhat.
            with contextlib.suppress(Exception):
                while True:
                    data = await process.stdout.read(8192)
                    if not data:
                        break
                    await websocket.send_text(data)
            with contextlib.suppress(Exception):
                await websocket.send_text(
                    "\r\n\x1b[33m[A munkamenet véget ért.]\x1b[0m\r\n"
                )
                await websocket.close()

        task = asyncio.create_task(pump())
        try:
            while True:
                message = await websocket.receive_text()
                try:
                    event = json.loads(message)
                except ValueError:
                    continue
                if event.get("t") == "i":
                    process.stdin.write(str(event.get("d", "")))
                elif event.get("t") == "r":
                    process.change_terminal_size(
                        _clamp(event.get("c"), 20, 500, cols),
                        _clamp(event.get("r"), 5, 200, rows),
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            process.close()
            task.cancel()

    # -------------------------------------------------------------- VNC
    class VNCRequest(BaseModel):
        address: str = Field(min_length=1, max_length=300)
        password: str = Field(default="", max_length=200)

    @app.post("/api/vnc")
    def post_vnc(payload: VNCRequest) -> dict[str, str]:
        try:
            viewer = vnc.launch(
                store.load().vnc_viewer, payload.address, payload.password
            )
        except vnc.VNCError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"viewer": viewer}

    # ------------------------------------------------------------- széf
    @app.get("/api/vault")
    def get_vault() -> EncryptedVault | None:
        return load_vault(directory)

    @app.put("/api/vault")
    def put_vault(payload: EncryptedVault) -> dict[str, str]:
        payload.updated_at = datetime.datetime.now(datetime.UTC).isoformat()
        save_vault(directory, payload)
        return {"updated_at": payload.updated_at}

    @app.exception_handler(SSHError)
    async def handle_ssh_error(_: Request, error: SSHError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=502)

    return app


def _clamp(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _fingerprint(host_key: str) -> str:
    if not host_key:
        return ""
    try:
        return asyncssh.import_public_key(host_key).get_fingerprint("sha256")
    except (ValueError, asyncssh.KeyImportError):
        return ""


FIELD_NAMES = {
    "ssh_host": "szerver címe",
    "ssh_user": "felhasználó",
    "ssh_port": "port",
    "repo_dir": "repó mappája",
}


def _first_error(error: ValidationError) -> str:
    items = error.errors()
    if not items:
        return "Hibás adat."
    field = ".".join(str(part) for part in items[0]["loc"])
    return f"Hibás adat: {FIELD_NAMES.get(field, field)}."
