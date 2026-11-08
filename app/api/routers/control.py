from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["control"])

_PAGE = Path(__file__).resolve().parents[1] / "static" / "control.html"


@router.get("/control", include_in_schema=False)
def control_panel() -> FileResponse:
    """Simulator control panel: stop the aerator, offset DO/temperature/pH/salinity per pond."""
    return FileResponse(_PAGE, media_type="text/html")
