import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import settings

router = APIRouter(tags=["control"])

_PAGE = Path(__file__).resolve().parents[1] / "static" / "control.html"

# Browser SDK CDN path per Datadog site. v6: the CDN has no v5 build for ap2 (it answers 403)
_RUM_CDN_REGION = {
    "datadoghq.com": "us1",
    "us3.datadoghq.com": "us3",
    "us5.datadoghq.com": "us5",
    "datadoghq.eu": "eu1",
    "ap1.datadoghq.com": "ap1",
    "ap2.datadoghq.com": "ap2",
}
_APPLICATION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CLIENT_TOKEN = re.compile(r"pub[0-9a-f]{32}")


def rum_snippet(application_id: str, client_token: str, site: str, env: str) -> str:
    """Datadog RUM for the control panel; "" unless both ids are set and well formed (they go into a <script>).

    allowedTracingUrls makes the browser send trace headers on the panel's own API calls, so a farmer's click in
    RUM opens the backend trace of that request (ddtrace picks up the headers) down to the MongoDB spans.
    """
    region = _RUM_CDN_REGION.get(site)
    if not (region and _APPLICATION_ID.fullmatch(application_id) and _CLIENT_TOKEN.fullmatch(client_token)):
        return ""
    config = {
        "applicationId": application_id,
        "clientToken": client_token,
        "site": site,
        "service": "shrimpwatch-control",
        "env": env,
        "sessionSampleRate": 100,
        "sessionReplaySampleRate": 100,
        "trackUserInteractions": True,
        "trackResources": True,
        "trackLongTasks": True,
        "defaultPrivacyLevel": "mask-user-input",
    }
    config_js = json.dumps(config).replace("</", "<\\/")  # no "</script>" inside the inline script
    return (
        f'<script src="https://www.datadoghq-browser-agent.com/{region}/v6/datadog-rum.js"></script>\n'
        f"<script>window.DD_RUM && window.DD_RUM.init(Object.assign({config_js}, "
        "{allowedTracingUrls: [window.location.origin]}));</script>\n"
    )


@lru_cache(maxsize=1)
def _page() -> str:
    snippet = rum_snippet(
        settings.DD_RUM_APPLICATION_ID, settings.DD_RUM_CLIENT_TOKEN, settings.DD_SITE, settings.DD_ENV
    )
    return _PAGE.read_text(encoding="utf-8").replace("</head>", f"{snippet}</head>", 1)


@router.get("/control", include_in_schema=False)
def control_panel() -> HTMLResponse:
    """Simulator control panel: stop the aerator, offset DO/temperature/pH/salinity per pond."""
    return HTMLResponse(_page())
