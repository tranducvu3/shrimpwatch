"""Chi so tinh toan (source=derived): CO2, NH3, bien do pH 24h. Ghi lai vao Time Series nhu telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import gauge, increment
from app.db.repositories import metrics as metrics_repo
from app.domain.models import DeviceType, Source
from app.domain.water_chemistry import co2_mg_per_l, nh3_mg_per_l

logger = get_logger("services.derivations")

SENSOR_FRESHNESS = timedelta(minutes=30)
MANUAL_FRESHNESS = timedelta(hours=24)


@dataclass(frozen=True)
class DerivedValues:
    co2: float | None
    nh3: float | None
    ph_swing: float | None


def derive_co2_for_pond(pond_id: str, timestamp: datetime) -> float | None:
    alk = metrics_repo.latest_value(pond_id, "alkalinity", timestamp - MANUAL_FRESHNESS, source=Source.MANUAL.value)
    ph = metrics_repo.latest_value(pond_id, "ph", timestamp - SENSOR_FRESHNESS, source=Source.SENSOR.value)
    if alk is None or ph is None:
        return None
    return co2_mg_per_l(alk, ph)


def derive_nh3_for_pond(pond_id: str, timestamp: datetime) -> float | None:
    tan = metrics_repo.latest_value(pond_id, "tan", timestamp - MANUAL_FRESHNESS)
    ph = metrics_repo.latest_value(pond_id, "ph", timestamp - SENSOR_FRESHNESS)
    temp = metrics_repo.latest_value(pond_id, "temperature", timestamp - SENSOR_FRESHNESS)
    if tan is None or ph is None or temp is None:
        return None
    return nh3_mg_per_l(tan, ph, temp)


def write_derived_record(pond_id: str, timestamp: datetime, metric: str, value: float, unit: str) -> None:
    metrics_repo.insert_one(
        metrics_repo.pack_ts_doc(
            {
                "timestamp": timestamp,
                "farm_id": settings.FARM_ID,
                "pond_id": pond_id,
                "device_id": f"worker-{metric}-{pond_id}",
                "device_type": DeviceType.WORKER.value,
                "metric": metric,
                "source": Source.DERIVED.value,
                "unit": unit,
                "value": value,
            }
        )
    )
    gauge(
        f"metric.{metric}",
        value,
        tags={"pond": pond_id, "device_type": DeviceType.WORKER.value, "source": Source.DERIVED.value},
    )
    increment(f"{metric}.derived", tags={"pond": pond_id})


def _record_if_present(
    pond_id: str, now: datetime, metric: str, value: float | None, unit: str, warn_above: float, label: str
) -> None:
    if value is None:
        return
    if value > warn_above:
        logger.warning(f"{label} warning", extra={"pond": pond_id, metric: value})
    write_derived_record(pond_id, now, metric, value, unit)


def derive_and_record(pond_id: str, now: datetime) -> DerivedValues:
    values = DerivedValues(
        co2=derive_co2_for_pond(pond_id, now),
        nh3=derive_nh3_for_pond(pond_id, now),
        ph_swing=metrics_repo.ph_swing(pond_id),
    )
    _record_if_present(pond_id, now, "co2", values.co2, "mg/L", settings.CO2_WARN, "CO2")
    _record_if_present(pond_id, now, "nh3", values.nh3, "mg/L", settings.NH3_WARN, "NH3")
    _record_if_present(pond_id, now, "ph_swing", values.ph_swing, "pH", settings.PH_SWING_WARN, "pH swing")
    return values
