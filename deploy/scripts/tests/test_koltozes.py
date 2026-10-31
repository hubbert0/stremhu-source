"""A költöztető (deploy/koltozes.sh) tesztje két szimulált szerverrel.

A „régi” és az „új” szerver egy-egy mappa; a hamis ssh a régi oldalon, a hamis
docker a megfelelő oldal köteteiben futtatja a parancsokat. A git és a tar valódi.
"""

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2]
TAILNET = "pelda.ts.net"
PROJECT = "stremhu-torbox"

FAKE_SSH = r"""#!/bin/sh
cmd=""
while [ $# -gt 0 ]; do
  case "$1" in
    -O) exit 0 ;;
    -o) shift 2 ;;
    -*) shift ;;
    *) if [ -z "${host:-}" ]; then host="$1"; else cmd="$1"; fi; shift ;;
  esac
done
FAKE_SIDE=old exec bash -c "$cmd"
"""

FAKE_DOCKER = r"""#!/usr/bin/env python3
import os, subprocess, sys

side = os.environ.get("FAKE_SIDE", "new")
root = os.path.join(os.environ["FAKE_ROOT"], side)
vols = os.path.join(root, "volumes")
os.makedirs(vols, exist_ok=True)
args = sys.argv[1:]
with open(os.path.join(root, "docker.log"), "a") as log:
    log.write(" ".join(args) + "\n")
fail = os.environ.get("FAKE_FAIL", "")

if args[:2] == ["compose", "version"]:
    print("Docker Compose version v2.30.0")
    sys.exit(0)
if args and args[0] == "compose":
    if args[1] == "create":
        for name in filter(None, os.environ.get("FAKE_COMPOSE_VOLUMES", "").split(",")):
            path = os.path.join(vols, name)
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "kepbol.txt"), "w") as f:
                f.write("az image tartalma")
    sys.exit(0)
if args[:2] == ["volume", "ls"]:
    print("\n".join(sorted(os.listdir(vols))))
    sys.exit(0)
if args[:2] == ["volume", "inspect"]:
    sys.exit(0 if os.path.isdir(os.path.join(vols, args[2])) else 1)
if args[:2] == ["volume", "create"]:
    os.makedirs(os.path.join(vols, args[-1]), exist_ok=True)
    print(args[-1])
    sys.exit(0)
if args[0] == "pull":
    sys.exit(0)
if args[0] == "run":
    i, mounts = 1, {}
    while args[i].startswith("-"):
        if args[i] == "-v":
            src, dst = args[i + 1].split(":")[:2]
            mounts[dst] = os.path.join(vols, src)
            i += 2
        else:
            i += 1
    cmd = args[i + 1:]
    if fail == side + "-extract" and "-xpf" in " ".join(cmd):
        sys.exit(1)
    cmd = [c.replace("/adat", mounts["/adat"]) for c in cmd]
    sys.exit(subprocess.run(cmd).returncode)
sys.exit(0)
"""

FAKE_CURL = r"""#!/bin/sh
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-w|--max-time) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
code=$(awk -v u="$url" '$1 == u { c = $2 } END { print c }' "$FAKE_ROOT/kodok" 2>/dev/null)
printf '%s' "${code:-200}"
"""

FAKE_INSTALL = """#!/bin/sh
echo "auto=$STREMHU_AUTO dir=$STREMHU_DIR" >> "$FAKE_ROOT/install.log"
"""

OLD_ENV = f"""TAILNET={TAILNET}
STREMHU_HOSTNAME=stremhu-tb
AIOSTREAMS_HOSTNAME=aiostreams
TMDB_HOSTNAME=tmdb
LELIBRARY_HOSTNAME=lelibrary
FELIRAT_HOSTNAME=felirat
EMBY_HOSTNAME=emby
NTFY_TOPIC=teszt
REPO_DIR=/root/stremhu-source
"""


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in (
            ("ssh", FAKE_SSH),
            ("docker", FAKE_DOCKER),
            ("curl", FAKE_CURL),
        ):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)

        # A GitHub-repó helyett egy helyi repó a hamis telepítővel.
        src = root / "src"
        (src / "deploy").mkdir(parents=True)
        (src / "deploy" / "install.sh").write_text(FAKE_INSTALL)
        git = ["git", "-C", str(src)]
        subprocess.run([*git, "init", "-q", "-b", "torbox"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run(
            [
                *git,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "-m",
                "x",
            ],
            check=True,
        )

        self.old_repo = root / "regi-repo"
        (self.old_repo / "deploy").mkdir(parents=True)
        (self.old_repo / "deploy" / ".env").write_text(OLD_ENV)
        self.new_repo = root / "uj-repo"
        self.cron = root / "cron.d" / "stremhu-torbox"
        self.cron.parent.mkdir()
        self.cron.write_text("*/3 * * * * root onjavito\n")
        self.parked = root / "cron.kikapcsolva"

        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(root / "home"),
            "FAKE_ROOT": str(root),
            "KOLTOZES_REPO_URL": str(src),
            "KOLTOZES_UJ_REPO": str(self.new_repo),
            "KOLTOZES_REGI_REPO": str(self.old_repo),
            "KOLTOZES_CRON_FILE": str(self.cron),
            "KOLTOZES_CRON_PARKED": str(self.parked),
            "KOLTOZES_IGEN": "1",
            "KOLTOZES_ROOT_ELLENORZES": "0",
            "KOLTOZES_APT_CONF": str(root / "apt-varakozas.conf"),
            "KOLTOZES_GEPAZONOSITO": "0",
            "KOLTOZES_IDOZONA": "0",
            "KOLTOZES_SSH_KULCSOK": "0",
            "KOLTOZES_ELLENORZES_SEC": "0",
            "KOLTOZES_KOROK_JAVITAS_ELOTT": "2",
            "KOLTOZES_KOROK_JAVITAS_UTAN": "2",
        }

    def volume(self, side: str, name: str) -> Path:
        path = self.root / side / "volumes" / f"{PROJECT}_{name}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def log(self, side: str) -> str:
        path = self.root / side / "docker.log"
        return path.read_text() if path.exists() else ""

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(DEPLOY / "koltozes.sh"), "10.0.0.1"],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = World(tmp_path)
    db = w.volume("old", "lelibrary-db")
    (db / "base" / "1").mkdir(parents=True)
    (db / "base" / "1" / "tabla").write_bytes(os.urandom(4096))
    (db / "PG_VERSION").write_text("16\n")
    if os.geteuid() == 0:
        os.chown(db / "PG_VERSION", 70, 70)
    ts = w.volume("old", "ts-lelibrary-state")
    (ts / "tailscaled.state").write_text('{"node":"kulcs"}')
    w.volume("old", "pult-data")  # üres kötet
    return w


def test_full_migration(world: World) -> None:
    result = world.run(
        FAKE_COMPOSE_VOLUMES=f"{PROJECT}_lelibrary-db,{PROJECT}_ts-lelibrary-state,{PROJECT}_pult-data"
    )
    assert result.returncode == 0, result.stdout + result.stderr

    new_db = world.root / "new" / "volumes" / f"{PROJECT}_lelibrary-db"
    old_db = world.root / "old" / "volumes" / f"{PROJECT}_lelibrary-db"
    assert (new_db / "base" / "1" / "tabla").read_bytes() == (
        old_db / "base" / "1" / "tabla"
    ).read_bytes()
    assert not (new_db / "kepbol.txt").exists(), (
        "az image-tartalom helyére a régi adat kerül"
    )
    if os.geteuid() == 0:
        assert (new_db / "PG_VERSION").stat().st_uid == 70
    state = (
        world.root
        / "new"
        / "volumes"
        / f"{PROJECT}_ts-lelibrary-state"
        / "tailscaled.state"
    )
    assert state.read_text() == '{"node":"kulcs"}'
    assert (world.root / "new" / "volumes" / f"{PROJECT}_pult-data").is_dir()

    assert "compose stop" in world.log("old")
    assert "compose start" not in world.log("old")
    assert not world.cron.exists() and world.parked.exists()
    assert (world.new_repo / "deploy" / ".env").read_text().count(
        f"REPO_DIR={world.new_repo}"
    ) == 1
    assert "auto=1" in (world.root / "install.log").read_text()
    assert "Minden nyilvános cím működik" in result.stdout


def test_volume_missing_from_compose_is_created(world: World) -> None:
    result = world.run(FAKE_COMPOSE_VOLUMES="")
    assert result.returncode == 0, result.stderr
    assert (
        "volume create --label com.docker.compose.project=stremhu-torbox"
        in world.log("new")
    )


def test_failed_copy_restarts_the_old_system(world: World) -> None:
    result = world.run(FAKE_FAIL="new-extract")
    assert result.returncode != 0
    assert "compose stop" in world.log("old")
    assert "compose start" in world.log("old")
    assert world.cron.exists() and not world.parked.exists()
    assert not (world.root / "install.log").exists()
    assert "a régi rendszert visszaindítom" in result.stdout


def test_wrong_old_server_changes_nothing(world: World) -> None:
    (world.old_repo / "deploy" / ".env").unlink()
    result = world.run()
    assert result.returncode != 0
    assert "nincs" in result.stderr
    assert "compose stop" not in world.log("old")
    assert world.cron.exists()


def test_unreachable_service_gets_one_restart(world: World) -> None:
    (world.root / "kodok").write_text(f"https://lelibrary.{TAILNET}/health 000\n")
    result = world.run(FAKE_COMPOSE_VOLUMES=f"{PROJECT}_lelibrary-db")
    assert result.returncode == 0, result.stderr
    assert (
        "compose up -d --no-deps --force-recreate ts-lelibrary lelibrary"
        in world.log("new")
    )
    assert "Még nem válaszol" in result.stdout
    assert "LeLibrary" in result.stdout
