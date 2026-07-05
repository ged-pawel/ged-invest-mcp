"""Fresh concrete pressure on vertical formwork per DIN 18218:2010-01.

The maximum characteristic lateral pressure rhk,max [kN/m2] for placement from
the top (reference: concrete temperature 15 C, specific weight 25 kN/m3):

    F1:  (5*v  + 21) * K1 * K2
    F2:  (10*v + 19) * K1 * K2
    F3:  (14*v + 18) * K1 * K2
    F4:  (17*v + 17) * K1 * K2
    F5:  max(25 + 30*v, 30) * K1 * K2
    F6:  max(25 + 38*v, 30) * K1 * K2
    SCC: max(25 + 33*v, 30) * K1 * K2

with v = mean placing (rise) rate [m/h], and always capped by the hydrostatic
pressure gamma_c * H.

K1 (stiffening / setting, valid 5 h <= tE <= 20 h):
    F1: 1 + 0.030*(tE-5)   F2: 1 + 0.053*(tE-5)
    F3: 1 + 0.077*(tE-5)   F4: 1 + 0.140*(tE-5)
    F5/F6/SCC: tE / 5.0

K2 = gamma_c / 25.

IMPORTANT: This is the simplified DIN 18218 model at the 15 C reference. It does
NOT apply the temperature/admixture correction factors. The final determination
of the real fresh concrete pressure is the site manager's responsibility (also
stated in the BAUTEKK DTR). Treat the result as advisory.
"""

from __future__ import annotations

import math

# (slope on v, intercept) for the characteristic pressure formula
_CLASS_TERMS = {
    "F1": (5.0, 21.0),
    "F2": (10.0, 19.0),
    "F3": (14.0, 18.0),
    "F4": (17.0, 17.0),
    "F5": (30.0, 25.0),
    "F6": (38.0, 25.0),
    "SCC": (33.0, 25.0),
}
_FLOWABLE = {"F5", "F6", "SCC"}
_FLOWABLE_MIN = 30.0  # absolute minimum pressure for highly workable concrete
_DEFAULT_UNIT_WEIGHT = 25.0
_ALIASES = {"SVB": "SCC"}


def _k1(consistency: str, setting_time_h: float) -> float:
    te = min(max(setting_time_h, 5.0), 20.0)
    if consistency in _FLOWABLE:
        return te / 5.0
    slope = {"F1": 0.030, "F2": 0.053, "F3": 0.077, "F4": 0.140}[consistency]
    return 1.0 + slope * (te - 5.0)


def _characteristic(consistency: str, v: float, k1: float, k2: float) -> float:
    slope, intercept = _CLASS_TERMS[consistency]
    base = slope * v + intercept
    if consistency in _FLOWABLE:
        base = max(base, _FLOWABLE_MIN)
    return base * k1 * k2


def normalize_class(consistency: str) -> str:
    c = consistency.strip().upper().replace(" ", "")
    c = _ALIASES.get(c, c)
    if c not in _CLASS_TERMS:
        raise ValueError(
            f"Unknown consistency class '{consistency}'. Valid: "
            f"{', '.join(_CLASS_TERMS)} (SVB is an alias for SCC)."
        )
    return c


def concrete_pressure(
    pouring_rate_m_per_h: float,
    wall_height_m: float,
    consistency_class: str = "F3",
    setting_time_h: float = 5.0,
    concrete_unit_weight_kn_m3: float = _DEFAULT_UNIT_WEIGHT,
    allowed_pressure_kn_m2: float | None = None,
    max_pouring_rate_m_per_h: float | None = None,
) -> dict:
    """Compute DIN 18218 fresh concrete pressure and check it against a limit.

    Args:
        pouring_rate_m_per_h: mean vertical rise rate v [m/h].
        wall_height_m: casting height H [m] (for the hydrostatic cap).
        consistency_class: F1..F6 or SCC (SVB alias).
        setting_time_h: final setting time tE [h], 5..20 (default 5).
        concrete_unit_weight_kn_m3: gamma_c (default 25).
        allowed_pressure_kn_m2: formwork system pressure limit to check against.
        max_pouring_rate_m_per_h: optional process cap (e.g. DTR BAUTEKK 2 m/h).
    """
    if not math.isfinite(pouring_rate_m_per_h) or pouring_rate_m_per_h <= 0:
        raise ValueError("pouring_rate_m_per_h must be a positive number.")
    if not math.isfinite(wall_height_m) or wall_height_m <= 0:
        raise ValueError("wall_height_m must be a positive number.")

    consistency = normalize_class(consistency_class)
    k1 = _k1(consistency, setting_time_h)
    k2 = concrete_unit_weight_kn_m3 / 25.0

    characteristic = _characteristic(consistency, pouring_rate_m_per_h, k1, k2)
    hydrostatic = concrete_unit_weight_kn_m3 * wall_height_m
    design_pressure = min(characteristic, hydrostatic)
    governing = "hydrostatic" if hydrostatic < characteristic else "characteristic (DIN 18218)"

    warnings: list[str] = []
    within_limit: bool | None = None
    max_rate_for_limit: float | None = None
    if allowed_pressure_kn_m2 is not None:
        within_limit = design_pressure <= allowed_pressure_kn_m2
        if not within_limit:
            warnings.append(
                f"Design pressure {round(design_pressure, 1)} kN/m2 EXCEEDS the allowed "
                f"{allowed_pressure_kn_m2} kN/m2. Reduce the pouring rate, use a stiffer/"
                f"colder mix, or a stronger formwork system."
            )
        # invert the formula to get the max pouring rate that stays within the limit
        slope, intercept = _CLASS_TERMS[consistency]
        target = allowed_pressure_kn_m2 / (k1 * k2)
        if hydrostatic <= allowed_pressure_kn_m2:
            max_rate_for_limit = None  # hydrostatic already within limit -> not rate-limited
        else:
            v_lim = (target - intercept) / slope
            max_rate_for_limit = round(v_lim, 2) if v_lim > 0 else 0.0

    if max_pouring_rate_m_per_h is not None and pouring_rate_m_per_h > max_pouring_rate_m_per_h:
        warnings.append(
            f"Pouring rate {pouring_rate_m_per_h} m/h exceeds the process limit "
            f"{max_pouring_rate_m_per_h} m/h."
        )

    return {
        "standard": "DIN 18218:2010-01 (simplified, 15 C reference)",
        "inputs": {
            "pouring_rate_m_per_h": pouring_rate_m_per_h,
            "wall_height_m": wall_height_m,
            "consistency_class": consistency,
            "setting_time_h": setting_time_h,
            "concrete_unit_weight_kn_m3": concrete_unit_weight_kn_m3,
        },
        "coefficients": {"K1": round(k1, 3), "K2": round(k2, 3)},
        "characteristic_pressure_kn_m2": round(characteristic, 1),
        "hydrostatic_pressure_kn_m2": round(hydrostatic, 1),
        "design_pressure_kn_m2": round(design_pressure, 1),
        "governing": governing,
        "allowed_pressure_kn_m2": allowed_pressure_kn_m2,
        "within_limit": within_limit,
        "max_pouring_rate_for_limit_m_per_h": max_rate_for_limit,
        "units": {"pressure": "kN/m2", "rate": "m/h", "height": "m", "setting_time": "h"},
        "warnings": warnings,
        "disclaimer": (
            "Simplified DIN 18218 estimate at the 15 C reference; temperature and "
            "admixture corrections are NOT applied. The real fresh concrete pressure "
            "and the safe pouring rate are the site manager's responsibility."
        ),
    }
