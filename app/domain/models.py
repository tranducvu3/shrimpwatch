"""Mo hinh nghiep vu dung chung cho API, worker va simulator."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.core.clock import utcnow


class Source(str, Enum):
    SENSOR = "sensor"
    MANUAL = "manual"
    DERIVED = "derived"


class DeviceType(str, Enum):
    WATER_NODE = "water_node"
    PUMP = "pump"
    MANUAL = "manual_tester"
    WORKER = "worker"


class MetricType(str, Enum):
    TEMPERATURE = "temperature"
    DO = "do"
    PH = "ph"
    ALKALINITY = "alkalinity"
    CO2 = "co2"
    PUMP_POWER = "pump_power"
    PUMP_STATUS = "pump_status"
    SALINITY = "salinity"
    ORP = "orp"
    TAN = "tan"
    NITRITE = "nitrite"
    NH3 = "nh3"
    PH_SWING = "ph_swing"


MANUAL_METRIC_UNITS: dict[MetricType, str] = {
    MetricType.ALKALINITY: "mg_CaCO3/L",
    MetricType.TAN: "mg/L",
    MetricType.NITRITE: "mg/L",
}


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentState(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVING = "resolving"
    RECOVERED = "recovered"


class MetricRecord(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    farm_id: str
    pond_id: str
    device_id: str
    device_type: DeviceType
    metric: MetricType
    value: float
    source: Source
    unit: str | None = None


class IncidentEvidence(BaseModel):
    do_drop_rate_mg_per_l_per_min: float | None = None
    avg_pump_power_watts: float | None = None
    min_do: float | None = None
    last_orp_mv: float | None = None
    nh3_mg_per_l: float | None = None
    ph_swing_24h: float | None = None
    do_window_sec: int = 300
    notes: dict | None = None


class Incident(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    farm_id: str
    pond_id: str
    severity: Severity
    state: IncidentState = IncidentState.OPEN
    title: str
    description: str
    root_cause: str | None = None
    evidence: IncidentEvidence = IncidentEvidence()
    actions: list[dict] = []
