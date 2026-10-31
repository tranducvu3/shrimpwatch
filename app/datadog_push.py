from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.config import settings
from app.logging_setup import get_logger

logger = get_logger("datadog_push")

ROOT = Path(__file__).resolve().parents[1]


def _headers():
    return {
        "DD-API-KEY": settings.DD_API_KEY,
        "DD-APPLICATION-KEY": settings.DD_APP_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _base_url():
    site = settings.DD_SITE or "datadoghq.com"
    return f"https://api.{site}"


def push_dashboard(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    resp = httpx.post(
        f"{_base_url()}/api/v1/dashboard",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    logger.info("push dashboard", extra={"file": path.name, "status": resp.status_code, "body": resp.text[:500]})
    return resp.status_code, resp.json() if resp.status_code < 400 else resp.text


def push_monitor(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    resp = httpx.post(
        f"{_base_url()}/api/v1/monitor",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    logger.info("push monitor", extra={"file": path.name, "status": resp.status_code, "body": resp.text[:500]})
    return resp.status_code, resp.json() if resp.status_code < 400 else resp.text


def main():
    if not settings.DD_API_KEY or not settings.DD_APP_KEY:
        logger.error("DD_API_KEY/DD_APP_KEY chưa cấu hình trong .env")
        return 1

    dash_dir = ROOT / "datadog" / "dashboards"
    monitor_dir = ROOT / "datadog" / "monitors"

    for p in sorted(dash_dir.glob("*.json")):
        push_dashboard(p)
    for p in sorted(monitor_dir.glob("*.json")):
        push_monitor(p)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
