import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import automation, control, health, incidents, ingest, ponds, stream, system_faults
from app.core.log import get_logger
from app.core.telemetry import init_metrics
from app.db.schema import init_collections
from app.domain.system_faults import API_ERROR_SHARE, FAULTS, FaultType
from app.services import system_faults as fault_service

logger = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_metrics()
    try:
        init_collections()
    except Exception as e:
        logger.warning("init_collections skipped (expected if DB not ready yet)", extra={"err": str(e)})
    yield


# Never failed by the "API errors" demo fault: the panel must still load and switch the fault off
_FAULT_EXEMPT = ("/control", "/system-faults", "/healthz", "/stream", "/docs", "/openapi.json")


async def inject_api_errors(request: Request, call_next):
    """While the API-errors demo fault is on, answer a share of requests with 503, as a failing backend would.

    The response goes through the APM middleware like any other, so Datadog counts it in the error rate.
    """
    path = request.url.path
    if (
        not path.startswith(_FAULT_EXEMPT)
        and FaultType.API_ERRORS in fault_service.active_cached()
        and random.random() < API_ERROR_SHARE
    ):
        # Shown as is in the panel's header ("Lỗi API: 503: …")
        detail = f"sự cố hệ thống giả lập ({FAULTS[FaultType.API_ERRORS].label})"
        return JSONResponse({"detail": detail}, status_code=503)
    return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ShrimpWatch Water Quality Ingest API",
        version="1.0.0",
        description="Ingests sensor/manual/derived metrics for 6 shrimp ponds, emits Datadog metrics & traces.",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.middleware("http")(inject_api_errors)
    routers = (health, ingest, ponds, incidents, control, stream, automation, system_faults)
    for router in (r.router for r in routers):
        app.include_router(router)
    return app


app = create_app()
