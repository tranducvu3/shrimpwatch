from datetime import datetime, timedelta

from app.domain.system_faults import FAULTS, FaultType, is_active

NOW = datetime(2026, 11, 11, 12, 0, 0)


def test_a_fault_is_on_until_its_time():
    assert is_active({"until": NOW + timedelta(seconds=1)}, NOW)
    assert not is_active({"until": NOW}, NOW)
    assert not is_active({"until": "soon"}, NOW)
    assert not is_active(None, NOW)


def test_every_fault_says_what_breaks_and_where_datadog_shows_it():
    assert set(FAULTS) == set(FaultType)
    assert all(f.label and f.description and f.watch and 0 < f.duration_min <= 30 for f in FAULTS.values())
