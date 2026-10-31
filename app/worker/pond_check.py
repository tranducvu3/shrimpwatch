"""Kiem tra 1 ao: thu thap so lieu -> ap quy tac (domain) -> mo/cap nhat/dong su co (service)."""

from __future__ import annotations

from datetime import timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import set_span_tags
from app.db.repositories import metrics as metrics_repo
from app.domain.incident_rules import PondSnapshot, Thresholds, evaluate, has_recovered
from app.domain.models import DeviceType, IncidentEvidence
from app.services import derivations
from app.services import incidents as incident_service

logger = get_logger("worker")

RECENT = timedelta(minutes=5)


def load_do_windows(pond_ids: list[str]) -> dict[str, dict]:
    rows = metrics_repo.do_windows(pond_ids, window_sec=settings.DO_WINDOW_SEC)
    for pond_id, row in rows.items():
        logger.info("DO window computed", extra={"pond": pond_id, "row": row})
    return rows


def check_pond(pond_id: str, do_row: dict | None = None) -> None:
    set_span_tags(pond_id=pond_id, device_type=DeviceType.WORKER.value)
    if do_row is None:
        do_row = load_do_windows([pond_id]).get(pond_id)
    do_row = do_row or {}
    now = utcnow()

    derived = derivations.derive_and_record(pond_id, now)

    last_orp = metrics_repo.latest_value(pond_id, "orp", now - RECENT)
    if last_orp is not None and last_orp < settings.ORP_WARN_LOW:
        logger.warning("ORP low: day ao thieu oxy, nguy co H2S", extra={"pond": pond_id, "orp": last_orp})

    snapshot = PondSnapshot(
        pond_id=pond_id,
        avg_pump_watts=metrics_repo.average_value(pond_id, "pump_power", now - RECENT),
        do_drop_rate=do_row.get("do_drop_rate_mg_per_l_per_min"),
        min_do=do_row.get("min_do"),
    )
    thresholds = Thresholds.from_settings()

    finding = evaluate(snapshot, thresholds)
    if finding:
        evidence = IncidentEvidence(
            do_drop_rate_mg_per_l_per_min=snapshot.do_drop_rate,
            avg_pump_power_watts=snapshot.avg_pump_watts,
            min_do=snapshot.min_do,
            last_orp_mv=last_orp,
            nh3_mg_per_l=derived.nh3,
            ph_swing_24h=derived.ph_swing,
            do_window_sec=settings.DO_WINDOW_SEC,
            notes={"timestamp": now.isoformat()},
        )
        incident_service.open_or_update(pond_id, finding, evidence)
    elif has_recovered(snapshot, thresholds):
        incident_service.auto_recover(pond_id)
