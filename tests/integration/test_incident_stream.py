"""Change Streams on a real MongoDB: needs a replica set (Compose and CI run a single-node one)."""

import os

import pytest

from app.db.repositories import events as events_repo
from app.services import incident_stream


@pytest.fixture
def stream():
    try:
        s = incident_stream.open_stream()
    except incident_stream.StreamUnavailable as e:
        if os.environ.get("CI") == "true":
            pytest.fail(f"CI must run MongoDB as a replica set: {e}")
        pytest.skip(f"MongoDB is not a replica set: {e}")
    yield s
    s.close()


def test_a_new_incident_event_reaches_the_stream(stream):
    events_repo.record("incident_open", "pond-61", incident_id="inc-stream-1", details={"severity": "warning"})
    change = None
    for _ in range(5):  # each try_next waits up to MAX_AWAIT_MS
        change = stream.try_next()
        if change and change["ns"]["coll"] == "events":
            break
    assert change is not None
    message = incident_stream.change_message(change)
    assert message == {
        "collection": "events",
        "op": "insert",
        "incident_id": "inc-stream-1",
        "pond_id": "pond-61",
        "event": "incident_open",
    }


def test_endpoint_answers_503_without_a_replica_set(client, monkeypatch):
    def unavailable():
        raise incident_stream.StreamUnavailable("standalone")

    monkeypatch.setattr(incident_stream, "open_stream", unavailable)
    assert client.get("/stream/incidents").status_code == 503
