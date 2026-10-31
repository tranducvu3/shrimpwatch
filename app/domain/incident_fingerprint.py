"""Dau van tay (fingerprint) cua su co: vector so tu bang chung chat luong nuoc luc phat hien.

Khong can mo hinh embedding: moi chieu la 1 chi so trong IncidentEvidence, chuan hoa ve [0, 1]
theo khoang co dinh. Hai su co co dien bien nuoc giong nhau -> vector gan nhau -> Vector Search
tim ra su co cu tuong tu va cach da xu ly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.models import IncidentEvidence


@dataclass(frozen=True)
class Feature:
    field: str
    low: float
    high: float
    # Gia tri ao khoe manh, dung khi thieu so lieu
    healthy: float

    def normalize(self, value: float | None) -> float:
        v = self.healthy if value is None else value
        scaled = (v - self.low) / (self.high - self.low)
        return round(min(1.0, max(0.0, scaled)), 4)


# Thu tu chieu co dinh: doi thu tu/so chieu = phai tao lai vector index va fingerprint cu.
FEATURES: tuple[Feature, ...] = (
    # Duong = DO dang giam (mg/L/phut); quat hong ~0.1
    Feature("do_drop_rate_mg_per_l_per_min", -0.1, 0.2, 0.0),
    Feature("avg_pump_power_watts", 0.0, 1600.0, 1500.0),
    Feature("min_do", 0.0, 8.0, 5.8),
    Feature("last_orp_mv", 0.0, 400.0, 280.0),
    Feature("nh3_mg_per_l", 0.0, 0.5, 0.02),
    Feature("ph_swing_24h", 0.0, 1.5, 0.3),
)

DIMENSIONS = len(FEATURES)


def fingerprint(evidence: IncidentEvidence) -> list[float]:
    """Vector DIMENSIONS chieu, moi chieu trong [0, 1]."""
    return [f.normalize(getattr(evidence, f.field)) for f in FEATURES]


def similarity(a: list[float], b: list[float]) -> float:
    """Diem tuong dong giong Atlas Vector Search voi similarity 'euclidean': 1 / (1 + d^2)."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} != {len(b)}")
    d2 = sum((x - y) ** 2 for x, y in zip(a, b, strict=True))
    return 1.0 / (1.0 + d2)


def is_valid(vector: object) -> bool:
    return (
        isinstance(vector, list)
        and len(vector) == DIMENSIONS
        and all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector)
    )
