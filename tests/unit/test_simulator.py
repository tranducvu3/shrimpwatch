from datetime import timedelta

from app.core.clock import utcnow
from app.domain.models import DeviceType, MetricType
from app.simulator.pond_model import PondSimulator


def test_step_emits_one_record_per_metric():
    records = PondSimulator("pond-01").step(utcnow(), pump_failed=False)
    assert [r.metric for r in records] == [
        MetricType.TEMPERATURE,
        MetricType.DO,
        MetricType.PH,
        MetricType.SALINITY,
        MetricType.ORP,
        MetricType.PUMP_POWER,
        MetricType.PUMP_STATUS,
    ]
    pump = [r for r in records if r.device_type == DeviceType.PUMP]
    assert {r.device_id for r in pump} == {"pump-pond-01"}


def test_simulator_do_and_orp_fall_when_pump_fails():
    sim = PondSimulator("pond-03")
    t = utcnow()
    first = {r.metric.value: r.value for r in sim.step(t, pump_failed=False)}
    for i in range(1, 21):
        last = {r.metric.value: r.value for r in sim.step(t + timedelta(seconds=30 * i), pump_failed=True)}
    assert last["do"] < first["do"] - 0.7, f"DO should fall without aerator: {first['do']} -> {last['do']}"
    assert last["orp"] < first["orp"], "ORP should fall with DO"
    assert last["pump_power"] == 0.0
    assert "salinity" in last
