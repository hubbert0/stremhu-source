"""Értesítések (ntfy) a seedkezelés problémáiról – ismétlésszűréssel.

Egy problémáról csak akkor megy értesítés, ha egy ideig fennáll (egy-egy
pillanatnyi állapot ne riasszon), és utána legfeljebb `repeat` időközönként.
"""

import datetime
from dataclasses import dataclass, replace

import httpx

from app.common.logger import logger
from app.modules.torbox_seed.logic import (
    ALERT_EMERGENCY,
    ALERT_HNR_UNAVAILABLE,
    ALERT_NOT_SEEDING,
    ALERT_SLOTS,
    ALERT_STALLED,
    REASON_EMERGENCY,
    Alert,
    Plan,
)

# Mennyi ideig kell fennállnia a problémának, mielőtt értesítés megy róla.
ALERT_DELAYS = {
    ALERT_EMERGENCY: datetime.timedelta(0),
    ALERT_NOT_SEEDING: datetime.timedelta(minutes=15),
    ALERT_STALLED: datetime.timedelta(minutes=30),
    ALERT_SLOTS: datetime.timedelta(minutes=15),
    ALERT_HNR_UNAVAILABLE: datetime.timedelta(hours=1),
}
DEFAULT_DELAY = datetime.timedelta(minutes=15)


@dataclass(frozen=True)
class AlertRecord:
    first_seen: datetime.datetime
    last_sent: datetime.datetime | None = None


def notifiable_alerts(plan: Plan, stopped_now: set[int]) -> list[Alert]:
    """A terv figyelmeztetései, kivéve a most tervezett vészleállításokat, amelyek
    nem történtek meg (próbaüzem vagy hiba) – azokról nem szólunk, mintha
    leállítottuk volna őket."""
    planned = {
        torrent.id for torrent, reason in plan.stop if reason == REASON_EMERGENCY
    }
    return [
        alert
        for alert in plan.alerts
        if not (
            alert.kind == ALERT_EMERGENCY
            and alert.torrent_id in planned
            and alert.torrent_id not in stopped_now
        )
    ]


def due_alerts(
    alerts: list[Alert],
    records: dict[str, AlertRecord],
    now: datetime.datetime,
    repeat: datetime.timedelta,
) -> tuple[list[Alert], dict[str, AlertRecord]]:
    """Visszaadja, mely figyelmeztetésekről kell most értesítést küldeni, és az
    új nyilvántartást (a megszűnt problémák kikerülnek belőle)."""
    due: list[Alert] = []
    updated: dict[str, AlertRecord] = {}

    for alert in alerts:
        record = records.get(alert.key) or AlertRecord(first_seen=now)
        delay = ALERT_DELAYS.get(alert.kind, DEFAULT_DELAY)
        if now - record.first_seen >= delay and (
            record.last_sent is None or now - record.last_sent >= repeat
        ):
            due.append(alert)
        updated[alert.key] = record

    return due, updated


def mark_sent(
    records: dict[str, AlertRecord], alerts: list[Alert], now: datetime.datetime
) -> dict[str, AlertRecord]:
    result = dict(records)
    for alert in alerts:
        record = result.get(alert.key)
        if record is not None:
            result[alert.key] = replace(record, last_sent=now)
    return result


def notification_text(alerts: list[Alert]) -> tuple[str, str]:
    title = (
        "TorBox-seed: figyelmeztetés"
        if len(alerts) == 1
        else f"TorBox-seed: {len(alerts)} figyelmeztetés"
    )
    message = "\n".join(f"• {alert.message}" for alert in alerts)
    message += "\n\nRészletek: Szerverpult → Műveletek → TorBox-helykezelés jelentése"
    return title, message


async def send_ntfy(
    url: str,
    topic: str,
    alerts: list[Alert],
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    title, message = notification_text(alerts)
    payload: dict[str, object] = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": ["warning"],
        "priority": 4,
    }
    links = [alert.link for alert in alerts if alert.link]
    if len(links) == 1:
        # Az értesítésre koppintva megnyílik a torrent adatlapja.
        payload["click"] = links[0]
    try:
        async with httpx.AsyncClient(timeout=20, transport=transport) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return True
    except Exception:
        logger.warning("⚠️ TorBox: az értesítést nem sikerült elküldeni (ntfy).")
        return False
