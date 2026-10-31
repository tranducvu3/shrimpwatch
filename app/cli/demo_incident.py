"""Kich ban demo vong doi su co: quat ao pond-03 hong -> oxy sut -> dieu tra -> sua -> hoi phuc.

Chi goi HTTP API, giong thao tac cua ky thuat vien that.
"""

from __future__ import annotations

import time

from app.core.log import get_logger
from app.simulator.api_client import ShrimpWatchClient

logger = get_logger("demo_incident")

TARGET_POND = "pond-03"


def _wait_for_incident(api: ShrimpWatchClient, pond_id: str) -> dict | None:
    for i in range(1, 16):
        time.sleep(6)
        active = api.incidents("open") + api.incidents("investigating")
        on_pond = [x for x in active if x.get("pond_id") == pond_id]
        incident = on_pond[-1] if on_pond else None
        logger.info(f"tick {i} -> open incidents on {pond_id}: {len(on_pond)}", extra={"incident": incident})
        if incident:
            return incident
    return None


def _log_investigation(incident: dict) -> None:
    logger.info("=== DIEU TRA SỰ CỐ ===")
    logger.info("title: " + incident.get("title", ""))
    logger.info("description: " + incident.get("description", ""))
    logger.info("evidence: " + str(incident.get("evidence", {})))
    logger.info("root_cause: " + str(incident.get("root_cause")))


def _latest_incident_id(api: ShrimpWatchClient, pond_id: str) -> str | None:
    # /incidents da sap xep created_at giam dan
    return next((x["_id"] for x in api.incidents() if x.get("pond_id") == pond_id), None)


def _transition(api: ShrimpWatchClient, incident_id: str, state: str, action: str) -> None:
    resp = api.transition_incident(incident_id, state, action)
    if resp.status_code >= 400:
        logger.warning(
            f"Incident {incident_id} -> {state} rejected", extra={"status": resp.status_code, "body": resp.text}
        )


def _resolve(api: ShrimpWatchClient, pond_id: str, incident_id: str) -> None:
    logger.info("=== XỬ LÝ SỰ CỐ ===")
    _transition(api, incident_id, "investigating", "KT điện quạt, báo bảo trì")
    logger.info(f"Incident {incident_id} -> INVESTIGATING (điều tra)")

    time.sleep(3)
    logger.info("Repairing pump -> restore power")
    api.set_pump_failed(pond_id, False)
    _transition(api, incident_id, "resolving", "Thay phụ tùng, khởi động lại quạt")
    logger.info(f"Incident {incident_id} -> RESOLVING (quạt đã hoạt động)")

    time.sleep(3)
    logger.info("=== HỒI PHỤC ===")
    _transition(api, incident_id, "recovered", "AO đã ổn định DO > 5mg/L")
    logger.info(f"Incident {incident_id} -> RECOVERED (hoàn tất luồng sự cố)")


def main():
    api = ShrimpWatchClient()
    try:
        health = api.health()
    except Exception as e:
        logger.error("API not reachable", extra={"err": str(e)})
        return 1
    if not health.get("mongo"):
        logger.error("mongo not reachable", extra={"health": health})
        return 1

    logger.info("=== DEMO: phat hien su co tran ven (quat nuoc hong -> oxy sut) ===")
    logger.info(f"Pond target: {TARGET_POND}")
    logger.info("API health", extra={"health": health})
    logger.info("pump state BEFORE", extra={p["_id"]: p.get("pump", {}).get("failed") for p in api.ponds()})

    logger.info(f"Injecting pump failure on {TARGET_POND}")
    api.set_pump_failed(TARGET_POND, True).raise_for_status()

    logger.info("Waiting for simulator and worker to detect (60-90 seconds)...")
    incident = _wait_for_incident(api, TARGET_POND)
    if incident:
        _log_investigation(incident)

    incident_id = _latest_incident_id(api, TARGET_POND)
    if incident_id:
        _resolve(api, TARGET_POND, incident_id)
    else:
        logger.warning("Chưa phát hiện sự cố. Chạy worker/simulator lâu hơn.")

    logger.info("=== DEMO HOÀN TẤT ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
