"""YouTube-mesék – Stremio/Nuvio-bővítmény a hivatalos magyar mesecsatornákhoz.

A bővítmény nem titkos: csak nyilvános YouTube-videókra mutat, és a Stremio-
kérések nem fogyasztanak YouTube-kvótát (a helyi tárból dolgoznak).
"""

import asyncio
import contextlib
import html
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.cache import Cache
from app.config import VERSION, Settings, load_settings
from app.index import Index, Indexer
from app.service import YouTubeService, parse_extra
from app.tmdb import TmdbClient
from app.youtube import YouTubeClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# Az httpx a teljes címet írná ki – abban API-kulcs van.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("youtube")

CHECK_EVERY_SECONDS = 3600


async def refresh_loop(app: FastAPI, settings: Settings) -> None:
    indexer: Indexer = app.state.indexer
    while True:
        try:
            await indexer.refresh(settings.channels, settings.refresh_hours)
        except Exception:  # a ciklus nem állhat le egy váratlan hiba miatt
            logger.exception("A csatornák frissítése megszakadt")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def create_app(
    settings: Settings | None = None,
    youtube_transport: httpx.AsyncBaseTransport | None = None,
    tmdb_transport: httpx.AsyncBaseTransport | None = None,
    start_refresh: bool = True,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        index = Index(settings.data_dir / "index.sqlite")
        cache = Cache(settings.data_dir / "cache.sqlite")
        youtube = YouTubeClient(
            settings.youtube_api_key, settings.timeout, youtube_transport
        )
        tmdb = (
            TmdbClient(settings.tmdb_api_key, cache, settings.timeout, tmdb_transport)
            if settings.tmdb_api_key
            else None
        )
        app.state.index = index
        app.state.indexer = Indexer(youtube, index)
        app.state.service = YouTubeService(settings, index, tmdb)
        task = None
        if not settings.youtube_api_key:
            logger.error(
                "Nincs YOUTUBE_API_KEY – a csatornák nem olvashatók be (README)."
            )
        elif start_refresh:
            task = asyncio.create_task(refresh_loop(app, settings))
        if tmdb is None:
            logger.warning(
                "Nincs TMDB_API_KEY – csak a YouTube-mesék katalógus működik."
            )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await youtube.close()
            if tmdb is not None:
                await tmdb.close()

    app = FastAPI(
        title="YouTube-mesék",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
    )

    def status(request: Request) -> dict[str, object]:
        index: Index = request.app.state.index
        channels = index.channels()
        last = index.get_meta("last_refresh")
        return {
            "status": "ok",
            "version": VERSION,
            "youtube_key": bool(settings.youtube_api_key),
            "tmdb_key": bool(settings.tmdb_api_key),
            "channels": len(channels),
            "videos": sum(count for _, _, count, _ in channels),
            "refreshing": request.app.state.indexer.running,
            "last_refresh": float(last) if last else None,
            "last_error": index.get_meta("last_error"),
        }

    @app.get("/health")
    def health(request: Request) -> dict[str, object]:
        return status(request)

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request) -> str:
        info = status(request)
        manifest = f"{settings.base_url}/manifest.json"
        rows = "".join(
            f"<li>{html.escape(title)} – {count} videó"
            f" ({time.strftime('%Y-%m-%d %H:%M', time.localtime(updated))})</li>"
            for _, title, count, updated in request.app.state.index.channels()
        )
        warning = ""
        if not settings.youtube_api_key:
            warning = "<p><b>Hiányzik a YOUTUBE_API_KEY a .env-ből.</b></p>"
        elif info["refreshing"] and not rows:
            warning = "<p>A csatornák beolvasása folyamatban, pár perc múlva frissítsd az oldalt.</p>"
        return f"""<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube-mesék</title>
<body style="font-family:system-ui;max-width:600px;margin:8vh auto;padding:0 16px;line-height:1.5">
<h2>YouTube-mesék</h2>
<p>Magyar mesék a hivatalos YouTube-csatornákról, a Stremióban és a Nuvióban.</p>
{warning}
<p><b>Telepítési link</b> (Nuvio: Bővítmények → hozzáadás):<br>
<code style="word-break:break-all">{html.escape(manifest)}</code></p>
<p><a href="stremio://{html.escape(manifest.split("://", 1)[-1])}">Telepítés a Stremióba</a></p>
<h3>Beolvasott csatornák</h3><ul>{rows or "<li>még egy sem</li>"}</ul>
</body></html>"""

    def service(request: Request) -> YouTubeService:
        return request.app.state.service

    @app.get("/manifest.json")
    def get_manifest(request: Request) -> JSONResponse:
        return JSONResponse(
            service(request).manifest(), headers={"Cache-Control": "max-age=300"}
        )

    @app.get("/catalog/{kind}/{catalog_id}.json")
    def get_catalog(request: Request, kind: str, catalog_id: str) -> JSONResponse:
        metas = service(request).catalog(kind, catalog_id, {})
        return JSONResponse({"metas": metas}, headers={"Cache-Control": "max-age=1800"})

    @app.get("/catalog/{kind}/{catalog_id}/{extra}.json")
    def get_catalog_extra(
        request: Request, kind: str, catalog_id: str, extra: str
    ) -> JSONResponse:
        metas = service(request).catalog(kind, catalog_id, parse_extra(extra))
        return JSONResponse({"metas": metas}, headers={"Cache-Control": "max-age=1800"})

    @app.get("/meta/{kind}/{stremio_id}.json")
    def get_meta(request: Request, kind: str, stremio_id: str) -> JSONResponse:
        meta = service(request).meta(kind, stremio_id)
        return JSONResponse({"meta": meta}, headers={"Cache-Control": "max-age=1800"})

    @app.get("/stream/{kind}/{stremio_id}.json")
    async def get_streams(request: Request, kind: str, stremio_id: str) -> JSONResponse:
        streams = await service(request).streams(kind, stremio_id)
        return JSONResponse(
            {"streams": streams},
            headers={"Cache-Control": "max-age=3600" if streams else "max-age=600"},
        )

    return app


app = create_app()
