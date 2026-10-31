from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.domain.models import MetricRecord
from app.domain.ponds import pond_ids
from app.simulator.api_client import ShrimpWatchClient
from app.simulator.pond_model import PondSimulator

logger = get_logger("simulator")

BACKFILL_HOURS = 6
MAX_BATCH = 1000
MANUAL_READING_HOURS = (0, 6, 12, 18)


def _dump(records: list[MetricRecord]) -> list[dict]:
    return [r.model_dump(mode="json") for r in records]


def post_manual_readings(client: ShrimpWatchClient, ponds: list[str], now: datetime) -> None:
    """Mo phong ky thuat vien do test-kit: do kiem, TAN, nitrite."""
    for pid in ponds:
        readings = {
            "alkalinity": round(150 + random.gauss(0, 8), 1),
            "tan": round(max(0.05, 0.6 + random.gauss(0, 0.2)), 2),
            "nitrite": round(max(0.02, 0.3 + random.gauss(0, 0.1)), 2),
        }
        for metric, value in readings.items():
            try:
                client.ingest_manual(metric, pid, value, now)
                logger.info("manual ingest", extra={"pond": pid, "metric": metric, "value": value})
            except Exception as e:
                logger.warning("manual ingest failed", extra={"metric": metric, "err": str(e)})


def backfill(client: ShrimpWatchClient, sims: dict[str, PondSimulator], now: datetime) -> None:
    """Nap 6h lich su khi khoi dong de dashboard va rollup co du lieu ngay."""
    records: list[dict] = []
    t = now - timedelta(hours=BACKFILL_HOURS)
    while t <= now:
        for pid, sim in sims.items():
            records.extend(_dump(sim.step(t, pump_failed=False)))
        t += timedelta(seconds=settings.SIMULATOR_INTERVAL_SEC)
    for i in range(0, len(records), MAX_BATCH):
        chunk = records[i : i + MAX_BATCH]
        try:
            resp = client.ingest_batch(chunk, timeout=60.0)
            logger.info("backfill ingest", extra={"status": resp.status_code, "count": len(chunk)})
        except Exception as e:
            logger.warning("backfill failed", extra={"err": str(e)})


def tick(client: ShrimpWatchClient, sims: dict[str, PondSimulator], now: datetime) -> None:
    pump_states = client.pump_states()
    batch: list[dict] = []
    for pid, sim in sims.items():
        batch.extend(_dump(sim.step(now, pump_states.get(pid, False))))
    try:
        resp = client.ingest_batch(batch)
        if resp.status_code >= 400:
            logger.error("ingest failed", extra={"status": resp.status_code, "body": resp.text})
    except Exception as e:
        logger.warning("ingest POST failed", extra={"err": str(e)})


def _is_manual_reading_time(now: datetime) -> bool:
    return now.hour in MANUAL_READING_HOURS and now.minute < 1 and now.second < settings.SIMULATOR_INTERVAL_SEC


async def run_simulation() -> None:
    ponds = pond_ids()
    sims = {pid: PondSimulator(pid, seed=i) for i, pid in enumerate(ponds)}
    client = ShrimpWatchClient()
    logger.info("simulator starting", extra={"ponds": ponds, "interval_sec": settings.SIMULATOR_INTERVAL_SEC})

    now = utcnow()
    backfill(client, sims, now)
    post_manual_readings(client, ponds, now)

    while True:
        t0 = time.time()
        now = utcnow()
        tick(client, sims, now)
        if _is_manual_reading_time(now):
            post_manual_readings(client, ponds, now)
        await asyncio.sleep(max(0.05, settings.SIMULATOR_INTERVAL_SEC - (time.time() - t0)))
