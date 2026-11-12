from datetime import datetime, timedelta

from app.domain.sensor_health import SensorHealth, sensor_health

NOW = datetime(2026, 11, 11, 12, 0, 0)
PONDS = ["pond-01", "pond-02", "pond-03"]


def test_probe_health_per_pond():
    coverage = {
        "pond-01": {"completeness": 1.0, "last_seen": NOW - timedelta(seconds=40), "last_value": 6.2},
        "pond-02": {"completeness": 0.4, "last_seen": NOW - timedelta(seconds=200)},
    }
    health = sensor_health(coverage, PONDS, NOW, window_sec=300, stale_sec=120)
    assert health["pond-01"] == SensorHealth(1.0, 40.0, 6.2)
    assert health["pond-02"] == SensorHealth(0.4, 200.0)
    # Nothing in the whole window: silent at least that long
    assert health["pond-03"] == SensorHealth(0.0, 300.0)


def test_whole_feed_down_is_not_a_probe_problem():
    assert sensor_health({}, PONDS, NOW, window_sec=300, stale_sec=120) == {}


def test_every_pond_losing_readings_at_once_is_the_feed_not_the_probes():
    # A failing ingest API drops whole batches: every pond's share falls together, none is silent
    lossy = {p: {"completeness": 0.4, "last_seen": NOW - timedelta(seconds=40)} for p in PONDS}
    assert sensor_health(lossy, PONDS, NOW, window_sec=300, stale_sec=120, min_completeness=0.5) == {}
    lossy["pond-01"]["completeness"] = 1.0  # one pond still healthy: the others are probe problems
    assert sensor_health(lossy, PONDS, NOW, window_sec=300, stale_sec=120, min_completeness=0.5)["pond-02"]


def test_every_pond_stale_at_once_is_the_feed_not_the_probes():
    # A backfill after a restart (or a pipeline outage) leaves every pond's latest reading minutes old at once
    stale = {p: {"completeness": 0.2, "last_seen": NOW - timedelta(seconds=270)} for p in PONDS}
    assert sensor_health(stale, PONDS, NOW, window_sec=300, stale_sec=120) == {}
