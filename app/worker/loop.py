from __future__ import annotations

import time
from datetime import timedelta

from ddtrace import tracer

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import init_metrics
from app.db.repositories import metrics as metrics_repo
from app.domain.ponds import pond_ids
from app.worker.pond_check import check_pond, load_do_windows

logger = get_logger("worker")


def run_worker() -> None:
    init_metrics()
    logger.info("worker starting")
    ponds = pond_ids()
    # Lan dau: backfill toan bo khoang TTL; sau do chi tinh lai gio truoc + gio hien tai.
    rollup_since = utcnow() - timedelta(seconds=settings.TS_TTL_SECONDS)
    last_rollup = 0.0
    while True:
        t0 = time.time()
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

        for pid in ponds:
            try:
                with tracer.trace("worker.check_pond", resource=pid):
                    check_pond(pid, do_rows.get(pid))
            except Exception as e:
                logger.error("check_pond failed", extra={"pond": pid, "err": str(e)})

        time.sleep(max(1.0, settings.WORKER_INTERVAL_SEC - (time.time() - t0)))
