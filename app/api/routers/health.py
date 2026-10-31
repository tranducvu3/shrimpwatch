from fastapi import APIRouter

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.db.mongo import ping

logger = get_logger("api")
router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    try:
        ping()
        mongo_ok = True
    except Exception as e:
        mongo_ok = False
        logger.error("Mongo ping failed", extra={"err": str(e)})
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "farm_id": settings.FARM_ID,
        "env": settings.DD_ENV,
        "timestamp": utcnow().isoformat(),
    }
