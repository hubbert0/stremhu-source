"""Emby-könyvtár – Stremio-bővítmény a saját Emby-szerverekhez.

Minden Stremio-útvonal egy titkos útvonalrész mögött van
(https://<cím>/<EMBY_ADDON_SECRET>/manifest.json), mert a lejátszási linkekben
benne van az Emby-felhasználó tokenje: aki nem ismeri a titkos linket, az
semmit nem lát a könyvtárakból.
"""

import hmac
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.cache import Cache
from app.config import VERSION, Settings, load_settings
from app.emby import EmbyClient
from app.service import EmbyService, parse_extra

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("emby")

LANDING = """<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emby-könyvtár</title>
<body style="font-family:system-ui;max-width:560px;margin:10vh auto;padding:0 16px;line-height:1.5">
<h2>Emby-könyvtár</h2>
<p>Stremio-bővítmény: a saját Emby-szerverek filmjei és sorozatai a Stremio kezdőlapján.</p>
<p>A telepítési link titkos; a szerveren ez a parancs írja ki:<br>
<code>docker compose exec emby python -m app.selftest</code></p>
</body></html>"""

TransportFactory = Callable[[int], httpx.AsyncBaseTransport | None]


def create_app(
    settings: Settings | None = None,
    transport_for: TransportFactory | None = None,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        clients = {
            server.index: EmbyClient(
                server,
                settings.timeout,
                transport_for(server.index) if transport_for else None,
            )
            for server in settings.servers
        }
        cache = Cache(settings.data_dir / "cache.sqlite")
        app.state.service = EmbyService(settings, cache, clients)
        if not settings.secret_ok:
            logger.error(
                "Az EMBY_ADDON_SECRET hiányzik vagy túl rövid – a bővítmény nem szolgál ki semmit. "
                "Futtasd az install.sh-t."
            )
        if not clients:
            logger.warning("Nincs beállítva Emby-szerver (EMBY1_URL ... a .env-ben).")
        try:
            yield
        finally:
            for client in clients.values():
                await client.close()

    app = FastAPI(
        title="Emby-könyvtár",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
    )

    def service(request: Request, secret: str) -> EmbyService:
        if not settings.secret_ok or not hmac.compare_digest(
            secret.encode(), settings.secret.encode()
        ):
            raise HTTPException(status_code=404)
        return request.app.state.service

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": VERSION,
            "servers": len(settings.servers),
            "secret": settings.secret_ok,
        }

    @app.get("/", response_class=HTMLResponse)
    def landing() -> str:
        return LANDING

    @app.get("/{secret}/manifest.json")
    def get_manifest(request: Request, secret: str) -> JSONResponse:
        return JSONResponse(
            service(request, secret).manifest(),
            headers={"Cache-Control": "max-age=600"},
        )

    async def catalog(
        request: Request, secret: str, kind: str, catalog_id: str, extra: str | None
    ) -> JSONResponse:
        metas = await service(request, secret).catalog(
            kind, catalog_id, parse_extra(extra)
        )
        return JSONResponse({"metas": metas}, headers={"Cache-Control": "max-age=300"})

    @app.get("/{secret}/catalog/{kind}/{catalog_id}.json")
    async def get_catalog(
        request: Request, secret: str, kind: str, catalog_id: str
    ) -> JSONResponse:
        return await catalog(request, secret, kind, catalog_id, None)

    @app.get("/{secret}/catalog/{kind}/{catalog_id}/{extra}.json")
    async def get_catalog_extra(
        request: Request, secret: str, kind: str, catalog_id: str, extra: str
    ) -> JSONResponse:
        return await catalog(request, secret, kind, catalog_id, extra)

    @app.get("/{secret}/meta/{kind}/{stremio_id}.json")
    async def get_meta(
        request: Request, secret: str, kind: str, stremio_id: str
    ) -> JSONResponse:
        meta = await service(request, secret).meta(kind, stremio_id)
        if meta is None:
            return JSONResponse({"meta": None}, headers={"Cache-Control": "max-age=60"})
        return JSONResponse({"meta": meta}, headers={"Cache-Control": "max-age=600"})

    @app.get("/{secret}/stream/{kind}/{stremio_id}.json")
    async def get_streams(
        request: Request, secret: str, kind: str, stremio_id: str
    ) -> JSONResponse:
        streams = await service(request, secret).streams(kind, stremio_id)
        return JSONResponse(
            {"streams": streams},
            headers={"Cache-Control": "max-age=300" if streams else "max-age=60"},
        )

    async def image(
        request: Request, secret: str, index: int, item_id: str, kind: str
    ) -> Response:
        found = await service(request, secret).image(index, item_id, kind)
        if found is None:
            raise HTTPException(status_code=404)
        data, content_type = found
        return Response(
            data, media_type=content_type, headers={"Cache-Control": "max-age=604800"}
        )

    @app.get("/{secret}/kep/{index}/{item_id}.jpg")
    async def get_poster(
        request: Request, secret: str, index: int, item_id: str
    ) -> Response:
        return await image(request, secret, index, item_id, "Primary")

    @app.get("/{secret}/hatter/{index}/{item_id}.jpg")
    async def get_backdrop(
        request: Request, secret: str, index: int, item_id: str
    ) -> Response:
        return await image(request, secret, index, item_id, "Backdrop")

    return app


app = create_app()
