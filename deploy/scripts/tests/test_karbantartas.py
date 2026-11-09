"""Az önjavító figyelő és a heti frissítés tesztjei, hamis curl/docker/git-tel."""

import fcntl
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
TAILNET = "pelda.ts.net"

FAKE_CURL = r"""#!/bin/sh
echo "$*" >> "$FAKE_DIR/curl_hivasok"
url=""; data=""; mode=post
while [ $# -gt 0 ]; do
  case "$1" in
    -w) mode=check; shift 2 ;;
    -d) data="$2"; shift 2 ;;
    -o|--max-time|-H) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [ "$mode" = check ]; then
  code=$(awk -v u="$url" '$1 == u { c = $2 } END { print c }' "$FAKE_DIR/kodok" 2>/dev/null)
  code=${code:-200}
  printf '%s' "$code"
  [ "$code" = 000 ] && exit 28
  exit 0
fi
printf '%s\n' "$data" >> "$FAKE_DIR/ntfy"
"""

FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$FAKE_DIR/docker"
if [ "$1 $2 $3" = "compose ps -q" ]; then
  n=$(cat "$FAKE_DIR/ps_szam" 2>/dev/null || echo 0)
  n=$((n + 1)); echo "$n" > "$FAKE_DIR/ps_szam"
  cat "$FAKE_DIR/ps$n" 2>/dev/null
fi
if [ "$1 $2" = "compose up" ]; then
  exit "${FAKE_DOCKER_RC:-0}"
fi
exit 0
"""

FAKE_GIT = r"""#!/bin/sh
case "$*" in
  *rev-parse*) cat "$FAKE_DIR/head" ;;
  *pull*)
    [ "${FAKE_GIT_PULL_RC:-0}" = 0 ] || { echo "error: local changes"; exit 1; }
    echo bbb2222 > "$FAKE_DIR/head"; echo "Updating" ;;
esac
"""

FAKE_INSTALL = r"""#!/bin/sh
echo "auto=$STREMHU_AUTO dir=$STREMHU_DIR" >> "$FAKE_DIR/install"
echo "Konténerek letöltése és indítása..."
if [ "${FAKE_INSTALL_RC:-0}" != 0 ]; then
  printf 'Error: pull access denied "valami" \\ C:\\path\n'
  exit "$FAKE_INSTALL_RC"
fi
echo "Frissítés kész."
"""

HOSTS = {
    "StremHU": "stremhu-tb",
    "AIOStreams": "aiostreams",
    "TMDB-addon": "tmdb",
    "LeLibrary": "lelibrary",
    "Magyar feliratok": "felirat",
    "Emby-könyvtár": "emby",
    "YouTube-mesék": "youtube",
}
PATHS = {
    "StremHU": "/",
    "AIOStreams": "/api/v1/health",
    "TMDB-addon": "/manifest.json",
    "LeLibrary": "/health",
    "Magyar feliratok": "/health",
    "Emby-könyvtár": "/health",
    "YouTube-mesék": "/health",
}


def url(name: str) -> str:
    return f"https://{HOSTS[name]}.{TAILNET}{PATHS[name]}"


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.deploy = root / "deploy"
        self.fake = root / "fake"
        self.state = root / "state"
        self.log = root / "karbantartas.log"
        (self.deploy / "scripts").mkdir(parents=True)
        self.fake.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("kozos.sh", "onjavito.sh", "frissites.sh"):
            shutil.copy(SCRIPTS / name, self.deploy / "scripts" / name)
        for name, body in (
            ("curl", FAKE_CURL),
            ("docker", FAKE_DOCKER),
            ("git", FAKE_GIT),
        ):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        (self.deploy / "install.sh").write_text(FAKE_INSTALL)
        (self.fake / "head").write_text("aaa1111\n")
        self.write_env()
        self.base_env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_DIR": str(self.fake),
            "STREMHU_STATE_DIR": str(self.state),
            "STREMHU_LOG_FILE": str(self.log),
            "STREMHU_UPDATE_WAIT": "0",
        }

    def write_env(self, **extra: str) -> None:
        values = {
            "TAILNET": TAILNET,
            "STREMHU_HOSTNAME": "stremhu-tb",
            "AIOSTREAMS_HOSTNAME": "aiostreams",
            "NTFY_TOPIC": "teszt-tema",
            "ONJAVITO": "1",
            "AUTO_FRISSITES": "1",
            **extra,
        }
        lines = [f"{k}={v}" for k, v in values.items()]
        (self.deploy / ".env").write_text("\n".join(lines) + "\n")

    def set_codes(self, **codes: str) -> None:
        names = {
            "lelibrary": "LeLibrary",
            "stremhu": "StremHU",
            "felirat": "Magyar feliratok",
            "aiostreams": "AIOStreams",
            "tmdb": "TMDB-addon",
            "emby": "Emby-könyvtár",
            "youtube": "YouTube-mesék",
        }
        lines = [f"{url(names[k])} {v}" for k, v in codes.items()]
        (self.fake / "kodok").write_text("\n".join(lines) + "\n")

    def all_codes(self, code: str) -> None:
        self.set_codes(
            **{
                k: code
                for k in (
                    "lelibrary",
                    "stremhu",
                    "felirat",
                    "aiostreams",
                    "tmdb",
                    "emby",
                    "youtube",
                )
            }
        )

    def run(self, script: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(self.deploy / "scripts" / script)],
            env={**self.base_env, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def read(self, name: str) -> str:
        path = self.fake / name
        return path.read_text() if path.exists() else ""

    def restarts(self) -> list[str]:
        return [
            line
            for line in self.read("docker").splitlines()
            if line.startswith("compose up")
        ]

    def notifications(self) -> list[dict[str, object]]:
        return [
            json.loads(line) for line in self.read("ntfy").splitlines() if line.strip()
        ]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# ---------------------------------------------------------------- önjavító


def test_restarts_after_three_failures(env: Env) -> None:
    env.set_codes(lelibrary="000")
    for _ in range(2):
        assert env.run("onjavito.sh").returncode == 0
    assert env.restarts() == []

    assert env.run("onjavito.sh").returncode == 0
    assert env.restarts() == [
        "compose up -d --no-deps --force-recreate ts-lelibrary lelibrary"
    ]
    [note] = env.notifications()
    assert note["topic"] == "teszt-tema"
    assert note["title"] == "LeLibrary újraindítva"
    assert note["priority"] == 4
    assert "LeLibrary újraindítása" in env.log.read_text()


def test_restarts_at_most_once_per_hour(env: Env) -> None:
    env.set_codes(lelibrary="000")
    for _ in range(9):
        env.run("onjavito.sh")
    assert len(env.restarts()) == 1
    assert "egy órán belül már újraindítottam" in env.log.read_text()


def test_restarts_again_after_cooldown(env: Env) -> None:
    env.set_codes(lelibrary="502")
    for _ in range(6):
        env.run("onjavito.sh", STREMHU_COOLDOWN="0")
    assert len(env.restarts()) == 2


def test_success_resets_the_counter(env: Env) -> None:
    env.set_codes(lelibrary="000")
    env.run("onjavito.sh")
    env.run("onjavito.sh")
    env.set_codes(lelibrary="200")
    env.run("onjavito.sh")
    assert "LeLibrary ismét válaszol" in env.log.read_text()
    env.set_codes(lelibrary="000")
    env.run("onjavito.sh")
    env.run("onjavito.sh")
    assert env.restarts() == []


def test_client_errors_count_as_up(env: Env) -> None:
    env.set_codes(stremhu="404", lelibrary="301")
    for _ in range(4):
        env.run("onjavito.sh")
    assert env.restarts() == []
    assert not env.log.exists()


def test_no_restart_when_everything_is_down(env: Env) -> None:
    env.all_codes("000")
    for _ in range(4):
        env.run("onjavito.sh")
    assert env.restarts() == []
    assert env.notifications() == []
    assert "Egyik nyilvános cím sem válaszol" in env.log.read_text()


def test_failed_restart_sends_warning(env: Env) -> None:
    env.set_codes(felirat="000")
    for _ in range(3):
        env.run("onjavito.sh", FAKE_DOCKER_RC="1")
    [note] = env.notifications()
    assert note["title"] == "Magyar feliratok: az újraindítás nem sikerült"
    assert note["priority"] == 5


def test_disabled_does_nothing(env: Env) -> None:
    env.write_env(ONJAVITO="0")
    env.set_codes(lelibrary="000")
    for _ in range(3):
        env.run("onjavito.sh")
    assert env.read("curl_hivasok") == ""
    assert env.restarts() == []


def test_skips_while_update_holds_the_lock(env: Env) -> None:
    env.set_codes(lelibrary="000")
    env.state.mkdir(parents=True, exist_ok=True)
    with open(env.state / "zar", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for _ in range(3):
            assert env.run("onjavito.sh").returncode == 0
    assert env.read("curl_hivasok") == ""
    assert env.restarts() == []


def test_missing_ntfy_topic_is_logged(env: Env) -> None:
    env.write_env(NTFY_TOPIC="")
    env.set_codes(tmdb="000")
    for _ in range(3):
        env.run("onjavito.sh")
    assert len(env.restarts()) == 1
    assert env.notifications() == []
    assert "Nincs NTFY_TOPIC" in env.log.read_text()


# ------------------------------------------------------------ heti frissítés


def test_weekly_update_reports_success(env: Env) -> None:
    (env.fake / "ps1").write_text("a\nb\n")
    (env.fake / "ps2").write_text("a\nc\nd\n")
    result = env.run("frissites.sh")
    assert result.returncode == 0, result.stderr
    assert "auto=1" in env.read("install")
    [note] = env.notifications()
    assert note["title"] == "Heti frissítés kész"
    assert note["priority"] == 2
    message = str(note["message"])
    assert "aaa1111 → bbb2222" in message
    assert "Frissített konténerek: 2." in message
    assert "Minden nyilvános cím működik." in message
    assert "image prune -f" in env.read("docker")


def test_weekly_update_unchanged_code(env: Env) -> None:
    (env.fake / "head").write_text("bbb2222\n")
    (env.fake / "ps1").write_text("a\nb\n")
    (env.fake / "ps2").write_text("a\nb\n")
    env.run("frissites.sh")
    message = str(env.notifications()[0]["message"])
    assert "A program kódja nem változott." in message
    assert "Frissített konténerek: 0." in message


def test_weekly_update_reports_unreachable_service(env: Env) -> None:
    env.set_codes(lelibrary="000")
    env.run("frissites.sh")
    [note] = env.notifications()
    assert note["title"] == "Heti frissítés kész, de valami nem válaszol"
    assert "Most nem válaszol: LeLibrary." in str(note["message"])
    assert note["priority"] == 4


def test_weekly_update_install_failure(env: Env) -> None:
    result = env.run("frissites.sh", FAKE_INSTALL_RC="1")
    assert result.returncode == 1
    [note] = env.notifications()
    assert note["title"] == "Heti frissítés: hiba"
    message = str(note["message"])
    assert 'pull access denied "valami"' in message
    assert "\\ C:\\path" in message
    assert note["priority"] == 5


def test_weekly_update_git_failure(env: Env) -> None:
    result = env.run("frissites.sh", FAKE_GIT_PULL_RC="1")
    assert result.returncode == 1
    assert env.read("install") == ""
    [note] = env.notifications()
    assert "git pull" in str(note["message"])


def test_weekly_update_disabled(env: Env) -> None:
    env.write_env(AUTO_FRISSITES="0")
    env.run("frissites.sh")
    assert env.read("docker") == ""
    assert env.read("install") == ""


# ------------------------------------------------------------------ segédek


def test_json_escape(env: Env) -> None:
    tricky = 'a "b"\\c\td\x01e\r\nmásodik sor'
    script = f'. "{env.deploy}/scripts/kozos.sh"; json_escape "$1"'
    result = subprocess.run(
        ["sh", "-c", script, str(env.deploy / "scripts" / "x.sh"), tricky],
        env=env.base_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(f'"{result.stdout}"') == 'a "b"\\c de\nmásodik sor'
