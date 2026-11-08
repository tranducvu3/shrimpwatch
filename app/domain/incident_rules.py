"""Incident detection rules for 1 pond. Pure functions: take a snapshot, return a finding; no DB reads/writes."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.domain.models import Severity


@dataclass(frozen=True)
class PondSnapshot:
    pond_id: str
    avg_pump_watts: float | None
    do_drop_rate: float | None  # mg/L/min, positive when DO falls
    min_do: float | None
    # 5-minute average (not a single point, to avoid sensor noise); None = no data
    avg_temperature: float | None = None
    avg_ph: float | None = None
    avg_salinity: float | None = None
    # Latest ORP, derived NH3/CO2 and test-kit (manual) readings; None = no fresh value
    last_orp: float | None = None
    nh3: float | None = None
    co2: float | None = None
    tan: float | None = None
    nitrite: float | None = None
    alkalinity: float | None = None


@dataclass(frozen=True)
class Thresholds:
    do_warn: float
    do_critical: float
    do_drop_rate_warn: float
    pump_failed_max_watts: float
    pump_running_min_watts: float
    do_recovery_margin: float
    temp_warn_low: float
    temp_warn_high: float
    ph_warn_low: float
    ph_warn_high: float
    salinity_warn_low: float
    orp_warn_low: float
    nh3_warn: float
    co2_warn: float
    tan_warn: float
    nitrite_warn: float
    alkalinity_warn_low: float

    @classmethod
    def from_settings(cls) -> Thresholds:
        return cls(
            do_warn=settings.DO_WARN,
            do_critical=settings.DO_CRITICAL,
            do_drop_rate_warn=settings.DO_DROP_RATE_WARN_MGPERL_PER_MIN,
            pump_failed_max_watts=settings.PUMP_FAILED_MAX_WATTS,
            pump_running_min_watts=settings.PUMP_RUNNING_MIN_WATTS,
            do_recovery_margin=settings.DO_RECOVERY_MARGIN,
            temp_warn_low=settings.TEMP_WARN_LOW,
            temp_warn_high=settings.TEMP_WARN_HIGH,
            ph_warn_low=settings.PH_WARN_LOW,
            ph_warn_high=settings.PH_WARN_HIGH,
            salinity_warn_low=settings.SALINITY_WARN_LOW,
            orp_warn_low=settings.ORP_WARN_LOW,
            nh3_warn=settings.NH3_WARN,
            co2_warn=settings.CO2_WARN,
            tan_warn=settings.TAN_WARN,
            nitrite_warn=settings.NITRITE_WARN,
            alkalinity_warn_low=settings.ALK_WARN,
        )


@dataclass(frozen=True)
class Finding:
    severity: Severity
    title: str
    description: str
    root_cause: str


def pump_failed(s: PondSnapshot, t: Thresholds) -> bool:
    return s.avg_pump_watts is not None and s.avg_pump_watts < t.pump_failed_max_watts


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def evaluate(s: PondSnapshot, t: Thresholds) -> Finding | None:
    """Returns the incident to open/update, in priority order; None if the pond is normal."""
    failed = pump_failed(s, t)

    if failed and (s.do_drop_rate is None or s.do_drop_rate >= 0):
        return Finding(
            Severity.CRITICAL,
            "SỰ CỐ: Quạt nước hỏng → oxy sụt",
            f"Công suất quạt ao {s.pond_id} trung bình 5 phút gần nhất: {s.avg_pump_watts:.1f} W. "
            f"DO drop rate: {_fmt(s.do_drop_rate, '.4f')} mg/L/phút, min DO: {s.min_do}",
            "pump_failure",
        )
    if s.do_drop_rate is not None and s.do_drop_rate >= t.do_drop_rate_warn and not failed:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: Oxy giảm nhanh bất thường",
            f"Tốc độ giảm DO {s.do_drop_rate:.3f} mg/L/phút (> ngưỡng {t.do_drop_rate_warn}). Cần điều tra.",
            "do_out_of_range",
        )
    if s.min_do is not None and s.min_do < t.do_critical:
        return Finding(
            Severity.CRITICAL,
            "CRITICAL: DO dưới ngưỡng nguy hiểm",
            f"Min DO = {s.min_do:.3f} mg/L < {t.do_critical}",
            "do_out_of_range",
        )
    if s.min_do is not None and s.min_do < t.do_warn:
        return Finding(
            Severity.WARNING,
            "WARNING: DO dưới ngưỡng cảnh báo",
            f"Min DO = {s.min_do:.3f} mg/L < {t.do_warn}",
            "do_out_of_range",
        )
    return _environment_finding(s, t) or _water_quality_finding(s, t)


def _outside(value: float | None, low: float, high: float) -> bool:
    return value is not None and not low <= value <= high


def _environment_finding(s: PondSnapshot, t: Thresholds) -> Finding | None:
    """Temperature, pH, salinity out of range: warning, checked after the oxygen rules (which are more dangerous)."""
    if _outside(s.avg_temperature, t.temp_warn_low, t.temp_warn_high):
        return Finding(
            Severity.WARNING,
            "Cảnh báo: Nhiệt độ nước ngoài ngưỡng",
            f"Nhiệt độ trung bình 5 phút = {s.avg_temperature:.2f} °C, ngoài ngưỡng "
            f"{t.temp_warn_low}–{t.temp_warn_high} °C.",
            "temperature_out_of_range",
        )
    if _outside(s.avg_ph, t.ph_warn_low, t.ph_warn_high):
        return Finding(
            Severity.WARNING,
            "Cảnh báo: pH ngoài ngưỡng",
            f"pH trung bình 5 phút = {s.avg_ph:.2f}, ngoài ngưỡng {t.ph_warn_low}–{t.ph_warn_high}.",
            "ph_out_of_range",
        )
    if s.avg_salinity is not None and s.avg_salinity < t.salinity_warn_low:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: Độ mặn thấp",
            f"Độ mặn trung bình 5 phút = {s.avg_salinity:.2f} ppt < {t.salinity_warn_low} ppt.",
            "salinity_out_of_range",
        )
    return None


def _water_quality_finding(s: PondSnapshot, t: Thresholds) -> Finding | None:
    """Toxic gases, nitrogen waste, bottom oxygen and buffer: warning, checked after oxygen and environment rules.
    pH swing is not a rule: it is above its threshold on a normal simulated day, so it would open an incident always."""
    if s.nh3 is not None and s.nh3 > t.nh3_warn:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: NH₃ độc cao",
            f"NH₃ = {s.nh3:.3f} mg/L > ngưỡng {t.nh3_warn} mg/L (tính từ TAN, pH, nhiệt độ).",
            "nh3_out_of_range",
        )
    if s.nitrite is not None and s.nitrite > t.nitrite_warn:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: Nitrite (NO₂) cao",
            f"Nitrite = {s.nitrite:.2f} mg/L > ngưỡng {t.nitrite_warn} mg/L (đo tay gần nhất).",
            "nitrite_out_of_range",
        )
    if s.tan is not None and s.tan > t.tan_warn:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: Amoni tổng (TAN) cao",
            f"TAN = {s.tan:.2f} mg/L > ngưỡng {t.tan_warn} mg/L (đo tay gần nhất).",
            "tan_out_of_range",
        )
    if s.co2 is not None and s.co2 > t.co2_warn:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: CO₂ cao",
            f"CO₂ = {s.co2:.2f} mg/L > ngưỡng {t.co2_warn} mg/L (tính từ pH và độ kiềm).",
            "co2_out_of_range",
        )
    if s.last_orp is not None and s.last_orp < t.orp_warn_low:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: ORP thấp, đáy ao thiếu oxy",
            f"ORP = {s.last_orp:.0f} mV < ngưỡng {t.orp_warn_low:.0f} mV, nguy cơ H₂S.",
            "orp_out_of_range",
        )
    if s.alkalinity is not None and s.alkalinity < t.alkalinity_warn_low:
        return Finding(
            Severity.WARNING,
            "Cảnh báo: Độ kiềm thấp",
            f"Độ kiềm = {s.alkalinity:.0f} mg CaCO₃/L < ngưỡng {t.alkalinity_warn_low:.0f} (đo tay gần nhất).",
            "alkalinity_out_of_range",
        )
    return None


def has_recovered(s: PondSnapshot, t: Thresholds) -> bool:
    """DO is back to a safe level and the pump is running again (or there is no pump data)."""
    return (
        s.min_do is not None
        and s.min_do > t.do_warn + t.do_recovery_margin
        and (s.avg_pump_watts is None or s.avg_pump_watts > t.pump_running_min_watts)
    )
