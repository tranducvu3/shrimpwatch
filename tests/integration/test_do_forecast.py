"""The worker turns the $derivative DO drop rate into minutes left before DO is critical."""

from datetime import timedelta

import pytest

from app.core.clock import utcnow
from app.core.config import settings
from app.db.mongo import incidents_collection, metrics_collection
from app.db.repositories.metrics import pack_ts_doc


def _insert(pond: str, metric: str, value: float, seconds_ago: int, device_type: str = "water_node") -> None:
    metrics_collection().insert_one(
        pack_ts_doc(
            {
                "timestamp": utcnow() - timedelta(seconds=seconds_ago),
                "farm_id": settings.FARM_ID,
                "pond_id": pond,
                "device_id": f"dev-{pond}",
                "device_type": device_type,
                "metric": metric,
                "source": "sensor",
                "unit": "",
                "value": value,
            }
        )
    )


def test_worker_forecasts_minutes_to_critical_do(monkeypatch):
    from app.worker import pond_check

    sent = []
    monkeypatch.setattr(pond_check, "gauge", lambda name, value, tags=None: sent.append((name, value, tags)))
    pond = "pond-71"
    # DO falls 0.1 mg/L per minute over the last 4.5 minutes, aerator running
    for i in range(10):
        seconds_ago = 30 * (9 - i)
        _insert(pond, "do", 6.0 - 0.05 * i, seconds_ago)
        _insert(pond, "pump_power", 1500.0, seconds_ago, device_type="pump")
        _insert(pond, "pump_status", 1.0, seconds_ago, device_type="pump")

    pond_check.check_pond(pond)

    [(name, minutes, tags)] = sent
    assert name == "do.minutes_to_critical" and tags == {"pond": pond}
    assert minutes == pytest.approx((5.55 - 3.0) / 0.1, abs=0.5)
    inc = incidents_collection().find_one({"pond_id": pond, "state": "open"})
    assert inc["root_cause"] == "do_out_of_range"
    assert inc["evidence"]["minutes_to_do_critical"] == pytest.approx(minutes, abs=0.1)
    assert "phút nữa DO < 3 mg/L" in inc["description"]
