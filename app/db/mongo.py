"""Ket noi MongoDB va truy cap tung collection theo ten."""

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.core.config import settings

_client: MongoClient | None = None


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
