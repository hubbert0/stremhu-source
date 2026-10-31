"""Magyar feliratok a feliratok.eu-ról – Stremio-felirataddon."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from app.cache import Cache
from app.config import VERSION, Settings, load_settings
from app.feliratok import FeliratokClient
from app.service import SubtitleService, parse_extra
from app.subfile import SubtitleError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("feliratok")


def manifest() -> dict[str, object]:
    return {
        "id": "hu.stremhu.feliratok",
        "version": VERSION,
        "name": "Magyar feliratok",
        "description": "Magyar feliratok a feliratok.eu-ról (Super Subtitles), "
        "helyes ékezetekkel, a kiadáshoz illő sorrendben.",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }


LANDING = """<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Magyar feliratok</title>
<body style="font-family:system-ui;max-width:560px;margin:10vh auto;padding:0 16px;line-height:1.5">
<h2>Magyar feliratok (feliratok.eu)</h2>
<p>Stremio-felirataddon: magyar feliratok a feliratok.eu-ról, helyes ékezetekkel.</p>
<p><a href="stremio://{host}/manifest.json" style="display:inline-block;padding:10px 16px;
background:#3b5bdb;color:#fff;border-radius:8px;text-decoration:none">Telepítés a Stremióba</a></p>
<p>Vagy másold be a Stremio <i>Addons</i> keresőjébe:<br><code>{base}/manifest.json</code></p>
</body></html>"""


def create_app(
    settings: Settings | None = None,
    feliratok_transport: httpx.AsyncBaseTransport | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = FeliratokClient(
            settings.feliratok_url, settings.user_agent, feliratok_transport
        )
        http = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            transport=http_transport,
        )
        cache = Cache(settings.data_dir / "cache.sqlite")
        app.state.service = SubtitleService(settings, cache, client, http)
        try:
            yield
        finally:
            await client.close()
            await http.aclose()

    app = FastAPI(
        title="Magyar feliratok",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
    )

    def service(request: Request) -> SubtitleService:
        return request.app.state.service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.get("/", response_class=HTMLResponse)
    def landing() -> str:
        host = settings.base_url.split("://", 1)[-1]
        return LANDING.format(host=host, base=settings.base_url)

    @app.get("/manifest.json")
    def get_manifest() -> JSONResponse:
        return JSONResponse(manifest(), headers={"Cache-Control": "max-age=3600"})

    async def subtitles(
        request: Request, kind: str, stremio_id: str, extra: str | None
    ) -> JSONResponse:
        found = await service(request).find(kind, stremio_id, parse_extra(extra))
        return JSONResponse(
            {"subtitles": found},
            headers={"Cache-Control": "max-age=3600" if found else "max-age=600"},
        )

    @app.get("/subtitles/{kind}/{stremio_id}.json")
    async def get_subtitles(
        request: Request, kind: str, stremio_id: str
    ) -> JSONResponse:
        return await subtitles(request, kind, stremio_id, None)

    @app.get("/subtitles/{kind}/{stremio_id}/{extra}.json")
    async def get_subtitles_extra(
        request: Request, kind: str, stremio_id: str, extra: str
    ) -> JSONResponse:
        return await subtitles(request, kind, stremio_id, extra)

    @app.get("/sub/{subtitle_id}/{season}/{episode}.srt")
    async def get_srt(
        request: Request, subtitle_id: str, season: int, episode: int
    ) -> Response:
        if not subtitle_id.isdigit() or season < 0 or episode < 0:
            raise HTTPException(status_code=400, detail="Hibás azonosító.")
        try:
            text = await service(request).srt(subtitle_id, season, episode)
        except SubtitleError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except httpx.HTTPError as error:
            logger.warning("Letöltési hiba (%s): %s", subtitle_id, error)
            raise HTTPException(
                status_code=502, detail="A feliratok.eu nem elérhető."
            ) from error
        return PlainTextResponse(
            text,
            media_type="application/x-subrip; charset=utf-8",
            headers={"Cache-Control": "max-age=86400"},
        )

    return app


app = create_app()
