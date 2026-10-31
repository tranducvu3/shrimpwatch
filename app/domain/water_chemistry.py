"""Cong thuc hoa hoc nuoc, ham thuan (khong doc DB) de test doc lap."""


def co2_mg_per_l(alkalinity_mg_caco3: float, ph: float) -> float:
    """CO2 hoa tan tu do kiem (mg CaCO3/L) va pH."""
    return round(44000.0 * (alkalinity_mg_caco3 / 50000.0) * (10 ** (6.35 - ph)), 3)


def nh3_fraction(ph: float, temp_c: float) -> float:
    """Ty le TAN o dang NH3 doc. Emerson et al. (1975): pKa = 0.09018 + 2729.92 / T(K)."""
    pka = 0.09018 + 2729.92 / (temp_c + 273.15)
    return 1.0 / (1.0 + 10 ** (pka - ph))


def nh3_mg_per_l(tan: float, ph: float, temp_c: float) -> float:
    return round(tan * nh3_fraction(ph, temp_c), 4)
