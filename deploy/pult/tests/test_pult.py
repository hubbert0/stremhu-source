import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import docker_ops
from app.main import app
from app.services import _service as service
from app.services import check_services, default_services
from app.settings import load_settings

AUTH = ("admin", "titok123")
VAULT = {"v": 1, "salt": "A" * 24, "iv": "B" * 16, "iter": 600000, "data": "C" * 40}


def test_auth_required(env: Path) -> None:
    client = TestClient(app)
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/info", auth=("admin", "rossz")).status_code == 401
    assert client.get("/api/info", auth=AUTH).status_code == 200


def test_missing_password_disables_everything(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULT_PASSWORD", "")
    client = TestClient(app)
    assert client.get("/api/info", auth=("admin", "")).status_code == 503


def test_info_terminal_url(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULT_HOSTNAME", "szerver")
    data = TestClient(app).get("/api/info", auth=AUTH).json()
    assert data["terminal_url"] == "https://szerver.pelda.ts.net:8443"
    assert "update" in data["actions"]


def test_index_served(env: Path) -> None:
    response = TestClient(app).get("/", auth=AUTH)
    assert response.status_code == 200
    assert "Szerverpult" in response.text


def test_default_services_use_tailnet(env: Path) -> None:
    services = default_services(load_settings())
    urls = [str(s.url) for s in services]
    assert "https://stremhu-tb.pelda.ts.net/" in urls
    assert any(u.startswith("https://aiostreams.pelda.ts.net/") for u in urls)
    assert "https://lelibrary.pelda.ts.net/configure" in urls


def test_default_services_without_tailnet(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAILNET", "")
    names = [s.name for s in default_services(load_settings())]
    assert "StremHU" not in names
    assert "TorBox" in names


def test_services_roundtrip(env: Path) -> None:
    client = TestClient(app)
    payload = {
        "services": [
            {
                "name": "Teszt",
                "url": "https://example.com",
                "group": "X",
                "note": "",
                "check": False,
            }
        ]
    }
    assert client.put("/api/services", json=payload, auth=AUTH).status_code == 200
    stored = json.loads((env / "services.json").read_text(encoding="utf-8"))
    assert stored["services"][0]["name"] == "Teszt"
    listed = client.get("/api/services", auth=AUTH).json()["services"]
    assert [s["name"] for s in listed] == ["Teszt"]


def test_services_rejects_bad_url(env: Path) -> None:
    payload = {"services": [{"name": "Rossz", "url": "javascript:alert(1)"}]}
    response = TestClient(app).put("/api/services", json=payload, auth=AUTH)
    assert response.status_code == 422


def test_vault_roundtrip(env: Path) -> None:
    client = TestClient(app)
    assert client.get("/api/vault", auth=AUTH).json() is None
    response = client.put("/api/vault", json=VAULT, auth=AUTH)
    assert response.status_code == 200
    assert response.json()["updated_at"]
    stored = client.get("/api/vault", auth=AUTH).json()
    assert stored["data"] == VAULT["data"]
    assert stored["iter"] == 600000


@pytest.mark.parametrize(
    "change",
    [{"iter": 1000}, {"v": 2}, {"data": ""}, {"salt": "rövid"}],
)
def test_vault_validation(env: Path, change: dict[str, object]) -> None:
    response = TestClient(app).put("/api/vault", json={**VAULT, **change}, auth=AUTH)
    assert response.status_code == 422


def test_unknown_action(env: Path) -> None:
    response = TestClient(app).post("/api/actions/rm-rf", auth=AUTH)
    assert response.status_code == 404


def test_parse_ps_output() -> None:
    lines = "\n".join(
        [
            json.dumps(
                {
                    "Names": "stremhu-torbox-stremhu-1",
                    "Image": "ghcr.io/x/stremhu-source:torbox",
                    "State": "running",
                    "Status": "Up 2 hours",
                    "Labels": "com.docker.compose.project=stremhu-torbox,com.docker.compose.service=stremhu",
                }
            ),
            "nem json",
            json.dumps(
                {
                    "Names": "magany",
                    "Image": "alpine",
                    "State": "exited",
                    "Status": "Exited (0)",
                    "Labels": "",
                }
            ),
            "",
        ]
    )
    containers = docker_ops.parse_ps_output(lines)
    assert [c["name"] for c in containers] == ["stremhu-torbox-stremhu-1", "magany"]
    assert containers[0]["project"] == "stremhu-torbox"
    assert containers[0]["service"] == "stremhu"
    assert containers[1]["project"] == ""


def test_invalid_container_name_rejected() -> None:
    with pytest.raises(docker_ops.CommandError):
        asyncio.run(docker_ops.container_logs("../../etc; rm -rf /", 50))


def test_check_services() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "jo.example.com":
            return httpx.Response(200)
        if request.url.host == "rossz.example.com":
            return httpx.Response(502)
        raise httpx.ConnectError("nincs kapcsolat", request=request)

    services = [
        service("Jó", "https://jo.example.com", "X"),
        service("Rossz", "https://rossz.example.com", "X"),
        service("Halott", "https://halott.example.com", "X"),
        service("Kihagyott", "https://kihagy.example.com", "X", check=False),
    ]
    results = asyncio.run(
        check_services(services, transport=httpx.MockTransport(handler))
    )
    by_name = {r["name"]: r for r in results}
    assert set(by_name) == {"Jó", "Rossz", "Halott"}
    assert by_name["Jó"]["ok"] is True
    assert by_name["Rossz"]["ok"] is False and by_name["Rossz"]["code"] == 502
    assert (
        by_name["Halott"]["ok"] is False
        and by_name["Halott"]["error"] == "ConnectError"
    )


def test_command_env_drops_pult_settings(env: Path) -> None:
    clean = docker_ops.command_env()
    assert "TAILNET" not in clean
    assert "PULT_PASSWORD" not in clean
    assert "PATH" in clean
