"""HTTP client for the ShrimpWatch API, used by the simulator and the demo script."""

from __future__ import annotations

from datetime import datetime

import httpx

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("simulator.api_client")


class ShrimpWatchClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.API_URL).rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def pond_states(self) -> dict[str, dict]:
        """pond_id -> pond document (pump failure, scenarios). A network error returns {} (treated as normal ponds)."""
        try:
            r = httpx.get(self._url("/ponds"), timeout=5.0)
            r.raise_for_status()
            return {p["_id"]: p for p in r.json()}
        except Exception as e:
            logger.warning("fetch ponds failed", extra={"err": str(e)})
            return {}

    def active_system_faults(self) -> set[str]:
        """Demo faults switched on from the control panel; a network error returns none."""
        try:
            r = httpx.get(self._url("/system-faults"), timeout=5.0)
            r.raise_for_status()
            return {f["type"] for f in r.json()["faults"] if f["active"]}
        except Exception as e:
            logger.warning("fetch system faults failed", extra={"err": str(e)})
            return set()

    def latest_reading_time(self, window_min: int) -> datetime | None:
        """Time of the latest sensor record (any pond, any metric) within `window_min` minutes; None if there is none."""
        r = httpx.get(self._url("/ponds/readings"), params={"window_min": window_min}, timeout=30.0)
        r.raise_for_status()
        stamps = [m["timestamp"] for metrics in r.json().values() for m in metrics.values()]
        return datetime.fromisoformat(max(stamps)) if stamps else None

    def ingest_batch(self, records: list[dict], timeout: float = 15.0) -> httpx.Response:
        return httpx.post(self._url("/ingest/batch"), json={"records": records}, timeout=timeout)

    def ingest_manual(self, metric: str, pond_id: str, value: float, timestamp: datetime, tester_id: str = "tech-001"):
        return httpx.post(
            self._url(f"/ingest/manual/{metric}"),
            json={
                "timestamp": timestamp.isoformat(),
                "farm_id": settings.FARM_ID,
                "pond_id": pond_id,
                "value": value,
                "tester_id": tester_id,
            },
            timeout=10.0,
        )

    def health(self) -> dict:
        return httpx.get(self._url("/healthz")).json()

    def ponds(self) -> list[dict]:
        return httpx.get(self._url("/ponds")).json()

    def set_pump_failed(self, pond_id: str, failed: bool) -> httpx.Response:
        return httpx.post(self._url(f"/ponds/{pond_id}/pump/fail"), params={"failed": str(failed).lower()})

    def incidents(self, state: str | None = None) -> list[dict]:
        return httpx.get(self._url("/incidents"), params={"state": state} if state else None).json()

    def transition_incident(self, incident_id: str, state: str, action: str) -> httpx.Response:
        return httpx.patch(self._url(f"/incidents/{incident_id}"), params={"state": state, "action": action})
