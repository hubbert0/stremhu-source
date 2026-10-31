import asyncio
import os
import stat
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSWORD = "teszt-jelszo"

FAKE_DOCKER = """#!/bin/sh
case "$1" in
  ps)
    echo '{"Names":"stremhu-torbox-stremhu-1","Image":"x/stremhu:torbox","State":"running","Status":"Up 2 hours","Labels":"com.docker.compose.project=stremhu-torbox,com.docker.compose.service=stremhu"}'
    echo '{"Names":"magany","Image":"alpine","State":"exited","Status":"Exited (0)","Labels":""}'
    ;;
  logs) echo "naplo-sor-1"; echo "naplo-sor-2" ;;
  restart) echo "$2" ;;
  compose)
    if [ "$2" = "exec" ]; then echo "nincs ilyen szolgaltatas" >&2; exit 1; fi
    echo "compose $2 rendben" ;;
  *) echo "docker $*" ;;
esac
"""


class FakeServer(asyncssh.SSHServer):
    def __init__(self, home: Path) -> None:
        self.home = home

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "root" and password == PASSWORD

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        path = self.home / ".ssh" / "authorized_keys"
        if username != "root" or not path.exists():
            return False
        wanted = key.export_public_key("openssh").decode().split()[:2]
        return any(line.split()[:2] == wanted for line in path.read_text().splitlines())


def make_handler(env: dict[str, str]):
    async def handle(process: asyncssh.SSHServerProcess) -> None:
        if process.get_terminal_type():
            size = process.get_terminal_size()
            process.stdout.write(f"teszt-terminal {size[0]}x{size[1]}\r\n")
            while True:
                try:
                    data = await process.stdin.read(1024)
                except asyncssh.TerminalSizeChanged as change:
                    process.stdout.write(f"MERET:{change.width}x{change.height}\r\n")
                    continue
                if not data:
                    break
                process.stdout.write("ECHO:" + data)
            process.exit(0)
            return
        proc = await asyncio.create_subprocess_shell(
            process.command or "true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await proc.communicate()
        process.stdout.write(out.decode())
        process.stderr.write(err.decode())
        process.exit(proc.returncode or 0)

    return handle


@pytest.fixture
def ssh_server(tmp_path: Path) -> Iterator[SimpleNamespace]:
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / ".env").write_text(
        "TAILNET=pelda-net.ts.net\nSTREMHU_HOSTNAME=stremhu-tb\nTMDB_HOSTNAME=Rossz_Nev!\n"
        "PULT_HOSTNAME=pult\nTORBOX_API_KEY=titkos\n"
    )
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
    }

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict[str, object] = {}

    def runner() -> None:
        asyncio.set_event_loop(loop)

        async def start() -> None:
            host_key = asyncssh.generate_private_key("ssh-ed25519")
            server = await asyncssh.create_server(
                lambda: FakeServer(home),
                "127.0.0.1",
                0,
                server_host_keys=[host_key],
                process_factory=make_handler(env),
                line_editor=False,
                encoding="utf-8",
            )
            holder.update(server=server, port=server.get_port(), host_key=host_key)

        loop.run_until_complete(start())
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert ready.wait(10)
    yield SimpleNamespace(
        port=holder["port"], home=home, repo=repo, host_key=holder["host_key"]
    )
    server = holder["server"]
    loop.call_soon_threadsafe(server.close)  # type: ignore[attr-defined]
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
