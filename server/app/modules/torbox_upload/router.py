from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.modules.auth.dependencies import OptionalSessionGuard, SessionGuard
from app.modules.torbox_seed.config import get_torbox_seed_settings
from app.modules.torbox_upload.service import (
    MAX_TORRENT_BYTES,
    TorboxUploadError,
    TorboxUploadService,
    find_magnet,
)
from app.modules.users.models import UserModel

router = APIRouter(prefix="/torbox", tags=["TorBox"])

# A telefonos megosztás (PWA share target) ide küldi a fájlt, és ide tér vissza.
UPLOAD_PAGE = "/torbox"


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class TorboxTorrentResponse(_CamelModel):
    name: str
    state: str
    progress: float
    finished: bool
    size: int
    created_at: datetime | None


class TorboxStatusResponse(_CamelModel):
    enabled: bool
    error: str | None = None
    torrents: list[TorboxTorrentResponse] = []


class TorboxAddResponse(_CamelModel):
    name: str
    hash: str
    message: str


def get_torbox_upload_service() -> TorboxUploadService:
    return TorboxUploadService(get_torbox_seed_settings())


async def _read_upload(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(MAX_TORRENT_BYTES + 1)
    return data or None


@router.get("/status", response_model=TorboxStatusResponse)
async def get_status(
    service: Annotated[TorboxUploadService, Depends(get_torbox_upload_service)],
    _: Annotated[UserModel, Depends(SessionGuard())],
):
    if not service.enabled:
        return TorboxStatusResponse(enabled=False)
    try:
        torrents = await service.recent()
    except TorboxUploadError as error:
        return TorboxStatusResponse(enabled=True, error=str(error))
    return TorboxStatusResponse(
        enabled=True,
        torrents=[
            TorboxTorrentResponse(
                name=t.name,
                state=t.state,
                progress=t.progress,
                finished=t.finished,
                size=t.size,
                created_at=t.created_at,
            )
            for t in torrents
        ],
    )


@router.post("/torrents", response_model=TorboxAddResponse)
async def add_torrent(
    service: Annotated[TorboxUploadService, Depends(get_torbox_upload_service)],
    _: Annotated[UserModel, Depends(SessionGuard())],
    file: Annotated[UploadFile | None, File()] = None,
    magnet: Annotated[str | None, Form()] = None,
):
    data = await _read_upload(file)
    try:
        result = await service.add(
            torrent_file=data,
            filename=file.filename if file else None,
            magnet=magnet,
        )
    except TorboxUploadError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
    return TorboxAddResponse(name=result.name, hash=result.hash, message=result.message)


@router.post("/share", include_in_schema=False)
async def share_target(
    service: Annotated[TorboxUploadService, Depends(get_torbox_upload_service)],
    user: Annotated[UserModel | None, Depends(OptionalSessionGuard())],
    torrent: Annotated[UploadFile | None, File()] = None,
    title: Annotated[str | None, Form()] = None,
    text: Annotated[str | None, Form()] = None,
    url: Annotated[str | None, Form()] = None,
):
    """A telefon „Megosztás” menüjéből érkező torrent vagy magnetlink."""
    if user is None:
        return RedirectResponse(
            f"{UPLOAD_PAGE}?hiba={quote('Előbb jelentkezz be, majd oszd meg újra.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    data = await _read_upload(torrent)
    try:
        result = await service.add(
            torrent_file=data,
            filename=torrent.filename if torrent else None,
            magnet=find_magnet(text, url, title),
        )
    except TorboxUploadError as error:
        return RedirectResponse(
            f"{UPLOAD_PAGE}?hiba={quote(str(error))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        f"{UPLOAD_PAGE}?ok={quote(result.name)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
