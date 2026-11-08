"""Push the dashboards and monitors in datadog/ to the Datadog API (update by name if they already exist)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("datadog_push")

ROOT = Path(__file__).resolve().parents[2]

NOTIFY_EMAIL_TAG = "notify:email"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


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
    else:
        resp = httpx.get(f"{_base_url()}/dashboard", headers=_headers(), timeout=30)
        resp.raise_for_status()
        matches = [d["id"] for d in resp.json().get("dashboards", []) if d.get("title") == payload["title"]]
    return matches[0] if matches else None


def _notify_handles() -> list[str]:
    """Datadog `@email` handles from DD_NOTIFY_EMAILS (comma separated). Entries that are not an email are skipped."""
    handles = []
    for entry in settings.DD_NOTIFY_EMAILS.split(","):
        email = entry.strip()
        if not email:
            continue
        if not _EMAIL.fullmatch(email):
            logger.warning("skipping DD_NOTIFY_EMAILS entry that is not an email", extra={"entry": email})
            continue
        handles.append(f"@{email}")
    return handles


def _with_recipients(payload: dict) -> dict:
    """Append the email handles to a monitor tagged notify:email, so the addresses stay in .env, not in the repo.

    The message is rebuilt from the JSON on every push, so removing an address and pushing again stops its emails.
    """
    if NOTIFY_EMAIL_TAG not in payload.get("tags", []):
        return payload
    handles = _notify_handles()
    if not handles:
        return payload
    return {**payload, "message": f"{payload['message']}\n\nNotify: {' '.join(handles)}"}


def _push(kind: str, path: Path):
    """kind: 'dashboard' or 'monitor' (endpoint /api/v1/<kind>). PUT if it exists, otherwise POST."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if kind == "monitor":
        payload = _with_recipients(payload)
    existing = _existing_id(kind, payload)
    if existing is None:
        resp = httpx.post(f"{_base_url()}/{kind}", headers=_headers(), json=payload, timeout=30)
    else:
        resp = httpx.put(f"{_base_url()}/{kind}/{existing}", headers=_headers(), json=payload, timeout=30)
    logger.info(
        f"push {kind}",
        extra={"file": path.name, "id": existing, "status": resp.status_code, "body": resp.text[:500]},
    )
    return resp.status_code, resp.json() if resp.status_code < 400 else resp.text


def main():
    if not settings.DD_API_KEY or not settings.DD_APP_KEY:
        logger.error("DD_API_KEY/DD_APP_KEY are not set in .env")
        return 1
    for kind, folder in (("dashboard", "dashboards"), ("monitor", "monitors")):
        for p in sorted((ROOT / "datadog" / folder).glob("*.json")):
            _push(kind, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
