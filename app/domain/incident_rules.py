"""Quy tac phat hien su co cho 1 ao. Ham thuan: nhan snapshot, tra ve ket luan; khong doc/ghi DB."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.domain.models import Severity


@dataclass(frozen=True)
class PondSnapshot:
    pond_id: str
    avg_pump_watts: float | None
    do_drop_rate: float | None  # mg/L/phut, duong khi DO giam
    min_do: float | None


@dataclass(frozen=True)
class Thresholds:
    do_warn: float
    do_critical: float
    do_drop_rate_warn: float
    pump_failed_max_watts: float
    pump_running_min_watts: float
    do_recovery_margin: float

    @classmethod
    def from_settings(cls) -> Thresholds:
        return cls(
            do_warn=settings.DO_WARN,
            do_critical=settings.DO_CRITICAL,
            do_drop_rate_warn=settings.DO_DROP_RATE_WARN_MGPERL_PER_MIN,
            pump_failed_max_watts=settings.PUMP_FAILED_MAX_WATTS,
            pump_running_min_watts=settings.PUMP_RUNNING_MIN_WATTS,
            do_recovery_margin=settings.DO_RECOVERY_MARGIN,
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
    """Tra ve su co can mo/cap nhat, theo thu tu uu tien; None neu ao binh thuong."""
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
            "do_anomaly_drop",
        )
    if s.min_do is not None and s.min_do < t.do_critical:
        return Finding(
            Severity.CRITICAL,
            "CRITICAL: DO dưới ngưỡng nguy hiểm",
            f"Min DO = {s.min_do:.3f} mg/L < {t.do_critical}",
            "do_critical",
        )
    if s.min_do is not None and s.min_do < t.do_warn:
        return Finding(
            Severity.WARNING,
            "WARNING: DO dưới ngưỡng cảnh báo",
            f"Min DO = {s.min_do:.3f} mg/L < {t.do_warn}",
            "do_low",
        )
    return None


def has_recovered(s: PondSnapshot, t: Thresholds) -> bool:
    """DO da len an toan va quat chay lai (hoac khong co so lieu quat)."""
    return (
        s.min_do is not None
        and s.min_do > t.do_warn + t.do_recovery_margin
        and (s.avg_pump_watts is None or s.avg_pump_watts > t.pump_running_min_watts)
    )
