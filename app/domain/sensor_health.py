"""Is each pond's water-quality probe delivering its readings? Pure: no DB, no HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SensorHealth:
    # Share of the expected readings that arrived over the window, 0..1
    completeness: float
    # Seconds since the latest reading; the window length when none arrived in it (so at least that long)
    silent_sec: float
    # Last DO the probe sent within the window (carried forward by $fill), None when none arrived
    last_do: float | None = None


def sensor_health(
    coverage: dict[str, dict],
    pond_ids: list[str],
    now: datetime,
    window_sec: int,
    stale_sec: float,
    min_completeness: float = 0.0,
) -> dict[str, SensorHealth]:
    """Per pond, from the coverage rows of `sensor_coverage_pipeline` (a pond with no row sent nothing).

    Empty when no pond is healthy (a reading newer than `stale_sec` and at least `min_completeness` of the expected
    ones): then the whole feed is down, lossy or catching up (simulator, network, a failing ingest API, a backfill
    after a restart), which the "sensor data stopped" and "API error rate" monitors report once, rather than as one
    probe incident per pond. A probe problem is one pond going quiet while the others still report.
    """
    if not coverage:
        return {}
    result = {}
    for pond_id in pond_ids:
        row = coverage.get(pond_id)
        if row is None:
            result[pond_id] = SensorHealth(0.0, float(window_sec))
        else:
            silent = max(0.0, (now - row["last_seen"]).total_seconds())
            result[pond_id] = SensorHealth(round(row["completeness"], 3), round(silent, 1), row.get("last_value"))
    if not any(h.silent_sec <= stale_sec and h.completeness >= min_completeness for h in result.values()):
        return {}
    return result
