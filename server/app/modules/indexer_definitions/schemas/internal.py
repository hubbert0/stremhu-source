from pydantic import BaseModel, ConfigDict


class IndexerDefinition(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: str
    name: str
    url: str
    details_path: str
    requires_full_download: bool
    supports_totp: bool = False


class AuthSessionError(BaseModel):
    pass


class AuthCredentialError(BaseModel):
    message: str | None = None


class AuthOtherError(BaseModel):
    message: str


AuthError = AuthSessionError | AuthCredentialError | AuthOtherError | None


class IndexerDefinitionLogin(BaseModel):
    username: str
    password: str
    totp_secret: str | None = None
    cookies: dict[str, str] | None = None


class IndexerDefinitionLoginPayload(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class IndexerDefinitionTorrent(BaseModel):
    torrent_id: str
    download_url: str
    imdb_id: str | None = None
    seeders: int = 0
    attribute_ids: list[str] = []


class IndexerDefinitionSearchHit(BaseModel):
    """Szabad szöveges keresés egy találata (a kiadás nevével)."""

    torrent_id: str
    name: str
    category: str = ""
    size: int = 0
    seeders: int = 0
    leechers: int = 0
    imdb_id: str | None = None
    download_url: str | None = None


class IndexerDefinitionFindTorrentsResult(BaseModel):
    torrents: list[IndexerDefinitionTorrent] = []
    next_page: int | None = None
