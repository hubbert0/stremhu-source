"""Docker-műveletek a szerveren, SSH-n keresztül.

Minden parancs rögzített szöveg; a változó részeket (konténernév, mappa)
ellenőrizzük és shell-idézőjelek közé tesszük.
"""

import json
import re
import shlex
from collections.abc import AsyncIterator
from typing import Any

from app.config import Settings
from app.ssh import SSHError, SSHManager

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

ACTIONS: dict[str, str] = {
    "update": "Frissítés (git pull + új image-ek + újraindítás)",
    "ps": "Konténerek állapota (docker compose ps)",
    "torbox-status": "TorBox-helykezelés jelentése",
    "disk": "Lemezhely és Docker-tárhely",
    "prune": "Felesleges régi image-ek törlése",
}


def parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            labels[key.strip()] = value.strip()
    return labels


def parse_ps_output(output: str) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        labels = parse_labels(str(item.get("Labels") or ""))
        containers.append(
            {
                "name": str(item.get("Names", "")),
                "image": str(item.get("Image", "")),
                "state": str(item.get("State", "")),
                "status": str(item.get("Status", "")),
                "project": labels.get("com.docker.compose.project", ""),
                "service": labels.get("com.docker.compose.service", ""),
            }
        )
    containers.sort(key=lambda c: (c["project"] == "", c["project"], c["name"]))
    return containers


async def list_containers(ssh: SSHManager) -> list[dict[str, Any]]:
    code, output = await ssh.run("docker ps -a --format '{{json .}}'", timeout=30)
    if code != 0:
        raise SSHError(output.strip() or "A docker ps nem futott le.")
    return parse_ps_output(output)


async def _ensure_known(ssh: SSHManager, name: str) -> None:
    if not NAME_RE.match(name):
        raise SSHError("Érvénytelen konténernév.")
    names = {c["name"] for c in await list_containers(ssh)}
    if name not in names:
        raise SSHError("Nincs ilyen konténer.")


async def container_logs(ssh: SSHManager, name: str, tail: int = 200) -> str:
    await _ensure_known(ssh, name)
    tail = max(10, min(tail, 2000))
    _, output = await ssh.run(
        f"docker logs --tail {tail} --timestamps {shlex.quote(name)} 2>&1", timeout=40
    )
    return output


async def restart_container(ssh: SSHManager, name: str) -> str:
    await _ensure_known(ssh, name)
    code, output = await ssh.run(f"docker restart {shlex.quote(name)}", timeout=120)
    if code != 0:
        raise SSHError(output.strip() or "Az újraindítás nem sikerült.")
    return output


def action_command(action: str, settings: Settings) -> str:
    repo = shlex.quote(settings.repo_dir)
    deploy = shlex.quote(f"{settings.repo_dir}/deploy")
    if action == "update":
        return (
            "set -e; "
            f"echo '$ git pull'; git -C {repo} pull --ff-only; "
            f"cd {deploy}; "
            "echo; echo '$ docker compose pull'; docker compose pull; "
            "echo; echo '$ docker compose up -d'; docker compose up -d; "
            "echo; docker compose ps"
        )
    if action == "ps":
        return f"cd {deploy} && docker compose ps"
    if action == "torbox-status":
        return (
            f"cd {deploy} && docker compose exec -T stremhu "
            "python -m app.modules.torbox_seed.status"
        )
    if action == "disk":
        return "df -h /; echo; docker system df"
    if action == "prune":
        return "docker image prune -f"
    raise SSHError("Ismeretlen művelet.")


async def run_action(
    ssh: SSHManager, action: str, settings: Settings
) -> AsyncIterator[str]:
    try:
        command = action_command(action, settings)
        async for line in ssh.stream(command):
            yield line
        yield "\n✅ Kész.\n"
    except SSHError as error:
        yield f"\n❌ {error}\n"
