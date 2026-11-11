"""Az nCore ajánló félórás lekérése (a staff-ajánlások gyűjtéséhez)."""

from app.common.logger import logger
from app.modules.torbox_play.stremio import get_torbox_play_service


async def run_ncore_recommended_refresh() -> None:
    try:
        await get_torbox_play_service().refresh_recommended_now()
    except Exception as error:  # az ütemező ne álljon le egy hiba miatt
        logger.warning("nCore-ajánló ütemezett lekérése sikertelen: %s", error)
