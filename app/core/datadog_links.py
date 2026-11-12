"""Deep links into the Datadog UI for one pond and time window, so the incident panel opens the right view."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.core.clock import to_naive_utc
from app.core.config import settings

# Show a little context before the incident opened and after its last change
LEAD = timedelta(minutes=15)
TRAIL = timedelta(minutes=5)


def app_base_url(site: str | None = None) -> str:
    """datadoghq.com -> https://app.datadoghq.com; regional sites (us5.datadoghq.com, ap1...) are their own host."""
    site = site or settings.DD_SITE or "datadoghq.com"
    host = site if site.count(".") >= 2 else f"app.{site}"
    return f"https://{host}"


def _ms(ts: datetime) -> int:
    """Epoch milliseconds; naive values are UTC (the app convention), aware ones are converted."""
    return int(to_naive_utc(ts).replace(tzinfo=timezone.utc).timestamp() * 1000)


def dashboard_url(params: dict | None = None) -> str:
    """The ShrimpWatch dashboard (DD_DASHBOARD_ID), or the dashboard list filtered to ShrimpWatch when it is unset."""
    base = app_base_url()
    if settings.DD_DASHBOARD_ID:
        query = f"?{urlencode(params)}" if params else ""
        return f"{base}/dashboard/{settings.DD_DASHBOARD_ID}{query}"
    return f"{base}/dashboard/lists?" + urlencode({"q": "ShrimpWatch"})


def incident_links(incident_id: str, pond_id: str, start: datetime, end: datetime) -> dict[str, str]:
    """Dashboard (pond template variable), logs, traces and events of the pond around the incident."""
    base = app_base_url()
    from_ms, to_ms = _ms(start - LEAD), _ms(end + TRAIL)
    query = f"service:{settings.DD_SERVICE} @pond:{pond_id}"
    window = {"from_ts": from_ms, "to_ts": to_ms, "live": "false"}
    return {
        "dashboard": dashboard_url({"tpl_var_ao": pond_id, **window}),
        "logs": f"{base}/logs?" + urlencode({"query": query, **window}),
        "traces": f"{base}/apm/traces?" + urlencode({"query": query, "start": from_ms, "end": to_ms}),
        "events": f"{base}/event/explorer?" + urlencode({"query": f"incident_id:{incident_id}", **window}),
    }
