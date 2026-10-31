"""Mo hinh vat ly 1 ao: nhiet do/pH/DO theo chu ky ngay dem, DO + ORP sut khi quat hong."""

from __future__ import annotations

import math
import random
from datetime import datetime

from app.core.clock import utcnow
from app.core.config import settings
from app.domain.models import DeviceType, MetricRecord, MetricType, Source
from app.domain.scenarios import ScenarioType


def _hour_fraction(dt: datetime) -> float:
    return (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0


def diurnal_amplitude(hour_frac: float, phase_offset_hours: float = 0.0) -> float:
    phase = (hour_frac * 24 - phase_offset_hours) / 24 * 2 * math.pi
    return math.sin(phase - math.pi / 2) * 0.5 + 0.5


class PondSimulator:
    def __init__(self, pond_id: str, seed: int = 42):
        self.pond_id = pond_id
        self.node_id = f"node-{pond_id}"
        self.pump_id = f"pump-{pond_id}"
        self._rng = random.Random(seed + sum(ord(c) for c in pond_id))
        self._temp_base = 28.5
        self._do_base = 5.8
        # pH dinh ngay = base + 0.30 = 8.35: con 0.15 duoi nguong PH_WARN_HIGH, khong bao dong gia moi dem
        self._ph_base = 8.05
        self._do_last = self._do_base
        self._do_last_ts = utcnow()
        self._orp_last = 280.0
        self._salinity_last = 15.0 + self._rng.uniform(-1.0, 1.0)

    def step(
        self, now: datetime, pump_failed: bool, offsets: dict[ScenarioType, float] | None = None
    ) -> list[MetricRecord]:
        offsets = offsets or {}
        hf = _hour_fraction(now)
        temp_amp = diurnal_amplitude(hf, phase_offset_hours=0)
        temp = self._temp_base + temp_amp * 3.0 + offsets.get(ScenarioType.TEMPERATURE, 0.0) + self._rng.gauss(0, 0.15)

        ph_amp = diurnal_amplitude(hf, phase_offset_hours=12)
        # Bien do ngay 0.30 + nhieu cam bien nho: bien do pH 24h cua ao khoe < PH_SWING_WARN (0.5), khong canh bao gia
        ph = self._ph_base + ph_amp * 0.30 + offsets.get(ScenarioType.PH, 0.0) + self._rng.gauss(0, 0.02)

        # Kich ban DO doi baseline: DO bi keo dan ve muc moi (hang so thoi gian ~10 phut), khong nhay tuc thi
        do_amp = diurnal_amplitude(hf, phase_offset_hours=18)
        do_baseline = self._do_base + do_amp * 0.8 + offsets.get(ScenarioType.DO, 0.0)

        delta_min = max(0.1, (now - self._do_last_ts).total_seconds() / 60.0)
        if pump_failed:
            # Khong co quat: tom + vi sinh tieu thu oxy, DO giam deu, khong keo ve baseline
            drift = 0.0
            aeration = -0.1 + self._rng.gauss(0, 0.005)
        else:
            drift = (do_baseline - self._do_last) * min(1.0, delta_min / 10.0)
            aeration = +0.04 + self._rng.gauss(0, 0.005)
        do = max(0.5, min(10.0, self._do_last + drift + aeration * delta_min + self._rng.gauss(0, 0.03)))
        self._do_last = do
        self._do_last_ts = now

        # ORP giam theo DO: day ao thieu oxy -> phan huy ky khi, nguy co H2S
        orp_target = 60.0 * (do - 3.0) + 120.0
        self._orp_last += (orp_target - self._orp_last) * min(1.0, delta_min / 5.0)
        orp = self._orp_last + self._rng.gauss(0, 4)

        self._salinity_last += self._rng.gauss(0, 0.02)
        salinity = self._salinity_last + offsets.get(ScenarioType.SALINITY, 0.0)

        pump_power = 0.0 if pump_failed else (1500.0 + self._rng.gauss(0, 50))
        pump_status = 0.0 if pump_failed else 1.0

        node = (self.node_id, DeviceType.WATER_NODE)
        pump = (self.pump_id, DeviceType.PUMP)
        readings = [
            (node, MetricType.TEMPERATURE, round(temp, 3), "C"),
            (node, MetricType.DO, round(do, 3), "mg/L"),
            (node, MetricType.PH, round(ph, 3), "pH"),
            (node, MetricType.SALINITY, round(salinity, 2), "ppt"),
            (node, MetricType.ORP, round(orp, 1), "mV"),
            (pump, MetricType.PUMP_POWER, round(pump_power, 1), "W"),
            (pump, MetricType.PUMP_STATUS, pump_status, "bool"),
        ]
        return [
            MetricRecord(
                timestamp=now,
                farm_id=settings.FARM_ID,
                pond_id=self.pond_id,
                device_id=device_id,
                device_type=device_type,
                metric=metric,
                value=value,
                source=Source.SENSOR,
                unit=unit,
            )
            for (device_id, device_type), metric, value, unit in readings
        ]
