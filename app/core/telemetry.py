"""Datadog: DogStatsD metric va tag cho span hien tai. Loi gui metric khong bao gio duoc lam hong luong chinh."""

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
