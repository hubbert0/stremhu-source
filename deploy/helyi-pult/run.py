"""Szerverpult indító: elindítja a helyi szervert és megnyitja a böngészőt.

Ha már fut, csak megnyitja újra a böngészőben (bejelentkezve).
"""

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from app.config import DEFAULT_PORT, data_dir, load_token
from app.main import create_app

PORT_RANGE = range(DEFAULT_PORT, DEFAULT_PORT + 10)


def probe(port: int) -> str:
    """'ours' ha ezen a porton már a Szerverpult fut, 'free' ha szabad, 'busy' ha más."""
    # Rendszer-proxy nélkül: a helyi címet sosem szabad proxyn át kérdezni.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
            return "ours" if data.get("app") == "szerverpult" else "busy"
    except OSError:
        pass
    except ValueError:
        return "busy"
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return "busy"
    return "free"


def open_browser(port: int, token: str) -> None:
    if os.environ.get("SZERVERPULT_NO_BROWSER"):
        return
    webbrowser.open(f"http://127.0.0.1:{port}/login?t={token}")


def wait_and_open(port: int, token: str) -> None:
    for _ in range(100):
        if probe(port) == "ours":
            open_browser(port, token)
            return
        time.sleep(0.2)


def set_title() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW("Szerverpult")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def main() -> None:
    # Ékezetek miatt: ha a kimenet nem tudja megjeleníteni, ne álljon le tőle.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")
    set_title()
    directory = data_dir()
    token = load_token(directory)

    port = None
    for candidate in PORT_RANGE:
        state = probe(candidate)
        if state == "ours":
            print("A Szerverpult már fut – megnyitom a böngészőben.")
            open_browser(candidate, token)
            time.sleep(1)
            return
        if state == "free":
            port = candidate
            break
    if port is None:
        print(
            "Nem találtam szabad portot (8765–8774). Zárj be más programokat, és próbáld újra."
        )
        input("Enter a kilépéshez…")
        return

    app = create_app(directory, port)
    print("=" * 60)
    print(" Szerverpult fut")
    print(f"   Cím:    http://127.0.0.1:{port}")
    print(f"   Adatok: {directory}")
    print()
    print(" Ezt az ablakot hagyd nyitva; bezárásával a pult leáll.")
    print(" Ha bezártad a böngészőlapot, indítsd újra a Szerverpult.exe-t.")
    print("=" * 60)

    threading.Thread(target=wait_and_open, args=(port, token), daemon=True).start()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        loop="asyncio",
        http="h11",
        ws="websockets",
        lifespan="off",
    )
    try:
        uvicorn.Server(config).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
