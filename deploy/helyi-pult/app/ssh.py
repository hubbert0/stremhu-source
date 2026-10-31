"""SSH-kapcsolat a szerverhez (asyncssh).

- Saját kulcspárt használ (az adatkönyvtárban), a jelszót sosem tárolja.
- A szerver gépkulcsát az első sikeres kapcsolódáskor rögzíti, utána
  csak ezzel a kulccsal fogadja el a szervert (közbeékelődés ellen).
"""

import asyncio
import shlex
from collections.abc import AsyncIterator
from pathlib import Path

import asyncssh

from app.config import Settings, SettingsStore

ENV_KEYS = (
    "TAILNET",
    "STREMHU_HOSTNAME",
    "AIOSTREAMS_HOSTNAME",
    "TMDB_HOSTNAME",
    "PULT_HOSTNAME",
    "LELIBRARY_HOSTNAME",
    "FELIRAT_HOSTNAME",
)


class SSHError(Exception):
    pass


def describe_error(error: BaseException) -> str:
    if isinstance(error, asyncssh.PermissionDenied):
        return "A szerver elutasította a belépést (hibás jelszó, vagy a kulcs nincs telepítve)."
    if isinstance(error, asyncssh.HostKeyNotVerifiable):
        return (
            "A szerver gépkulcsa megváltozott. Ha újratelepítetted a szervert, "
            "a Beállításokban kapcsolódj újra; ha nem, ez támadásra is utalhat."
        )
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "Időtúllépés: a szerver nem válaszol."
    if isinstance(error, OSError):
        return f"Nem sikerült kapcsolódni: {error.strerror or error}"
    if isinstance(error, asyncssh.Error):
        return f"SSH-hiba: {error.reason}"
    return str(error) or type(error).__name__


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in ENV_KEYS:
            values[key] = value.strip().strip('"').strip("'")
    return values


def apply_env(settings: Settings, env: dict[str, str]) -> Settings:
    """A szerver .env-jéből a tailnet és a gépnevek átvétele (a hibásakat kihagyja)."""
    data = settings.model_dump()
    mapping = {
        "STREMHU_HOSTNAME": "stremhu",
        "AIOSTREAMS_HOSTNAME": "aiostreams",
        "TMDB_HOSTNAME": "tmdb",
        "PULT_HOSTNAME": "pult",
        "LELIBRARY_HOSTNAME": "lelibrary",
        "FELIRAT_HOSTNAME": "felirat",
    }
    candidates: list[tuple[str, str | None, str]] = [("TAILNET", None, "tailnet")]
    candidates += [(key, "hostnames", field) for key, field in mapping.items()]
    for env_key, parent, field in candidates:
        value = env.get(env_key)
        if not value:
            continue
        trial = {**data, "hostnames": dict(data["hostnames"])}
        if parent:
            trial[parent][field] = value
        else:
            trial[field] = value
        try:
            Settings.model_validate(trial)
        except ValueError:
            continue
        data = trial
    return Settings.model_validate(data)


class SSHManager:
    def __init__(self, store: SettingsStore) -> None:
        self.store = store
        self.key_path: Path = store.directory / "id_ed25519"
        self._conn: asyncssh.SSHClientConnection | None = None
        self._conn_loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ kulcs
    def ensure_key(self) -> asyncssh.SSHKey:
        if self.key_path.exists():
            return asyncssh.read_private_key(str(self.key_path))
        key = asyncssh.generate_private_key("ssh-ed25519", comment="szerverpult")
        key.write_private_key(str(self.key_path))
        key.write_public_key(str(self.key_path) + ".pub")
        if self.key_path.stat().st_mode & 0o077:
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
        return key

    def public_key(self) -> str:
        key = self.ensure_key()
        return key.export_public_key("openssh").decode("ascii").strip()

    # -------------------------------------------------------- kapcsolat
    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _open(
        self,
        settings: Settings,
        *,
        password: str | None = None,
        trust_new_host: bool = False,
    ) -> asyncssh.SSHClientConnection:
        if not settings.ssh_host:
            raise SSHError("Még nincs beállítva a szerver címe (Beállítások fül).")
        if trust_new_host:
            known_hosts = None
        elif settings.host_key:
            known_hosts = ([asyncssh.import_public_key(settings.host_key)], [], [])
        else:
            raise SSHError("A kapcsolat még nincs beállítva (Beállítások fül).")
        try:
            return await asyncssh.connect(
                settings.ssh_host,
                port=settings.ssh_port,
                username=settings.ssh_user,
                password=password,
                client_keys=None if password else [self.ensure_key()],
                known_hosts=known_hosts,
                agent_path=None,
                config=None,
                connect_timeout=15,
                keepalive_interval=30,
            )
        except (OSError, asyncssh.Error, TimeoutError) as error:
            raise SSHError(describe_error(error)) from error

    async def connection(self) -> asyncssh.SSHClientConnection:
        loop = asyncio.get_running_loop()
        async with self._get_lock():
            conn = self._conn
            if conn is not None and self._conn_loop is loop and not conn.is_closed():
                return conn
            if conn is not None and self._conn_loop is loop:
                conn.close()
            self._conn = await self._open(self.store.load())
            self._conn_loop = loop
            return self._conn

    def reset(self) -> None:
        conn, loop = self._conn, self._conn_loop
        self._conn = None
        self._conn_loop = None
        if conn is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(conn.close)
            except RuntimeError:
                pass

    # ---------------------------------------------------------- beállítás
    async def setup(self, settings: Settings, password: str | None) -> Settings:
        """Első kapcsolódás: gépkulcs rögzítése, kulcs telepítése, .env beolvasása.

        Jelszóval a saját kulcsot is felteszi a szerverre. Jelszó nélkül csak
        azt ellenőrzi, hogy a (kézzel telepített) kulcs működik-e.
        """
        public_key = self.public_key()
        conn = await self._open(
            settings, password=password or None, trust_new_host=True
        )
        try:
            host_key = conn.get_server_host_key()
            if host_key is None:
                raise SSHError("A szerver nem küldött gépkulcsot.")
            settings.host_key = (
                host_key.export_public_key("openssh").decode("ascii").strip()
            )
            if password:
                quoted = shlex.quote(public_key)
                command = (
                    "umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
                    f"(grep -qxF {quoted} ~/.ssh/authorized_keys || "
                    f"echo {quoted} >> ~/.ssh/authorized_keys)"
                )
                result = await conn.run(command, check=False)
                if result.exit_status != 0:
                    raise SSHError(
                        "Nem sikerült a kulcsot telepíteni: "
                        + str(result.stderr or result.stdout or "").strip()
                    )
            env_file = shlex.quote(f"{settings.repo_dir}/deploy/.env")
            result = await conn.run(f"cat {env_file} 2>/dev/null", check=False)
            env = parse_env(str(result.stdout or ""))
        finally:
            conn.close()

        if password:
            # A kulcsos belépésnek is működnie kell, különben nincs értelme menteni.
            check = await self._open(settings)
            check.close()

        settings = apply_env(settings, env)
        self.store.save(settings)
        self.reset()
        return settings

    # ----------------------------------------------------------- parancsok
    async def run(self, command: str, timeout: float = 60) -> tuple[int, str]:
        conn = await self.connection()
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False, stderr=asyncssh.STDOUT), timeout=timeout
            )
        except TimeoutError as error:
            raise SSHError("Időtúllépés a parancs futtatásakor.") from error
        except (OSError, asyncssh.Error) as error:
            self.reset()
            raise SSHError(describe_error(error)) from error
        return int(result.exit_status or 0), str(result.stdout or "")

    async def stream(self, command: str) -> AsyncIterator[str]:
        conn = await self.connection()
        try:
            async with conn.create_process(
                command, stderr=asyncssh.STDOUT, encoding="utf-8", errors="replace"
            ) as process:
                async for line in process.stdout:
                    yield str(line)
                await process.wait()
                if process.exit_status not in (0, None):
                    raise SSHError(
                        f"A parancs hibával állt le (kód: {process.exit_status})."
                    )
        except (OSError, asyncssh.Error) as error:
            self.reset()
            raise SSHError(describe_error(error)) from error

    async def open_terminal(
        self, command: str, cols: int, rows: int
    ) -> asyncssh.SSHClientProcess[str]:
        conn = await self.connection()
        try:
            return await conn.create_process(
                command,
                term_type="xterm-256color",
                term_size=(cols, rows),
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, asyncssh.Error) as error:
            self.reset()
            raise SSHError(describe_error(error)) from error
