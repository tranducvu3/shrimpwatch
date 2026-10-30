from __future__ import annotations

import asyncio
import math
import random
import time
from datetime import datetime, timedelta

import httpx

from app.config import settings
from app.logging_setup import get_logger
from app.models import (
    DeviceType,
    MetricRecord,
    MetricType,
    Source,
)

logger = get_logger("simulator")


def _hour_fraction(dt: datetime) -> float:
    return (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0


def diurnal_amplitude(hour_frac: float, phase_offset_hours: float = 0.0) -> float:
    phase = (hour_frac * 24 - phase_offset_hours) / 24 * 2 * math.pi
    return math.sin(phase - math.pi / 2) * 0.5 + 0.5


class PondSimulator:
    def __init__(self, pond_id: str, seed: int = 42):
        self.pond_id = pond_id
        self.node_id = f"node-{pond_id}"
        self.pump_id = f"pump-{pond_id}"
        self._rng = random.Random(seed + sum(ord(c) for c in pond_id))
        self._temp_base = 28.5
        self._do_base = 5.8
        self._ph_base = 8.15
        self._do_last = self._do_base
        self._do_last_ts = datetime.utcnow()
        self._orp_last = 280.0
        self._salinity_last = 15.0 + self._rng.uniform(-1.0, 1.0)

    def step(self, now: datetime, pump_failed: bool) -> list[MetricRecord]:
        hf = _hour_fraction(now)
        temp_amp = diurnal_amplitude(hf, phase_offset_hours=0)
        temp = self._temp_base + temp_amp * 3.0 + self._rng.gauss(0, 0.15)

        ph_amp = diurnal_amplitude(hf, phase_offset_hours=12)
        ph = self._ph_base + ph_amp * 0.35 + self._rng.gauss(0, 0.05)

        do_amp = diurnal_amplitude(hf, phase_offset_hours=18)
        do_baseline = self._do_base + do_amp * 0.8

        delta_min = max(
            0.1, (now - self._do_last_ts).total_seconds() / 60.0
        )
        if pump_failed:
            # Khong co quat: tom + vi sinh tieu thu oxy, DO giam deu, khong keo ve baseline
            drift = 0.0
            aeration = -0.1 + self._rng.gauss(0, 0.005)
        else:
            drift = (do_baseline - self._do_last) * min(1.0, delta_min / 10.0)
            aeration = +0.04 + self._rng.gauss(0, 0.005)
        do = max(
            0.5, min(10.0, self._do_last + drift + aeration * delta_min + self._rng.gauss(0, 0.03))
        )
        self._do_last = do
        self._do_last_ts = now

        # ORP giam theo DO: day ao thieu oxy -> phan huy ky khi, nguy co H2S
        orp_target = 60.0 * (do - 3.0) + 120.0
        self._orp_last += (orp_target - self._orp_last) * min(1.0, delta_min / 5.0)
        orp = self._orp_last + self._rng.gauss(0, 4)

        self._salinity_last += self._rng.gauss(0, 0.02)
        salinity = self._salinity_last

        pump_power = (
            0.0 if pump_failed else (1500.0 + self._rng.gauss(0, 50))
        )
        pump_status = 0.0 if pump_failed else 1.0

        return [
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.node_id,
                device_type=DeviceType.WATER_NODE,
                metric=MetricType.TEMPERATURE,
                value=round(temp, 3),
                source=Source.SENSOR,
                unit="C",
            ),
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.node_id,
                device_type=DeviceType.WATER_NODE,
                metric=MetricType.DO,
                value=round(do, 3),
                source=Source.SENSOR,
                unit="mg/L",
            ),
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.node_id,
                device_type=DeviceType.WATER_NODE,
                metric=MetricType.PH,
                value=round(ph, 3),
                source=Source.SENSOR,
                unit="pH",
            ),
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.node_id,
                device_type=DeviceType.WATER_NODE,
                metric=MetricType.SALINITY,
                value=round(salinity, 2),
                source=Source.SENSOR,
                unit="ppt",
            ),
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.node_id,
                device_type=DeviceType.WATER_NODE,
                metric=MetricType.ORP,
                value=round(orp, 1),
                source=Source.SENSOR,
                unit="mV",
            ),
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.pump_id,
                device_type=DeviceType.PUMP,
                metric=MetricType.PUMP_POWER,
                value=round(pump_power, 1),
                source=Source.SENSOR,
                unit="W",
            ),
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=self.pump_id,
                device_type=DeviceType.PUMP,
                metric=MetricType.PUMP_STATUS,
                value=pump_status,
                source=Source.SENSOR,
                unit="bool",
            ),
        ]


def _fetch_pump_state(api_url: str) -> dict[str, bool]:
    try:
        r = httpx.get(f"{api_url}/ponds", timeout=5.0)
        r.raise_for_status()
        data = r.json()
        return {
            p["_id"]: bool(p.get("pump", {}).get("failed", False))
            for p in data
        }
    except Exception as e:
        logger.warning("fetch ponds failed", extra={"err": str(e)})
        return {}


def post_manual_readings(api_url: str, ponds: list[str], now: datetime):
    """Mo phong ky thuat vien do test-kit: do kiem, TAN, nitrite."""
    for pid in ponds:
        readings = {
            "alkalinity": round(150 + random.gauss(0, 8), 1),
            "tan": round(max(0.05, 0.6 + random.gauss(0, 0.2)), 2),
            "nitrite": round(max(0.02, 0.3 + random.gauss(0, 0.1)), 2),
        }
        for metric, value in readings.items():
            try:
                httpx.post(
                    f"{api_url}/ingest/manual/{metric}",
                    json={
                        "timestamp": now.isoformat(),
                        "farm_id": settings.FARM_ID,
                        "pond_id": pid,
                        "value": value,
                        "tester_id": "tech-001",
                    },
                    timeout=10.0,
                )
                logger.info("manual ingest", extra={"pond": pid, "metric": metric, "value": value})
            except Exception as e:
                logger.warning("manual ingest failed", extra={"metric": metric, "err": str(e)})


async def run_simulation():
    from app.db import get_client, ponds_collection
    from app.metrics import init_metrics

    init_metrics()
    try:
        get_client().admin.command("ping")
    except Exception:
        logger.warning("mongo not ready yet; skipping init")

    ponds = [f"pond-{i:02d}" for i in range(1, settings.NUM_PONDS + 1)]
    sims = {pid: PondSimulator(pid, seed=i) for i, pid in enumerate(ponds)}

    logger.info("simulator starting", extra={"ponds": ponds, "interval_sec": settings.SIMULATOR_INTERVAL_SEC})
    api_url = settings.API_URL.rstrip("/")

    backfill_hours = 6
    now = datetime.utcnow()
    start = now - timedelta(hours=backfill_hours)
    records: list[dict] = []
    pump_states: dict[str, bool] = {p: False for p in ponds}
    t = start
    while t <= now:
        for pid in ponds:
            recs = sims[pid].step(t, pump_states.get(pid, False))
            records.extend([r.model_dump(mode="json") for r in recs])
        t += timedelta(seconds=settings.SIMULATOR_INTERVAL_SEC)
    for i in range(0, len(records), 1000):
        chunk = records[i : i + 1000]
        try:
            resp = httpx.post(
                f"{api_url}/ingest/batch",
                json={"records": chunk},
                timeout=60.0,
            )
            logger.info("backfill ingest", extra={"status": resp.status_code, "count": len(chunk)})
        except Exception as e:
            logger.warning("backfill failed", extra={"err": str(e)})
    post_manual_readings(api_url, ponds, now)

    while True:
        t0 = time.time()
        now = datetime.utcnow()
        pump_states = _fetch_pump_state(api_url)
        batch: list[dict] = []
        for pid in ponds:
            recs = sims[pid].step(now, pump_states.get(pid, False))
            batch.extend([r.model_dump(mode="json") for r in recs])
        try:
            resp = httpx.post(
                f"{api_url}/ingest/batch",
                json={"records": batch},
                timeout=15.0,
            )
            if resp.status_code >= 400:
                logger.error("ingest failed", extra={"status": resp.status_code, "body": resp.text})
        except Exception as e:
            logger.warning("ingest POST failed", extra={"err": str(e)})

        if (now.hour in (0, 6, 12, 18)) and now.minute < 1 and now.second < settings.SIMULATOR_INTERVAL_SEC:
            post_manual_readings(api_url, ponds, now)

        elapsed = time.time() - t0
        await asyncio.sleep(max(0.05, settings.SIMULATOR_INTERVAL_SEC - elapsed))


def main():
    asyncio.run(run_simulation())


if __name__ == "__main__":
    main()
