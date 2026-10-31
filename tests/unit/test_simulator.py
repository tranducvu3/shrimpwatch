from datetime import datetime, timedelta

import pytest

from app.core.clock import utcnow
from app.core.config import settings
from app.domain.models import DeviceType, MetricType
from app.domain.scenarios import ScenarioType
from app.simulator.pond_model import PondSimulator
from app.simulator.runner import BACKFILL_HOURS, backfill_start, warm_up


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


def _values(records):
    return {r.metric.value: r.value for r in records}


def test_offsets_shift_temperature_ph_and_salinity():
    t = utcnow()
    base = _values(PondSimulator("pond-02").step(t, pump_failed=False))
    offsets = {ScenarioType.TEMPERATURE: 4.0, ScenarioType.PH: 0.8, ScenarioType.SALINITY: -7.0}
    shifted = _values(PondSimulator("pond-02").step(t, pump_failed=False, offsets=offsets))
    assert shifted["temperature"] == pytest.approx(base["temperature"] + 4.0, abs=0.01)
    assert shifted["ph"] == pytest.approx(base["ph"] + 0.8, abs=0.01)
    assert shifted["salinity"] == pytest.approx(base["salinity"] - 7.0, abs=0.01)


def test_do_offset_pulls_do_down_while_pump_runs():
    # Cung seed, cung moc thoi gian: chi khac do lech DO -> so sanh khong phu thuoc gio trong ngay
    normal, low = PondSimulator("pond-04"), PondSimulator("pond-04")
    t = utcnow()
    for i in range(61):  # 30 phut, hang so thoi gian ~10 phut
        ts = t + timedelta(seconds=30 * i)
        base = _values(normal.step(ts, pump_failed=False))
        last = _values(low.step(ts, pump_failed=False, offsets={ScenarioType.DO: -3.5}))
    assert last["do"] < base["do"] - 2.8, f"DO should follow lowered baseline: {base['do']} vs {last['do']}"
    assert last["orp"] < base["orp"], "ORP should follow DO"
    assert last["pump_power"] > 1000


@pytest.mark.parametrize("pond_id", [f"pond-0{i}" for i in range(1, 7)])
def test_healthy_pond_ph_swing_stays_below_warning(pond_id):
    # 24h, 30 giay/lan nhu simulator: ao khoe khong duoc kich hoat canh bao bien do pH
    sim = PondSimulator(pond_id)
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    ph = [
        next(
            r.value for r in sim.step(start + timedelta(seconds=30 * i), pump_failed=False) if r.metric == MetricType.PH
        )
        for i in range(2880)
    ]
    assert max(ph) - min(ph) < settings.PH_SWING_WARN
    assert max(ph) < settings.PH_WARN_HIGH


def test_backfill_start_without_data_covers_full_window():
    now = utcnow()
    assert backfill_start(now, None) == now - timedelta(hours=BACKFILL_HOURS)


def test_backfill_start_only_fills_gap_after_latest_reading():
    now = utcnow()
    latest = now - timedelta(minutes=3)
    assert backfill_start(now, latest) == latest + timedelta(seconds=settings.SIMULATOR_INTERVAL_SEC)


def test_backfill_start_caps_at_window_for_old_data():
    now = utcnow()
    assert backfill_start(now, now - timedelta(days=2)) == now - timedelta(hours=BACKFILL_HOURS)


def test_warm_up_sends_only_gap_but_warms_model_state():
    now = utcnow()
    warmed = {"pond-01": PondSimulator("pond-01", seed=0)}
    records = warm_up(warmed, now, send_from=now - timedelta(minutes=2))
    # Chi gui ~2 phut cuoi (5 buoc x so chi so), khong gui lai 6h
    assert 0 < len(records) <= 5 * len(PondSimulator("pond-01").step(now, pump_failed=False))
    assert all(datetime.fromisoformat(r["timestamp"]) >= now - timedelta(minutes=2) for r in records)

    # Mo hinh da chay qua 6h nen buoc ke tiep lien mach voi chuoi cu (khong nhay ve muc DO ban dau)
    cold = PondSimulator("pond-01", seed=0)
    warm_up({"pond-01": cold}, now, send_from=None)
    assert cold._do_last == warmed["pond-01"]._do_last
    assert cold._do_last_ts == now
