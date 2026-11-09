"""Külön Nuvio-bővítmény: nCore-keresés, lejátszás a TorBoxon át.

Telepítési link: <app_url>/api/<api_key>/ncore/manifest.json

Külön bővítmény, hogy ne kelljen a StremHU saját bővítményét telepíteni (az a
szerveren át játszana le), és a Nuvio csak a saját azonosítóinkkal
(stremhu-tb:...) kérdezze.
"""

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from app.config import config
from app.modules.auth.dependencies import ApiKeyGuard
from app.modules.settings.dependencies import get_settings_service
from app.modules.settings.service import SettingsService
from app.modules.stremio.enums import ContentType, ShortManifestResource
from app.modules.stremio.schemas import (
    FullManifestResource,
    Manifest,
    ManifestBehaviorHints,
    MetaResponse,
    StremioCatalogResponse,
    StremioStreams,
)
from app.modules.torbox_play import ajanlo, stremio as adapter
from app.modules.torbox_play.service import TorboxPlayError, TorboxPlayService
from app.modules.users.models import UserModel

router = APIRouter(prefix="/{api_key}/ncore", tags=["nCore-keresés"])

Service = Annotated[TorboxPlayService, Depends(adapter.get_torbox_play_service)]
Settings = Annotated[SettingsService, Depends(get_settings_service)]
User = Annotated[UserModel, Depends(ApiKeyGuard())]


def _extra(extra: str, name: str) -> str | None:
    for part in extra.split("&"):
        if part.startswith(f"{name}="):
            return unquote(part.removeprefix(f"{name}="))
    return None


@router.get("/manifest.json", response_model=Manifest, response_model_exclude_none=True)
def manifest(settings: Settings, _: User) -> Manifest:
    version = config.version.removeprefix("v")
    if not version[:1].isdigit():
        version = "0.0.0"
    return Manifest(
        id="hu.stremhu-source.ncore-torbox",
        version=version,
        name="nCore-keresés (TorBox)",
        description=(
            "Szabad szöveges keresés az nCore-on (pl. „X-Faktor S14E11”), és "
            "külön nCore-forráslista a rendes adatlapokon is; lejátszás a "
            "TorBoxon át, részletes hang- és feliratadatokkal (NFO)."
        ),
        resources=[
            ShortManifestResource.CATALOG,
            ShortManifestResource.META,
            # Forrást a rendes (TMDB/IMDb) adatlapokhoz is adunk: ott külön
            # „nCore-keresés (TorBox)” fül jelenik meg. Adatlapot nem.
            FullManifestResource(
                name=ShortManifestResource.STREAM,
                types=[ContentType.MOVIE, ContentType.SERIES],
                id_prefixes=[adapter.PREFIX, "tt"],
            ),
        ],
        types=[ContentType.MOVIE, ContentType.SERIES],
        id_prefixes=[adapter.PREFIX],
        catalogs=adapter.manifest_catalogs() + ajanlo.manifest_catalogs(),
        behavior_hints=ManifestBehaviorHints(
            configurable=False, configuration_required=False
        ),
        logo=f"{settings.get_app_url()}/logo.png",
    )


@router.get(
    "/catalog/{media_type}/{catalog_id}.json", response_model=StremioCatalogResponse
)
async def catalog(
    media_type: str, catalog_id: str, service: Service, _: User
) -> StremioCatalogResponse:
    # Az ajánló kezdőlapsor; a keresőhöz kötelező a keresett szöveg.
    if media_type in ("movie", "series") and ajanlo.is_ajanlo(catalog_id):
        metas = await ajanlo.catalog(service, media_type, catalog_id)
        return StremioCatalogResponse(metas=metas)
    return StremioCatalogResponse(metas=[])


@router.get(
    "/catalog/{media_type}/{catalog_id}/{extra}.json",
    response_model=StremioCatalogResponse,
)
async def catalog_with_extra(
    media_type: str, catalog_id: str, extra: str, service: Service, _: User
) -> StremioCatalogResponse:
    if media_type not in ("movie", "series"):
        return StremioCatalogResponse(metas=[])
    if ajanlo.is_ajanlo(catalog_id):
        # Egy oldal van; a további oldalak (skip) üresek.
        if (_extra(extra, "skip") or "0") not in ("", "0"):
            return StremioCatalogResponse(metas=[])
        metas = await ajanlo.catalog(service, media_type, catalog_id)
        return StremioCatalogResponse(metas=metas)
    if catalog_id != adapter.CATALOG_ID:
        return StremioCatalogResponse(metas=[])
    metas = await adapter.catalog(service, media_type, _extra(extra, "search"))
    return StremioCatalogResponse(metas=metas)


@router.get(
    "/meta/{media_type}/{meta_id}.json",
    response_model=MetaResponse,
    response_model_exclude_none=True,
)
async def meta(
    media_type: str, meta_id: str, service: Service, _: User
) -> MetaResponse:
    if media_type not in ("movie", "series") or not adapter.is_own_id(meta_id):
        return MetaResponse(meta={})
    found = await adapter.meta(service, media_type, meta_id)
    return MetaResponse(meta=found or {})


@router.get("/stream/{media_type}/{stream_id}.json", response_model=StremioStreams)
async def stream(
    media_type: str, stream_id: str, service: Service, settings: Settings, user: User
) -> StremioStreams:
    if media_type not in ("movie", "series"):
        return StremioStreams(streams=[])
    if not adapter.is_own_id(stream_id) and not adapter.is_imdb_id(stream_id):
        return StremioStreams(streams=[])
    streams = await adapter.streams(
        service, stream_id, user, settings.get_app_url(), media_type
    )
    return StremioStreams(streams=streams)


@router.get("/play/{torrent_id}/{file_index}", include_in_schema=False)
async def play(
    torrent_id: str, file_index: str, service: Service, settings: Settings, user: User
):
    """Lejátszáskor: ha a verzió még nincs a TorBoxon, most kerül fel, és a
    „még tölti” videóra irányítunk; ha kész, a friss TorBox-linkre."""
    if not torrent_id.isdigit() or not (file_index.isdigit() or file_index == "auto"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        url = await service.play_url(
            torrent_id, None if file_index == "auto" else int(file_index)
        )
    except TorboxPlayError as error:
        return PlainTextResponse(
            str(error), status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    if url is None:
        url = f"{settings.get_app_url()}/api/{user.api_key}/ncore/varj.mp4"
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/varj.mp4", include_in_schema=False)
def wait_video(_: User) -> FileResponse:
    if not adapter.WAIT_VIDEO.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(
        adapter.WAIT_VIDEO,
        media_type="video/mp4",
        headers={"Cache-Control": "max-age=86400"},
    )
