"""Push the dashboards, monitors and SLOs in datadog/ to the Datadog API (update by name if they already exist)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("datadog_push")

ROOT = Path(__file__).resolve().parents[2]

TELEGRAM_WEBHOOK = "shrimpwatch-telegram"
NOTIFY_TELEGRAM_TAG = "notify:telegram"


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


def telegram_webhook() -> dict | None:
    """Datadog webhook that forwards the alert text to the farmer's Telegram chat; None when Telegram is not set up.

    The bot token is part of the URL: it lives in .env and in Datadog's webhook settings, never in the repo or logs.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return None
    body = {"chat_id": settings.TELEGRAM_CHAT_ID, "text": "$TEXT_ONLY_MSG", "disable_web_page_preview": True}
    return {
        "name": TELEGRAM_WEBHOOK,
        "url": f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
        "payload": json.dumps(body, ensure_ascii=False),
        "encode_as": "json",
    }


def _push_telegram_webhook(webhook: dict) -> int:
    """Create or update the webhook. Logs the status only: the response echoes the URL with the bot token."""
    base = f"{_base_url()}/integration/webhooks/configuration/webhooks"
    exists = httpx.get(f"{base}/{TELEGRAM_WEBHOOK}", headers=_headers(), timeout=30).status_code == 200
    if exists:
        resp = httpx.put(f"{base}/{TELEGRAM_WEBHOOK}", headers=_headers(), json=webhook, timeout=30)
    else:
        resp = httpx.post(base, headers=_headers(), json=webhook, timeout=30)
    logger.info("push telegram webhook", extra={"status": resp.status_code, "updated": exists})
    return resp.status_code


def with_notify_handles(payload: dict, telegram: bool) -> dict:
    """Add the @webhook handle to monitors tagged notify:telegram once the webhook exists (Datadog rejects unknown
    handles); rebuilt from the JSON on every push, so removing the Telegram settings stops the alerts."""
    if not telegram or NOTIFY_TELEGRAM_TAG not in payload.get("tags", []):
        return payload
    return {**payload, "message": f"{payload.get('message', '')}\n\n@webhook-{TELEGRAM_WEBHOOK}"}


def _push(kind: str, path: Path, telegram: bool = False):
    """kind: 'dashboard', 'monitor' or 'slo' (endpoint /api/v1/<kind>). PUT if it exists, otherwise POST."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if kind == "monitor":
        payload = with_notify_handles(payload, telegram)
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
    # The webhook must exist before a monitor can notify it
    webhook = telegram_webhook()
    telegram = webhook is not None and _push_telegram_webhook(webhook) < 400
    if webhook is None:
        logger.info("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set: no Telegram farmer alert")
    elif not telegram:
        logger.error("Telegram webhook push failed: monitors are pushed without the farmer alert handle")
    failed = []
    for kind, folder in (("dashboard", "dashboards"), ("monitor", "monitors"), ("slo", "slos")):
        for p in sorted((ROOT / "datadog" / folder).glob("*.json")):
            status, _ = _push(kind, p, telegram)
            if status >= 400:
                failed.append(p.name)
    if failed:
        logger.error("some Datadog files were rejected", extra={"files": failed})
    # Keep pushing the rest, but a rejected file (or Telegram configured but not working) is a failed run
    return 1 if failed or (webhook is not None and not telegram) else 0


if __name__ == "__main__":
    raise SystemExit(main())
