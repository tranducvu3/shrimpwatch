from app.domain.conditions import REMEDIES, Remedy
from app.domain.remedies import _BY_ROOT_CAUSE, suggested_remedies


def test_aerator_failure_suggests_repairing_it():
    assert suggested_remedies("pump_failure") == [Remedy.REPAIR_AERATOR]


def test_waste_causes_suggest_cutting_feed_first():
    for cause in ("nh3_out_of_range", "tan_out_of_range", "nitrite_out_of_range"):
        assert suggested_remedies(cause)[0] == Remedy.CUT_FEED


def test_causes_without_a_matching_remedy_suggest_nothing():
    assert suggested_remedies("salinity_out_of_range") == []
    assert suggested_remedies(None) == []


def test_every_suggested_remedy_exists():
    assert {r for remedies in _BY_ROOT_CAUSE.values() for r in remedies} <= set(REMEDIES)
