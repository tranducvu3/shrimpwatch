"""MongoDB connection, access to each collection by name, and multi-document transactions."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import TypeVar

from pymongo import MongoClient, ReadPreference
from pymongo.client_session import ClientSession
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.core.config import settings

_client: MongoClient | None = None
_session: ContextVar[ClientSession | None] = ContextVar("mongo_session", default=None)
T = TypeVar("T")


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
        _client.admin.command("ping")
    return _client


def get_db() -> Database:
    return get_client()[settings.MONGODB_DB]


def ping() -> None:
    get_client().admin.command("ping")


def session() -> ClientSession | None:
    """The session of the transaction running in this context, or None: repositories pass it to every call."""
    return _session.get()


def run_in_transaction(fn: Callable[[], T]) -> T:
    """Run fn's reads and writes as one multi-document transaction: all of its writes are committed, or none.

    Reads see one snapshot and writes are majority-acknowledged. MongoDB retries fn on a transient error (a write
    conflict with another writer, a primary election), so fn must only touch MongoDB: send telemetry after it
    returns. Any other exception aborts the transaction and is raised. A call inside a transaction joins it.
    Needs a replica set (Atlas is one; Compose and CI run a single-node one).
    """
    if _session.get() is not None:
        return fn()
    with get_client().start_session() as s:
        token = _session.set(s)
        try:
            return s.with_transaction(
                lambda _: fn(),
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                read_preference=ReadPreference.PRIMARY,
            )
        finally:
            _session.reset(token)


METRICS = "metrics"
METRICS_HOURLY = "metrics_hourly"
INCIDENTS = "incidents"
PONDS = "ponds"
EVENTS = "events"

ALL = [METRICS, METRICS_HOURLY, INCIDENTS, PONDS, EVENTS]


def metrics_collection() -> Collection:
    return get_db()[METRICS]


def metrics_hourly_collection() -> Collection:
    return get_db()[METRICS_HOURLY]


def incidents_collection() -> Collection:
    return get_db()[INCIDENTS]


def ponds_collection() -> Collection:
    return get_db()[PONDS]


def events_collection() -> Collection:
    return get_db()[EVENTS]
