from __future__ import annotations

import time

import httpx

from app.core.config import settings
from app.db import get_client, incidents_collection
from app.core.log import get_logger

logger = get_logger("demo_incident")


def main():
    api_url = settings.API_URL.rstrip("/")
    try:
        get_client().admin.command("ping")
    except Exception as e:
        logger.error("mongo not reachable", extra={"err": str(e)})
        return 1

    target_pond = "pond-03"

    logger.info("=== DEMO: phat hien su co tran ven (quat nuoc hong -> oxy sut) ===")
    logger.info(f"Pond target: {target_pond}")

    health = httpx.get(f"{api_url}/healthz").json()
    logger.info("API health", extra={"health": health})

    before_ponds = httpx.get(f"{api_url}/ponds").json()
    logger.info("pump state BEFORE", extra={p["_id"]: p.get("pump", {}).get("failed") for p in before_ponds})

    logger.info(f"Injecting pump failure on {target_pond}")
    httpx.post(f"{api_url}/ponds/{target_pond}/pump/fail", params={"failed": "true"}).raise_for_status()

    logger.info("Waiting for simulator and worker to detect (60-90 seconds)...")
    for i in range(1, 16):
        time.sleep(6)
        incidents = (
            httpx.get(f"{api_url}/incidents", params={"state": "open"}).json()
            + httpx.get(f"{api_url}/incidents", params={"state": "investigating"}).json()
        )
        target_in = [x for x in incidents if x.get("pond_id") == target_pond]
        state = target_in[-1] if target_in else None
        logger.info(
            f"tick {i} -> open incidents on {target_pond}: {len(target_in)}",
            extra={"incident": state},
        )
        if state:
            logger.info("=== DIEU TRA SỰ CỐ ===")
            logger.info("title: " + state.get("title", ""))
            logger.info("description: " + state.get("description", ""))
            logger.info("evidence: " + str(state.get("evidence", {})))
            logger.info("root_cause: " + str(state.get("root_cause")))
            break

    incident_id = None
    for state in list(incidents_collection().find({"pond_id": target_pond}).sort("created_at", -1).limit(3)):
        incident_id = state["_id"]
        break

    if incident_id:
        logger.info("=== XỬ LÝ SỰ CỐ ===")
        httpx.patch(
            f"{api_url}/incidents/{incident_id}",
            params={"state": "investigating", "action": "KT điện quạt, báo bảo trì"},
        )
        logger.info(f"Incident {incident_id} -> INVESTIGATING (điều tra)")

        time.sleep(3)
        logger.info("Repairing pump -> restore power")
        httpx.post(f"{api_url}/ponds/{target_pond}/pump/fail", params={"failed": "false"})
        httpx.patch(
            f"{api_url}/incidents/{incident_id}",
            params={"state": "resolving", "action": "Thay phụ tùng, khởi động lại quạt"},
        )
        logger.info(f"Incident {incident_id} -> RESOLVING (quạt đã hoạt động)")

        time.sleep(3)
        logger.info("=== HỒI PHỤC ===")
        httpx.patch(
            f"{api_url}/incidents/{incident_id}",
            params={"state": "recovered", "action": "AO đã ổn định DO > 5mg/L"},
        )
        logger.info(f"Incident {incident_id} -> RECOVERED (hoàn tất luồng sự cố)")
    else:
        logger.warning("Chưa phát hiện sự cố. Chạy worker/simulator lâu hơn.")

    logger.info("=== DEMO HOÀN TẤT ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
