from __future__ import annotations

import time
from datetime import timedelta

from ddtrace import tracer

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import gauge, init_metrics
from app.db.repositories import metrics as metrics_repo
from app.domain.ponds import pond_ids
from app.services import incidents as incident_service
from app.worker.pond_check import check_pond, load_do_windows, load_sensor_health

logger = get_logger("worker")


def run_worker() -> None:
    init_metrics()
    logger.info("worker starting")
    ponds = pond_ids()
    # First run: backfill the whole TTL range; after that only recompute the previous + current hour.
    rollup_since = utcnow() - timedelta(seconds=settings.TS_TTL_SECONDS)
    last_rollup = float("-inf")  # roll up on the first cycle
    while True:
        t0 = time.monotonic()  # not wall clock: an NTP jump must not look like a slow cycle
        if t0 - last_rollup >= settings.ROLLUP_INTERVAL_SEC:
            try:
                with tracer.trace("worker.rollup_hourly"):
                    metrics_repo.run_hourly_rollup(rollup_since)
                last_rollup = t0
                rollup_since = utcnow() - timedelta(hours=1)
            except Exception as e:
                logger.error("hourly rollup failed", extra={"err": str(e)})

        try:
            with tracer.trace("worker.do_window"):
                do_rows = load_do_windows(ponds)
        except Exception as e:
            logger.error("DO window failed", extra={"err": str(e)})
            do_rows = {}

        try:
            with tracer.trace("worker.sensor_coverage"):
                sensors = load_sensor_health(ponds)
        except Exception as e:
            logger.error("sensor coverage failed", extra={"err": str(e)})
            sensors = {}

        for pid in ponds:
            try:
                with tracer.trace("worker.check_pond", resource=pid):
                    check_pond(pid, do_rows.get(pid), sensors.get(pid))
            except Exception as e:
                logger.error("check_pond failed", extra={"pond": pid, "err": str(e)})

        try:
            incident_service.report_active(ponds)
        except Exception as e:
            logger.error("report active incidents failed", extra={"err": str(e)})

        elapsed = time.monotonic() - t0
        # Heartbeat and load: a monitor alerts when the cycle gets slow or the metric stops (worker down)
        gauge("worker.cycle_seconds", round(elapsed, 2))
        time.sleep(max(1.0, settings.WORKER_INTERVAL_SEC - elapsed))
