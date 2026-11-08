"""Water chemistry formulas as pure functions (no DB reads) so they can be tested on their own."""


def co2_mg_per_l(alkalinity_mg_caco3: float, ph: float) -> float:
    """Dissolved CO2 from alkalinity (mg CaCO3/L) and pH."""
    return round(44000.0 * (alkalinity_mg_caco3 / 50000.0) * (10 ** (6.35 - ph)), 3)


def nh3_fraction(ph: float, temp_c: float) -> float:
    """Fraction of TAN present as toxic NH3. Emerson et al. (1975): pKa = 0.09018 + 2729.92 / T(K)."""
    pka = 0.09018 + 2729.92 / (temp_c + 273.15)
    return 1.0 / (1.0 + 10 ** (pka - ph))


def nh3_mg_per_l(tan: float, ph: float, temp_c: float) -> float:
    return round(tan * nh3_fraction(ph, temp_c), 4)
