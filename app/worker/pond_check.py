"""Check 1 pond: gather data -> apply rules (domain) -> open/update/close the incident (service)."""

from __future__ import annotations

from datetime import timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import gauge, set_span_tags
from app.db.repositories import metrics as metrics_repo
from app.domain.incident_rules import (
    PondSnapshot,
    Thresholds,
    evaluate,
    has_recovered,
    minutes_to_do_critical,
    pump_stopped,
)
from app.domain.models import DeviceType, IncidentEvidence, Source
from app.services import derivations
from app.services import incidents as incident_service

logger = get_logger("worker")

RECENT = timedelta(minutes=5)
# Forecasts further out are sensor noise on a flat DO curve: not worth a point on the chart
FORECAST_HORIZON_MIN = 240


def load_do_windows(pond_ids: list[str]) -> dict[str, dict]:
    rows = metrics_repo.do_windows(pond_ids, window_sec=settings.DO_WINDOW_SEC)
    for pond_id, row in rows.items():
        logger.info("DO window computed", extra={"pond": pond_id, "row": row})
    return rows


def _latest_manual(pond_id: str, metric: str, now):
    """Latest test-kit reading of the last 24 h (technicians test a few times a day)."""
    return metrics_repo.latest_value(pond_id, metric, now - derivations.MANUAL_FRESHNESS, source=Source.MANUAL.value)


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
        last_do=do_row.get("last_do"),
        avg_temperature=metrics_repo.average_value(pond_id, "temperature", now - RECENT),
        avg_ph=metrics_repo.average_value(pond_id, "ph", now - RECENT),
        avg_salinity=metrics_repo.average_value(pond_id, "salinity", now - RECENT),
        last_orp=last_orp,
        nh3=derived.nh3,
        co2=derived.co2,
        tan=_latest_manual(pond_id, "tan", now),
        nitrite=_latest_manual(pond_id, "nitrite", now),
        alkalinity=_latest_manual(pond_id, "alkalinity", now),
        pump_stopped=pump_stopped(
            metrics_repo.latest_values(pond_id, "pump_status", now - RECENT, settings.PUMP_STOPPED_READINGS),
            settings.PUMP_STOPPED_READINGS,
        ),
    )
    thresholds = Thresholds.from_settings()
    minutes_left = minutes_to_do_critical(snapshot.last_do, snapshot.do_drop_rate, thresholds.do_critical)
    if minutes_left is not None and minutes_left <= FORECAST_HORIZON_MIN:
        gauge("do.minutes_to_critical", round(minutes_left, 1), tags={"pond": pond_id})

    finding = evaluate(snapshot, thresholds)
    if finding:
        evidence = IncidentEvidence(
            do_drop_rate_mg_per_l_per_min=snapshot.do_drop_rate,
            avg_pump_power_watts=snapshot.avg_pump_watts,
            min_do=snapshot.min_do,
            last_orp_mv=last_orp,
            nh3_mg_per_l=derived.nh3,
            ph_swing_24h=derived.ph_swing,
            minutes_to_do_critical=minutes_left,
            do_window_sec=settings.DO_WINDOW_SEC,
            notes={
                "timestamp": now.isoformat(),
                "avg_temperature": snapshot.avg_temperature,
                "avg_ph": snapshot.avg_ph,
                "avg_salinity": snapshot.avg_salinity,
                "co2": snapshot.co2,
                "tan": snapshot.tan,
                "nitrite": snapshot.nitrite,
                "alkalinity": snapshot.alkalinity,
            },
        )
        incident_service.open_or_update(pond_id, finding, evidence)
    elif has_recovered(snapshot, thresholds):
        incident_service.auto_recover(pond_id)
        return
    # After a remedy: back to normal, or (a minute later) is the root-cause metric getting better?
    incident_service.check_remedy(pond_id, now, still_abnormal=finding is not None)
