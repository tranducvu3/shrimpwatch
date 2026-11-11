"""A pond whose probe stops sending is found by the $densify coverage pipeline and gets a sensor_offline incident."""

from datetime import timedelta

from app.core.clock import utcnow
from app.db.mongo import incidents_collection
from app.db.repositories.metrics import pack_ts_doc, sensor_coverage
from app.db.mongo import metrics_collection
from app.domain.sensor_health import sensor_health
from app.services import incidents as incident_service
from app.worker.pond_check import check_pond

ALIVE, FLAKY, DEAD = "pond-91", "pond-92", "pond-93"


def _do(pond_id: str, seconds_ago: int, value: float = 6.5) -> dict:
    return pack_ts_doc(
        {
            "timestamp": utcnow() - timedelta(seconds=seconds_ago),
            "farm_id": "farm-001",
            "pond_id": pond_id,
            "device_id": f"node-{pond_id}",
            "device_type": "water_node",
            "metric": "do",
            "source": "sensor",
            "unit": "mg/L",
            "value": value,
        }
    )


def _seed():
    # Alive: a reading every 30 s for 6 minutes. Flaky: every 90 s. Dead: stopped 4 minutes ago.
    docs = [_do(ALIVE, s) for s in range(15, 375, 30)]
    docs += [_do(FLAKY, s) for s in range(15, 375, 90)]
    docs += [_do(DEAD, s, value=6.1) for s in range(255, 375, 30)]
    metrics_collection().insert_many(docs)


def test_coverage_counts_missing_slots_and_carries_the_last_value():
    _seed()
    rows = sensor_coverage([ALIVE, FLAKY, DEAD], window_sec=300, step_sec=30)
    assert rows[ALIVE]["completeness"] >= 0.9
    assert rows[FLAKY]["completeness"] <= 0.4
    dead = rows[DEAD]
    # $densify filled the slots after the probe stopped; $fill carried its last DO into them. 2 or 3 of its readings
    # fall in the window, depending on where `now` is within the current 30 s slot.
    assert dead["slots"] == 10 and dead["received"] <= 3 and dead["last_value"] == 6.1

    health = sensor_health(rows, [ALIVE, FLAKY, DEAD], utcnow(), 300)
    assert health[DEAD].silent_sec > 200 and health[ALIVE].silent_sec < 90


def test_dead_and_flaky_probes_open_sensor_offline_incidents():
    _seed()
    rows = sensor_coverage([ALIVE, FLAKY, DEAD], window_sec=300, step_sec=30)
    health = sensor_health(rows, [ALIVE, FLAKY, DEAD], utcnow(), 300)
    for pond in (ALIVE, FLAKY, DEAD):
        incident_service.auto_recover(pond)
        check_pond(pond, {"do_drop_rate_mg_per_l_per_min": 0.0, "min_do": 6.0}, health[pond])

    def active_cause(pond):
        inc = incidents_collection().find_one({"pond_id": pond, "state": {"$ne": "recovered"}})
        return inc and inc["root_cause"]

    assert active_cause(DEAD) == "sensor_offline"
    assert active_cause(FLAKY) == "sensor_offline"
    assert active_cause(ALIVE) is None
