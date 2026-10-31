from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import control, health, incidents, ingest, ponds
from app.core.log import get_logger
from app.core.telemetry import init_metrics
from app.db.schema import init_collections

logger = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_metrics()
    try:
        init_collections()
    except Exception as e:
        logger.warning("init_collections skipped (expected if DB not ready yet)", extra={"err": str(e)})
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="ShrimpWatch Water Quality Ingest API",
        version="1.0.0",
        description="Ingests sensor/manual/derived metrics for 6 shrimp ponds, emits Datadog metrics & traces.",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    for router in (health.router, ingest.router, ponds.router, incidents.router, control.router):
        app.include_router(router)
    return app


app = create_app()
