from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["control"])

_PAGE = Path(__file__).resolve().parents[1] / "static" / "control.html"


@router.get("/control", include_in_schema=False)
def control_panel() -> FileResponse:
    """Bang dieu khien simulator: tat quat, lech DO/nhiet do/pH/do man cho tung ao."""
    return FileResponse(_PAGE, media_type="text/html")
