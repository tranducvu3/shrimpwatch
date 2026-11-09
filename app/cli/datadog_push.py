"""Push the dashboards, monitors and SLOs in datadog/ to the Datadog API (update by name if they already exist)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.datadog_links import app_base_url
from app.core.log import get_logger

logger = get_logger("datadog_push")

ROOT = Path(__file__).resolve().parents[2]

NOTIFY_EMAIL_TAG = "notify:email"
_EMAIL = re.compile(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+")


def _headers() -> dict[str, str]:
    return {
        "DD-API-KEY": settings.DD_API_KEY,
        "DD-APPLICATION-KEY": settings.DD_APP_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return f"https://api.{settings.DD_SITE or 'datadoghq.com'}/api/v1"


def _existing_id(kind: str, payload: dict) -> str | int | None:
    """Find an existing one with the same name (monitor: name, dashboard: title) to update instead of creating a duplicate.

    A renamed monitor is found by its query instead, so a new name updates it rather than adding a second alert.
    """
    if kind == "monitor":
        resp = httpx.get(f"{_base_url()}/monitor", headers=_headers(), params={"name": payload["name"]}, timeout=30)
        resp.raise_for_status()
        matches = [m["id"] for m in resp.json() if m.get("name") == payload["name"]]
        if not matches:
            resp = httpx.get(
                f"{_base_url()}/monitor", headers=_headers(), params={"monitor_tags": "shrimpwatch"}, timeout=30
            )
            resp.raise_for_status()
            matches = [m["id"] for m in resp.json() if m.get("query") == payload.get("query")]
    elif kind == "slo":
        # All ShrimpWatch SLOs by tag, then an exact name match (name search is fuzzy with brackets and diacritics)
        params = {"tags_query": "service:shrimpwatch", "limit": 1000}
        resp = httpx.get(f"{_base_url()}/slo", headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        matches = [s["id"] for s in resp.json().get("data") or [] if s.get("name") == payload["name"]]
    else:
        resp = httpx.get(f"{_base_url()}/dashboard", headers=_headers(), timeout=30)
        resp.raise_for_status()
        matches = [d["id"] for d in resp.json().get("dashboards", []) if d.get("title") == payload["title"]]
    return matches[0] if matches else None


def farmer_alert_emails() -> list[str]:
    """Addresses in FARMER_ALERT_EMAILS (comma separated); invalid entries are skipped with a warning.

    They live in .env only: the repo is public, so the monitor JSON carries no address.
    """
    emails = []
    for position, entry in enumerate(settings.FARMER_ALERT_EMAILS.split(","), start=1):
        entry = entry.strip()
        if _EMAIL.fullmatch(entry):
            emails.append(entry)
        elif entry:
            logger.warning("skipping an invalid FARMER_ALERT_EMAILS entry", extra={"position": position})
    return emails


def with_notify_handles(payload: dict, emails: Sequence[str]) -> dict:
    """Add an @email handle per address to monitors tagged notify:email; rebuilt from the JSON on every push, so
    removing an address and pushing again stops its emails."""
    if not emails or NOTIFY_EMAIL_TAG not in payload.get("tags", []):
        return payload
    handles = " ".join(f"@{e}" for e in emails)
    return {**payload, "message": f"{payload.get('message', '')}\n\n{handles}"}


def link_bases() -> dict[str, str]:
    """Placeholders the dashboard JSON uses in widget custom links (the repo cannot know the hosts)."""
    return {"$DD_APP_URL": app_base_url(), "$PUBLIC_BASE_URL": settings.PUBLIC_BASE_URL.rstrip("/")}


def with_link_bases(payload: dict, bases: dict[str, str]) -> dict:
    """Fill the placeholders in every widget's custom links; a link whose base is not configured is dropped."""

    def fill(widget: dict) -> dict:
        definition = dict(widget["definition"])
        if "widgets" in definition:
            definition["widgets"] = [fill(w) for w in definition["widgets"]]
        if "custom_links" in definition:
            definition["custom_links"] = [
                {**link, "link": url} for link in definition["custom_links"] if (url := fill_url(link["link"]))
            ]
        return {**widget, "definition": definition}

    def fill_url(url: str) -> str:
        for placeholder, base in bases.items():
            if url.startswith(placeholder):
                return f"{base}{url[len(placeholder) :]}" if base else ""
        return url

    return {**payload, "widgets": [fill(w) for w in payload.get("widgets", [])]}


def _push(kind: str, path: Path, emails: Sequence[str] = ()):
    """kind: 'dashboard', 'monitor' or 'slo' (endpoint /api/v1/<kind>). PUT if it exists, otherwise POST."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if kind == "monitor":
        payload = with_notify_handles(payload, emails)
    elif kind == "dashboard":
        payload = with_link_bases(payload, link_bases())
    existing = _existing_id(kind, payload)
    if existing is None:
        resp = httpx.post(f"{_base_url()}/{kind}", headers=_headers(), json=payload, timeout=30)
    else:
        resp = httpx.put(f"{_base_url()}/{kind}/{existing}", headers=_headers(), json=payload, timeout=30)
    logger.info(
        f"push {kind}",
        extra={"file": path.name, "id": existing, "status": resp.status_code, "body": resp.text[:500]},
    )
    if kind == "dashboard" and resp.status_code < 400:
        logger.info("dashboard pushed: set DD_DASHBOARD_ID in .env", extra={"dashboard_id": resp.json().get("id")})
    return resp.status_code, resp.json() if resp.status_code < 400 else resp.text


def main():
    if not settings.DD_API_KEY or not settings.DD_APP_KEY:
        logger.error("DD_API_KEY/DD_APP_KEY are not set in .env")
        return 1
    emails = farmer_alert_emails()
    if not emails:
        logger.info("FARMER_ALERT_EMAILS not set: the farmer alert monitors email nobody")
    failed = []
    for kind, folder in (("dashboard", "dashboards"), ("monitor", "monitors"), ("slo", "slos")):
        for p in sorted((ROOT / "datadog" / folder).glob("*.json")):
            status, _ = _push(kind, p, emails)
            if status >= 400:
                failed.append(p.name)
    if failed:
        logger.error("some Datadog files were rejected", extra={"files": failed})
    # Keep pushing the rest, but a rejected file is a failed run
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
