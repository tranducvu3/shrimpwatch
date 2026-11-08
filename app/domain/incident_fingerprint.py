"""Incident fingerprint: a numeric vector built from the water-quality evidence at detection time.

No embedding model needed: each dimension is one metric from IncidentEvidence, normalized to [0, 1]
over a fixed range. Two incidents with similar water behavior -> nearby vectors -> Vector Search
finds the similar past incident and how it was handled.
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
    # Healthy-pond value, used when data is missing
    healthy: float

    def normalize(self, value: float | None) -> float:
        v = self.healthy if value is None else value
        scaled = (v - self.low) / (self.high - self.low)
        return round(min(1.0, max(0.0, scaled)), 4)


# Fixed dimension order: changing the order/number of dimensions means rebuilding the vector index and old fingerprints.
FEATURES: tuple[Feature, ...] = (
    # Positive = DO falling (mg/L/min); failed pump ~0.1
    Feature("do_drop_rate_mg_per_l_per_min", -0.1, 0.2, 0.0),
    Feature("avg_pump_power_watts", 0.0, 1600.0, 1500.0),
    Feature("min_do", 0.0, 8.0, 5.8),
    Feature("last_orp_mv", 0.0, 400.0, 280.0),
    Feature("nh3_mg_per_l", 0.0, 0.5, 0.02),
    Feature("ph_swing_24h", 0.0, 1.5, 0.3),
)

DIMENSIONS = len(FEATURES)


def fingerprint(evidence: IncidentEvidence) -> list[float]:
    """Vector of DIMENSIONS dimensions, each in [0, 1]."""
    return [f.normalize(getattr(evidence, f.field)) for f in FEATURES]


def similarity(a: list[float], b: list[float]) -> float:
    """Similarity score matching Atlas Vector Search with similarity 'euclidean': 1 / (1 + d^2)."""
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
