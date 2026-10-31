import pytest

from app.domain.water_chemistry import co2_mg_per_l, nh3_fraction, nh3_mg_per_l


def test_co2_matches_reference_value():
    # pH 7.9, do kiem 170 mg CaCO3/L -> ~4.2 mg/L
    assert co2_mg_per_l(170.0, 7.9) == pytest.approx(4.2, abs=0.1)


def test_co2_rises_as_ph_falls():
    assert co2_mg_per_l(150.0, 7.5) > co2_mg_per_l(150.0, 8.0)


def test_nh3_fraction_at_ph8_30c():
    # Emerson 1975: ~7.5% TAN o dang NH3 o pH 8, 30 do C
    assert nh3_fraction(8.0, 30.0) == pytest.approx(0.075, abs=0.01)


def test_nh3_rises_with_ph_and_temperature():
    assert nh3_fraction(8.5, 30.0) > nh3_fraction(8.0, 30.0)
    assert nh3_fraction(8.0, 32.0) > nh3_fraction(8.0, 26.0)


def test_nh3_mg_per_l_scales_with_tan():
    assert nh3_mg_per_l(2.0, 8.0, 30.0) == pytest.approx(2 * nh3_mg_per_l(1.0, 8.0, 30.0), abs=1e-4)
