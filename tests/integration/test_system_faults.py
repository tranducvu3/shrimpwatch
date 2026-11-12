"""Demo system faults from the control panel: switched on and off, recorded, and the API one fails requests."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.db.mongo import events_collection
from app.services import system_faults as fault_service


@pytest.fixture(autouse=True)
def _all_off(client: TestClient):
    client.delete("/system-faults")
    yield
    client.delete("/system-faults")


def _status(client: TestClient) -> dict:
    return {f["type"]: f for f in client.get("/system-faults").json()["faults"]}


def test_start_and_clear_are_listed_and_recorded(client: TestClient):
    assert set(_status(client)) == {"slow_worker", "api_errors", "feed_stopped"}
    assert client.post("/system-faults/slow_worker").status_code == 200

    worker = _status(client)["slow_worker"]
    assert worker["active"] and 590 <= worker["remaining_sec"] <= 600
    assert fault_service.active() == {fault_service.FaultType.SLOW_WORKER}
    assert events_collection().find_one({"event": "system_fault_start", "details.fault": "slow_worker"})

    assert client.delete("/system-faults/slow_worker").json() == {"cleared": ["slow_worker"]}
    assert not _status(client)["slow_worker"]["active"]
    assert events_collection().find_one({"event": "system_fault_clear", "details.fault": "slow_worker"})
    assert client.post("/system-faults/volcano").status_code == 422


def test_api_errors_fail_requests_but_never_the_panel_controls(client: TestClient, monkeypatch):
    monkeypatch.setattr(api_main, "API_ERROR_SHARE", 1.0)  # every eligible request, for a deterministic test
    assert client.get("/ponds").status_code == 200
    client.post("/system-faults/api_errors")

    assert client.get("/ponds").status_code == 503
    assert client.get("/healthz").status_code == 200
    assert client.get("/control").status_code == 200
    assert client.get("/system-faults").status_code == 200

    client.delete("/system-faults/api_errors")
    assert client.get("/ponds").status_code == 200
