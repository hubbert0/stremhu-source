"""Docker-műveletek a docker CLI-n keresztül (a gazdagép docker.sock-ján).

Minden parancs rögzített argumentumlistával fut (nincs shell), a konténernevet
pedig a ténylegesen létező konténerek listájával ellenőrizzük.
"""

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.settings import SELF_SERVICES, Settings

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# Egyszerre csak egy hosszabb művelet fusson.
action_lock = asyncio.Lock()


class CommandError(Exception):
    pass


def command_env() -> dict[str, str]:
    """Tiszta környezet a parancsoknak.

    A pult saját környezeti változói (TAILNET, *_HOSTNAME…) a konténer
    indításakori értékek; ha a docker compose megkapná őket, felülírnák a
    .env-ben azóta módosított értékeket.
    """
    keep = ("PATH", "HOME", "LANG", "TZ", "DOCKER_HOST")
    return {key: os.environ[key] for key in keep if key in os.environ}


async def run(args: Sequence[str], timeout: float = 60) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=command_env(),
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        raise CommandError(f"Időtúllépés: {' '.join(args[:3])}…") from None
    text = output.decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise CommandError(text.strip() or f"Hibakód: {process.returncode}")
    return text


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
        labels = parse_labels(str(item.get("Labels") or ""))
        containers.append(
            {
                "name": item.get("Names", ""),
                "image": item.get("Image", ""),
                "state": item.get("State", ""),
                "status": item.get("Status", ""),
                "project": labels.get("com.docker.compose.project", ""),
                "service": labels.get("com.docker.compose.service", ""),
            }
        )
    containers.sort(key=lambda c: (c["project"] == "", c["project"], c["name"]))
    return containers


async def list_containers() -> list[dict[str, Any]]:
    output = await run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=20)
    return parse_ps_output(output)


async def _ensure_known(name: str) -> None:
    if not NAME_RE.match(name):
        raise CommandError("Érvénytelen konténernév.")
    names = {container["name"] for container in await list_containers()}
    if name not in names:
        raise CommandError("Nincs ilyen konténer.")


async def container_logs(name: str, tail: int = 200) -> str:
    await _ensure_known(name)
    tail = max(10, min(tail, 2000))
    return await run(
        ["docker", "logs", "--tail", str(tail), "--timestamps", name], timeout=30
    )


async def restart_container(name: str) -> str:
    await _ensure_known(name)
    return await run(["docker", "restart", name], timeout=120)


def _compose(settings: Settings, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(settings.compose_dir),
        "-f",
        str(settings.compose_file),
        *args,
    ]


async def stream(args: Sequence[str], title: str) -> AsyncIterator[str]:
    yield f"\n$ {title}\n"
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=command_env(),
    )
    assert process.stdout is not None
    async for raw in process.stdout:
        yield raw.decode("utf-8", errors="replace")
    code = await process.wait()
    if code != 0:
        raise CommandError(f"A parancs hibával állt le (kód: {code}).")


async def project_services(settings: Settings) -> list[str]:
    output = await run(_compose(settings, "config", "--services"), timeout=30)
    return [line.strip() for line in output.splitlines() if line.strip()]


ACTIONS: dict[str, str] = {
    "update": "Frissítés (git pull + új image-ek + újraindítás)",
    "torbox-status": "TorBox-helykezelés jelentése",
    "disk": "Lemezhely és Docker-tárhely",
    "self-update": "A pult frissítése",
}


async def run_action(action: str, settings: Settings) -> AsyncIterator[str]:
    """Egy előre meghatározott művelet futtatása, soronként visszaadva a kimenetet."""
    if action not in ACTIONS:
        raise CommandError("Ismeretlen művelet.")

    if action_lock.locked():
        yield "Már fut egy művelet, várd meg, amíg befejeződik.\n"
        return

    async with action_lock:
        try:
            if action == "update":
                async for line in stream(
                    ["git", "-C", str(settings.repo_dir), "pull", "--ff-only"],
                    "git pull",
                ):
                    yield line
                services = [
                    s
                    for s in await project_services(settings)
                    if s not in SELF_SERVICES
                ]
                async for line in stream(
                    _compose(settings, "pull", *services), "docker compose pull"
                ):
                    yield line
                async for line in stream(
                    _compose(settings, "up", "-d", *services), "docker compose up -d"
                ):
                    yield line

            elif action == "torbox-status":
                async for line in stream(
                    _compose(
                        settings,
                        "exec",
                        "-T",
                        "stremhu",
                        "python",
                        "-m",
                        "app.modules.torbox_seed.status",
                    ),
                    "TorBox-jelentés",
                ):
                    yield line

            elif action == "disk":
                async for line in stream(["df", "-h", "/"], "df -h /"):
                    yield line
                async for line in stream(
                    ["docker", "system", "df"], "docker system df"
                ):
                    yield line

            elif action == "self-update":
                async for line in stream(
                    ["git", "-C", str(settings.repo_dir), "pull", "--ff-only"],
                    "git pull",
                ):
                    yield line
                async for line in stream(
                    _compose(settings, "pull", "pult", "terminal"),
                    "docker compose pull pult terminal",
                ):
                    yield line
                # A pult nem cserélheti le saját magát futás közben, ezért egy
                # külön, rövid életű segédkonténer végzi el az újraindítást.
                helper = [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    "pult-self-update",
                    "-v",
                    "/var/run/docker.sock:/var/run/docker.sock",
                    "-v",
                    f"{settings.repo_dir}:{settings.repo_dir}",
                    settings.pult_image,
                    "sh",
                    "-c",
                    "sleep 3 && "
                    + " ".join(_compose(settings, "up", "-d", "pult", "terminal")),
                ]
                async for line in stream(helper, "segédkonténer indítása"):
                    yield line
                yield "\nA pult néhány másodperc múlva újraindul – frissítsd az oldalt.\n"

            yield "\n✅ Kész.\n"
        except CommandError as error:
            yield f"\n❌ {error}\n"
