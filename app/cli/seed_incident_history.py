"""Nap lich su su co da hoi phuc (du lieu tong hop) de Vector Search co su co cu de so khop.

Chay lai nhieu lan van an toan: id co dinh, su co da co thi bo qua.
    python -m app.cli.seed_incident_history
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.domain.models import Incident, IncidentEvidence, IncidentState, Severity
from app.domain.ponds import pond_ids
from app.services import incidents as incident_service

logger = get_logger("seed_incident_history")


@dataclass(frozen=True)
class Scenario:
    key: str
    root_cause: str
    severity: Severity
    title: str
    description: str
    # do_drop_rate, avg_pump_power, min_do, orp, nh3, ph_swing
    evidence: tuple[float, float, float, float, float, float]
    actions: tuple[tuple[IncidentState, str], ...]
    recovery_minutes: int
    variants: int = 3


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "pump",
        "pump_failure",
        Severity.CRITICAL,
        "SỰ CỐ: Quạt nước hỏng → oxy sụt",
        "Quạt mất điện, DO giảm ~0.1 mg/L/phút.",
        (0.10, 0.0, 3.2, 140.0, 0.05, 0.35),
        (
            (IncidentState.INVESTIGATING, "KT điện quạt, phát hiện cháy tụ"),
            (IncidentState.RESOLVING, "Thay tụ quạt, khởi động lại quạt"),
            (IncidentState.RECOVERED, "DO > 5 mg/L, ao ổn định"),
        ),
        25,
    ),
    Scenario(
        "outage",
        "pump_failure",
        Severity.CRITICAL,
        "SỰ CỐ: Mất điện ban đêm → quạt dừng",
        "Mất điện lưới lúc 2h sáng, DO thấp tự nhiên ban đêm.",
        (0.08, 0.0, 2.4, 105.0, 0.06, 0.30),
        (
            (IncidentState.INVESTIGATING, "Kiểm tra lưới điện, xác nhận mất điện khu vực"),
            (IncidentState.RESOLVING, "Chạy máy phát dự phòng, bật lại quạt"),
            (IncidentState.RECOVERED, "Có điện lưới, DO hồi phục"),
        ),
        50,
    ),
    Scenario(
        "algae",
        "do_anomaly_drop",
        Severity.WARNING,
        "Cảnh báo: Oxy giảm nhanh bất thường",
        "Tảo tàn: quạt chạy bình thường nhưng DO và ORP giảm, biên độ pH lớn.",
        (0.06, 1500.0, 3.8, 190.0, 0.12, 0.90),
        (
            (IncidentState.INVESTIGATING, "Nước chuyển màu, xác định tảo tàn"),
            (IncidentState.RESOLVING, "Thay 20% nước, bón vôi, giảm 30% thức ăn"),
            (IncidentState.RECOVERED, "Màu nước ổn định, DO bình thường"),
        ),
        180,
    ),
    Scenario(
        "ammonia",
        "do_low",
        Severity.WARNING,
        "WARNING: DO dưới ngưỡng cảnh báo",
        "Dư thức ăn: NH3 cao, DO thấp dần, quạt chạy bình thường.",
        (0.02, 1480.0, 3.7, 170.0, 0.25, 0.50),
        (
            (IncidentState.INVESTIGATING, "Test TAN cao, kiểm tra sàng ăn thấy dư thức ăn"),
            (IncidentState.RESOLVING, "Ngưng 1 bữa ăn, bổ sung vi sinh xử lý đáy"),
            (IncidentState.RECOVERED, "NH3 < 0.1 mg/L, DO ổn định"),
        ),
        240,
    ),
    Scenario(
        "rain",
        "do_low",
        Severity.WARNING,
        "WARNING: DO dưới ngưỡng cảnh báo",
        "Mưa lớn: phân tầng nước, pH dao động mạnh.",
        (0.03, 1500.0, 3.9, 220.0, 0.04, 1.10),
        (
            (IncidentState.INVESTIGATING, "Mưa lớn 2 giờ, đo pH tầng mặt và tầng đáy"),
            (IncidentState.RESOLVING, "Bón vôi CaCO3, tăng thời gian chạy quạt"),
            (IncidentState.RECOVERED, "pH và DO ổn định sau mưa"),
        ),
        120,
    ),
)


def _jitter(rng: random.Random, value: float, ratio: float = 0.1) -> float:
    return round(value * (1 + rng.uniform(-ratio, ratio)), 4)


def build_history(seed: int = 7) -> list[Incident]:
    rng = random.Random(seed)
    ponds = pond_ids()
    now = utcnow()
    history: list[Incident] = []
    for s_idx, scenario in enumerate(SCENARIOS):
        for v in range(scenario.variants):
            drop, pump, min_do, orp, nh3, swing = scenario.evidence
            created = now - timedelta(days=2 + s_idx * 3 + v * 5, hours=rng.randint(0, 23))
            recovery = timedelta(minutes=_jitter(rng, scenario.recovery_minutes, 0.2))
            steps = len(scenario.actions)
            actions = [
                {"time": created + recovery * (i + 1) / steps, "state": state.value, "action": action}
                for i, (state, action) in enumerate(scenario.actions)
            ]
            history.append(
                Incident(
                    id=f"hist-{scenario.key}-{v + 1:02d}",
                    created_at=created,
                    updated_at=created + recovery,
                    farm_id=settings.FARM_ID,
                    pond_id=ponds[(s_idx + v) % len(ponds)],
                    severity=scenario.severity,
                    state=IncidentState.RECOVERED,
                    title=scenario.title,
                    description=scenario.description,
                    root_cause=scenario.root_cause,
                    evidence=IncidentEvidence(
                        do_drop_rate_mg_per_l_per_min=_jitter(rng, drop),
                        avg_pump_power_watts=_jitter(rng, pump, 0.03),
                        min_do=_jitter(rng, min_do),
                        last_orp_mv=_jitter(rng, orp),
                        nh3_mg_per_l=_jitter(rng, nh3),
                        ph_swing_24h=_jitter(rng, swing),
                        notes={"synthetic": True, "scenario": scenario.key},
                    ),
                    actions=actions,
                )
            )
    return history


def main() -> int:
    history = build_history()
    imported = incident_service.import_history(history)
    logger.info(f"Seeded {imported} new incidents ({len(history)} in history)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
