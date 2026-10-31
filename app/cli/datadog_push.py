"""Day dashboard va monitor trong thu muc datadog/ len Datadog API."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("datadog_push")

ROOT = Path(__file__).resolve().parents[2]


def _headers() -> dict[str, str]:
    return {
        "DD-API-KEY": settings.DD_API_KEY,
        "DD-APPLICATION-KEY": settings.DD_APP_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _push(kind: str, path: Path):
    """kind: 'dashboard' hoac 'monitor' (ten endpoint /api/v1/<kind>)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    site = settings.DD_SITE or "datadoghq.com"
    resp = httpx.post(f"https://api.{site}/api/v1/{kind}", headers=_headers(), json=payload, timeout=30)
    logger.info(f"push {kind}", extra={"file": path.name, "status": resp.status_code, "body": resp.text[:500]})
    return resp.status_code, resp.json() if resp.status_code < 400 else resp.text


def main():
    if not settings.DD_API_KEY or not settings.DD_APP_KEY:
        logger.error("DD_API_KEY/DD_APP_KEY chưa cấu hình trong .env")
        return 1
    for kind, folder in (("dashboard", "dashboards"), ("monitor", "monitors")):
        for p in sorted((ROOT / "datadog" / folder).glob("*.json")):
            _push(kind, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
