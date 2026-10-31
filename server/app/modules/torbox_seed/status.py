"""Állapotjelentés a parancssorból.

Használat a szerveren:
    docker compose exec stremhu python -m app.modules.torbox_seed.status
"""

from app.config import config
from app.modules.torbox_seed.report import format_status
from app.modules.torbox_seed.storage import STATUS_FILE_NAME, load_status, utc_now


def main() -> None:
    print(format_status(load_status(config.system_dir / STATUS_FILE_NAME), utc_now()))


if __name__ == "__main__":
    main()
