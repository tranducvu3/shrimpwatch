"""Day dashboard va monitor trong thu muc datadog/ len Datadog API (cap nhat theo ten neu da co)."""

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


def _base_url() -> str:
    return f"https://api.{settings.DD_SITE or 'datadoghq.com'}/api/v1"


def _existing_id(kind: str, payload: dict) -> str | int | None:
    """Tim ban da co cung ten (monitor: name, dashboard: title) de cap nhat thay vi tao trung."""
    if kind == "monitor":
        resp = httpx.get(f"{_base_url()}/monitor", headers=_headers(), params={"name": payload["name"]}, timeout=30)
        resp.raise_for_status()
        matches = [m["id"] for m in resp.json() if m.get("name") == payload["name"]]
    else:
        resp = httpx.get(f"{_base_url()}/dashboard", headers=_headers(), timeout=30)
        resp.raise_for_status()
        matches = [d["id"] for d in resp.json().get("dashboards", []) if d.get("title") == payload["title"]]
    return matches[0] if matches else None


def _push(kind: str, path: Path):
    """kind: 'dashboard' hoac 'monitor' (ten endpoint /api/v1/<kind>). Co roi thi PUT, chua co thi POST."""
    payload = json.loads(path.read_text(encoding="utf-8"))
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
        logger.error("DD_API_KEY/DD_APP_KEY chưa cấu hình trong .env")
        return 1
    for kind, folder in (("dashboard", "dashboards"), ("monitor", "monitors")):
        for p in sorted((ROOT / "datadog" / folder).glob("*.json")):
            _push(kind, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
