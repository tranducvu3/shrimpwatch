"""Body cua request HTTP. Mo hinh nghiep vu nam o app.domain.models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.clock import utcnow
from app.domain.models import MetricRecord

MAX_BATCH = 1000


class BatchIngestRequest(BaseModel):
    records: list[MetricRecord]

    @field_validator("records")
    @classmethod
    def not_empty(cls, v: list[MetricRecord]) -> list[MetricRecord]:
        if not v:
            raise ValueError("records cannot be empty")
        if len(v) > MAX_BATCH:
            raise ValueError(f"too many records (max {MAX_BATCH})")
        return v


class ManualInput(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    farm_id: str
    pond_id: str
    value: float
    tester_id: str = "tech-001"
