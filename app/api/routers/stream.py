from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.services import incident_stream

router = APIRouter(prefix="/stream", tags=["stream"])


@router.get("/incidents")
async def incident_changes(request: Request):
    """Server-sent events: one message per incident change or new event (MongoDB Change Streams)."""
    try:
        stream = await run_in_threadpool(incident_stream.open_stream)
    except incident_stream.StreamUnavailable as e:
        raise HTTPException(503, "live updates need MongoDB as a replica set") from e
    return StreamingResponse(
        incident_stream.sse_events(stream, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
