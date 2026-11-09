"""Datadog: DogStatsD metrics and tags for the current span. A failed metric send must never break the main flow."""

import re
import unicodedata

from datadog import initialize, statsd
from ddtrace import tracer

from app.core.config import settings


def init_metrics():
    initialize(
        statsd_host=settings.DD_AGENT_HOST,
        statsd_port=settings.DD_DOGSTATSD_PORT,
        statsd_namespace="shrimpwatch",
    )


def _tags(tags: dict | None) -> list[str]:
    return [f"{k}:{v}" for k, v in (tags or {}).items()] + [
        f"farm_id:{settings.FARM_ID}",
        f"env:{settings.DD_ENV}",
        f"service:{settings.DD_SERVICE}",
    ]


_NOT_TAG_CHAR = re.compile(r"[^\w\-./]+")


def tag_value(text: str) -> str:
    """Free text (an incident title) as a tag value that reads the same in Datadog.

    Datadog lowercases tags, keeps Unicode letters and turns other characters into underscores; doing it here keeps
    `|`, `,` and `#` out of the DogStatsD packet. NFKC turns subscripts into digits (NH₃ -> nh3).
    """
    text = unicodedata.normalize("NFKC", text).lower()
    return _NOT_TAG_CHAR.sub("_", text).strip("_")[:200]


def gauge(name: str, value: float, tags: dict | None = None):
    try:
        statsd.gauge(name, value, tags=_tags(tags))
    except Exception:
        pass


def increment(name: str, value: int = 1, tags: dict | None = None):
    try:
        statsd.increment(name, value=value, tags=_tags(tags))
    except Exception:
        pass


def distribution(name: str, value: float, tags: dict | None = None):
    """Server-side aggregated: Datadog computes avg/p95 across all ponds and hosts."""
    try:
        statsd.distribution(name, value, tags=_tags(tags))
    except Exception:
        pass


def event(title: str, text: str, alert_type: str, aggregation_key: str, tags: dict | None = None):
    """A Datadog Event through the agent (no API key in the app). alert_type: info, warning, error or success."""
    try:
        statsd.event(title, text, alert_type=alert_type, aggregation_key=aggregation_key, tags=_tags(tags))
    except Exception:
        pass


def set_span_tags(pond_id: str | None = None, device_type: str | None = None, **extra):
    span = tracer.current_span()
    if span is None:
        return
    if pond_id:
        span.set_tag("pond", pond_id)
    if device_type:
        span.set_tag("device_type", device_type)
    span.set_tag("farm_id", settings.FARM_ID)
    for k, v in extra.items():
        span.set_tag(k, v)
