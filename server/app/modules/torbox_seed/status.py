"""Állapotjelentés a parancssorból.

Használat a szerveren:
    docker compose exec stremhu python -m app.modules.torbox_seed.status
    docker compose exec stremhu python -m app.modules.torbox_seed.status --all
"""

import sys

from app.config import config
from app.modules.torbox_seed.report import format_status
from app.modules.torbox_seed.storage import STATUS_FILE_NAME, load_status, utc_now


def main() -> None:
    show_all = "--all" in sys.argv[1:]
    status = load_status(config.system_dir / STATUS_FILE_NAME)
    print(format_status(status, utc_now(), show_all=show_all))


if __name__ == "__main__":
    main()
