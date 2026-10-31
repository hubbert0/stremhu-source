import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest
from conftest import PASSWORD
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import vnc
from app.config import Settings, SettingsStore, load_token
from app.docker_ops import parse_ps_output
from app.main import create_app
from app.ssh import apply_env, parse_env

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-shelles teszt-szerver"
)


def make_client(tmp_path: Path) -> tuple[TestClient, str]:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    app = create_app(data, port=8765)
    app.state.allowed_hosts = {"testserver", "127.0.0.1:8765"}
    return TestClient(app), load_token(data)


def login(client: TestClient, token: str) -> None:
    response = client.get(f"/login?t={token}", follow_redirects=False)
    assert response.status_code == 303


# ------------------------------------------------------------------ védelem
def test_health_is_open(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    assert client.get("/health").json()["app"] == "szerverpult"


def test_requires_login(tmp_path: Path) -> None:
    client, token = make_client(tmp_path)
    assert client.get("/api/info").status_code == 401
    page = client.get("/")
    assert page.status_code == 401 and "Szerverpult.exe" in page.text
    assert client.get("/login?t=rossz").status_code == 403
    login(client, token)
    assert client.get("/api/info").status_code == 200
    assert "Szerverpult" in client.get("/").text
    assert client.get("/static/vendor/xterm.js").status_code == 200


def test_rejects_foreign_host_and_origin(tmp_path: Path) -> None:
    client, token = make_client(tmp_path)
    login(client, token)
    assert (
        client.get("/api/info", headers={"host": "gonosz.example.com"}).status_code
        == 400
    )
    response = client.put(
        "/api/settings/local",
        json={"vnc_viewer": ""},
        headers={"origin": "https://gonosz.example.com"},
    )
    assert response.status_code == 403
    ok = client.put(
        "/api/settings/local",
        json={"vnc_viewer": ""},
        headers={"origin": "http://testserver"},
    )
    assert ok.status_code == 200


def test_token_is_stable(tmp_path: Path) -> None:
    assert load_token(tmp_path) == load_token(tmp_path)
    assert len(load_token(tmp_path)) >= 32


# --------------------------------------------------------------- beállítások
def test_settings_validation() -> None:
    with pytest.raises(ValueError):
        Settings(ssh_host="rossz host; rm -rf /")
    with pytest.raises(ValueError):
        Settings(repo_dir="relativ/ut")
    with pytest.raises(ValueError):
        Settings(ssh_user="Root Admin")
    assert Settings(ssh_host=" szerver.example.com ").ssh_host == "szerver.example.com"


def test_apply_env_skips_invalid_values() -> None:
    env = parse_env(
        "TAILNET=pelda-net.ts.net\n# komment\nTMDB_HOSTNAME=Rossz_Nev!\nPULT_HOSTNAME='pult2'\n"
    )
    settings = apply_env(Settings(), env)
    assert settings.tailnet == "pelda-net.ts.net"
    assert settings.hostnames.tmdb == "tmdb"
    assert settings.hostnames.pult == "pult2"


def test_parse_ps_output() -> None:
    lines = "\n".join(
        [
            json.dumps(
                {
                    "Names": "b",
                    "State": "running",
                    "Labels": "com.docker.compose.project=p",
                }
            ),
            "zaj",
            json.dumps({"Names": "a", "State": "exited", "Labels": ""}),
        ]
    )
    assert [c["name"] for c in parse_ps_output(lines)] == ["b", "a"]


# ----------------------------------------------------------------------- VNC
def test_vnc_args() -> None:
    viewer = Path("vncviewer.exe")
    assert vnc.build_args(viewer, "203.0.113.10:63100", "abc") == [
        "vncviewer.exe",
        "203.0.113.10::63100",
        "-password",
        "abc",
    ]
    assert vnc.build_args(viewer, "vnc://szerver.example.com", "") == [
        "vncviewer.exe",
        "szerver.example.com",
    ]
    assert vnc.build_args(viewer, "203.0.113.10::5901", "")[1] == "203.0.113.10::5901"
    for bad in ("-evil", "a b:1", "host:99999"):
        with pytest.raises(vnc.VNCError):
            vnc.build_args(viewer, bad, "")


@pytest.mark.skipif(sys.platform == "win32", reason="shell-szkriptes álnéző")
def test_vnc_launch(tmp_path: Path) -> None:
    out = tmp_path / "args.txt"
    viewer = tmp_path / "vncviewer"
    viewer.write_text(f'#!/bin/sh\nprintf "%s|" "$@" > "{out}"\n')
    viewer.chmod(0o755)
    vnc.launch(str(viewer), "203.0.113.10:63100", "titok")
    for _ in range(50):
        if out.exists() and out.read_text():
            break
        time.sleep(0.1)
    assert out.read_text() == "203.0.113.10::63100|-password|titok|"
    with pytest.raises(vnc.VNCError):
        vnc.launch(str(tmp_path / "nincs.exe"), "1.2.3.4:5900", "")


# ------------------------------------------------------- SSH, teszt-szerverrel
def setup_payload(
    server: SimpleNamespace, password: str = PASSWORD
) -> dict[str, object]:
    return {
        "ssh_host": "127.0.0.1",
        "ssh_port": server.port,
        "ssh_user": "root",
        "repo_dir": str(server.repo),
        "password": password,
    }


@POSIX_ONLY
def test_setup_wrong_password(tmp_path: Path, ssh_server: SimpleNamespace) -> None:
    client, token = make_client(tmp_path)
    with client:
        login(client, token)
        response = client.post("/api/setup", json=setup_payload(ssh_server, "rossz"))
        assert response.status_code == 502
        assert "elutasította" in response.json()["detail"]
        assert client.get("/api/info").json()["configured"] is False


@POSIX_ONLY
def test_full_flow(tmp_path: Path, ssh_server: SimpleNamespace) -> None:
    client, token = make_client(tmp_path)
    with client:
        login(client, token)
        response = client.post("/api/setup", json=setup_payload(ssh_server))
        assert response.status_code == 200, response.text
        assert response.json()["tailnet"] == "pelda-net.ts.net"

        # A kulcs a szerverre került, a jelszó sehol sincs elmentve.
        authorized = (ssh_server.home / ".ssh" / "authorized_keys").read_text()
        assert "szerverpult" in authorized
        data_dir = tmp_path / "data"
        for file in data_dir.iterdir():
            if file.is_file():
                assert PASSWORD not in file.read_text(errors="ignore")

        settings = client.get("/api/settings").json()
        assert settings["configured"] is True
        assert settings["host_key_fingerprint"].startswith("SHA256:")
        assert settings["hostnames"]["tmdb"] == "tmdb"  # a hibás érték kimaradt

        services = client.get("/api/services").json()["services"]
        assert any(
            s["url"].startswith("https://stremhu-tb.pelda-net.ts.net") for s in services
        )

        check = client.get("/api/ssh/check").json()
        assert check["ok"] is True

        containers = client.get("/api/containers").json()
        assert [c["name"] for c in containers] == ["stremhu-torbox-stremhu-1", "magany"]

        logs = client.get("/api/containers/magany/logs").json()["logs"]
        assert "naplo-sor-2" in logs
        assert client.get("/api/containers/nincs-ilyen/logs").status_code == 502
        assert (
            client.post("/api/containers/magany/restart").json()["output"].strip()
            == "magany"
        )

        ps = client.post("/api/actions/ps").text
        assert "compose ps rendben" in ps and "✅ Kész." in ps
        failed = client.post("/api/actions/torbox-status").text
        assert "nincs ilyen szolgaltatas" in failed and "❌" in failed
        assert client.post("/api/actions/rm-rf").status_code == 404

        with client.websocket_connect("/ws/terminal?cols=100&rows=30") as ws:
            assert "teszt-terminal 100x30" in ws.receive_text()
            ws.send_text(json.dumps({"t": "r", "c": 90, "r": 20}))
            assert "MERET:90x20" in ws.receive_text()
            ws.send_text(json.dumps({"t": "i", "d": "szia"}))
            assert "ECHO:szia" in ws.receive_text()


@POSIX_ONLY
def test_changed_host_key_is_rejected(
    tmp_path: Path, ssh_server: SimpleNamespace
) -> None:
    client, token = make_client(tmp_path)
    with client:
        login(client, token)
        assert (
            client.post("/api/setup", json=setup_payload(ssh_server)).status_code == 200
        )
        store = SettingsStore(tmp_path / "data")
        settings = store.load()
        other = asyncssh.generate_private_key("ssh-ed25519")
        settings.host_key = other.export_public_key("openssh").decode().strip()
        store.save(settings)
        client.app.state.ssh.reset()  # type: ignore[attr-defined]
        check = client.get("/api/ssh/check").json()
        assert check["ok"] is False
        assert "gépkulcs" in check["error"]


def test_websocket_requires_login(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/terminal") as ws,
    ):
        ws.receive_text()


def test_run_py_probe_free_port() -> None:
    import socket

    import run

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert run.probe(port) == "busy"
    assert run.probe(port) == "free"


def test_cli_has_no_syntax_errors() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(root / "run.py")], check=True
    )
