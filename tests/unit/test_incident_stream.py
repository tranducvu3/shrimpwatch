import asyncio
import json

from app.db.pipelines import incident_changes_pipeline
from app.services.incident_stream import change_message, sse_events


def test_pipeline_watches_incidents_and_new_events_only():
    match = incident_changes_pipeline()[0]["$match"]["$or"]
    assert {m["ns.coll"] for m in match} == {"incidents", "events"}
    assert "metrics" not in json.dumps(match)


def test_incident_update_message_uses_the_document_key():
    change = {
        "ns": {"coll": "incidents"},
        "operationType": "update",
        "documentKey": {"_id": "inc-1"},
        "fullDocument": {"pond_id": "pond-01", "state": "resolving", "severity": "critical"},
    }
    assert change_message(change) == {
        "collection": "incidents",
        "op": "update",
        "incident_id": "inc-1",
        "pond_id": "pond-01",
        "state": "resolving",
        "severity": "critical",
    }


def test_event_message_uses_its_incident_id():
    change = {
        "ns": {"coll": "events"},
        "operationType": "insert",
        "documentKey": {"_id": "65f0"},
        "fullDocument": {"pond_id": "pond-02", "incident_id": "inc-2", "event": "incident_open"},
    }
    message = change_message(change)
    assert (message["incident_id"], message["event"]) == ("inc-2", "incident_open")


class FakeStream:
    def __init__(self, changes):
        self.changes = list(changes)
        self.closed = False

    def try_next(self):
        return self.changes.pop(0) if self.changes else None

    def close(self):
        self.closed = True


def test_sse_events_sends_changes_and_keep_alives_then_closes():
    change = {
        "ns": {"coll": "incidents"},
        "operationType": "insert",
        "documentKey": {"_id": "inc-3"},
        "fullDocument": {},
    }
    stream = FakeStream([change])
    checks = iter([False, False, True])  # disconnected on the third check

    async def is_disconnected():
        return next(checks)

    async def collect():
        return [chunk async for chunk in sse_events(stream, is_disconnected)]

    chunks = asyncio.run(collect())
    assert chunks[0].startswith("retry:")
    assert json.loads(chunks[1].removeprefix("data: "))["incident_id"] == "inc-3"
    assert chunks[2] == ": keep-alive\n\n"
    assert stream.closed


def test_a_mongo_error_ends_the_stream_cleanly():
    from pymongo.errors import OperationFailure

    class BrokenStream(FakeStream):
        def try_next(self):
            raise OperationFailure("change stream invalidated")

    stream = BrokenStream([])

    async def connected():
        return False

    async def collect():
        return [chunk async for chunk in sse_events(stream, connected)]

    assert asyncio.run(collect()) == ["retry: 5000\n\n"]
    assert stream.closed


def test_open_streams_are_capped(monkeypatch):
    import pytest

    from app.services import incident_stream

    monkeypatch.setattr(incident_stream.changes_repo, "watch_incident_changes", lambda: FakeStream([]))
    monkeypatch.setattr(incident_stream, "_open_streams", 0)
    monkeypatch.setattr(incident_stream, "MAX_STREAMS", 2)
    streams = [incident_stream.open_stream(), incident_stream.open_stream()]
    with pytest.raises(incident_stream.StreamUnavailable):
        incident_stream.open_stream()

    async def gone():
        return True

    async def drain(s):
        return [c async for c in sse_events(s, gone)]

    asyncio.run(drain(streams[0]))  # a client leaving frees its slot
    assert incident_stream.open_stream() is not None
