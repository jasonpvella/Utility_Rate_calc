"""Synthetic Walmart / Sam's Club 8760-hour load profile generator.

Based on NREL ComStock "Standalone Retail" prototype scaled to Walmart
Supercenter (~150,000 sq ft) and Sam's Club (~135,000 sq ft) footprints,
parameterized by IECC climate zone.

Spec §9 targets:
  - Walmart Supercenter: ~6,500 MWh/year annual energy
  - Sam's Club:          ~5,700 MWh/year annual energy
  - Summer-peaking, mid-afternoon
  - Deterministic: numpy.random not used; profile is fully formula-driven

Profile model:
  hourly_kw[h] = base_kw + op_kw(h) + cool_kw(h, month, cz)

  base_kw   – always-on load (refrigeration, base lighting, security)
  op_kw     – operational load (variable lighting, fans, plug loads)
              shaped by hour-of-day operational curve
  cool_kw   – HVAC cooling load shaped by hour-of-day × monthly fraction
              × climate-zone multiplier

The raw profile is normalised to hit the target annual kWh exactly.

Usage:
    from voltregistry.load.synthetic import synthesize_load
    profile = synthesize_load("Walmart", climate_zone=4)  # shape (8760,)
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Target annual energies per brand (kWh)
_ANNUAL_KWH: dict[str, float] = {
    "Walmart": 6_500_000.0,
    "SamsClub": 5_700_000.0,
}

# Brand scaling factors relative to Walmart baseline (affects op + cool max kW)
_BRAND_SCALE: dict[str, float] = {
    "Walmart": 1.00,
    "SamsClub": 0.875,  # ~135k / 150k sq ft
}

# Hour-of-day operational shape (index 0 = midnight–1 am, max = 1.0 at hour 14)
# Covers: variable lighting, HVAC fans, plug loads, checkout, foot-traffic
_HOUR_OP: np.ndarray = np.array(
    [
        0.00, 0.00, 0.00, 0.00, 0.00, 0.08,  # 12 am – 5 am  (near-zero ops)
        0.48, 0.72, 0.84, 0.90, 0.93, 0.95,  # 6 am – 11 am  (opening ramp)
        0.97, 0.98, 1.00, 0.99, 0.98, 0.97,  # 12 pm – 5 pm  (peak ops)
        0.95, 0.92, 0.88, 0.80, 0.48, 0.08,  # 6 pm – 11 pm  (closing/clean)
    ],
    dtype=np.float64,
)

# Hour-of-day cooling shape (HVAC cooling load; max = 1.0 at hour 14)
_HOUR_COOL: np.ndarray = np.array(
    [
        0.00, 0.00, 0.00, 0.00, 0.00, 0.00,  # 12 am – 5 am
        0.05, 0.15, 0.35, 0.55, 0.70, 0.82,  # 6 am – 11 am
        0.90, 0.95, 1.00, 0.98, 0.95, 0.88,  # 12 pm – 5 pm
        0.75, 0.58, 0.42, 0.25, 0.08, 0.00,  # 6 pm – 11 pm
    ],
    dtype=np.float64,
)

# Monthly cooling fraction (fraction of peak cooling capacity active each month)
# Index 0 = January … 11 = December
_MONTH_COOL_FRAC: np.ndarray = np.array(
    [0.02, 0.02, 0.06, 0.15, 0.40, 0.80, 1.00, 0.96, 0.65, 0.26, 0.06, 0.02],
    dtype=np.float64,
)

# Climate-zone cooling multipliers (IECC zones 1–8; index 0 unused)
# Zone 1 = very hot/humid; Zone 8 = subarctic
_CZ_COOL_MULT: list[float] = [0.0, 1.35, 1.20, 1.08, 1.00, 0.88, 0.74, 0.60, 0.45]

# Weekend reduction factor on both operational and cooling load
_WEEKEND_FACTOR: float = 0.92

# Operational and cooling max kW for Walmart CZ-4 baseline (pre-normalisation)
# These are design constants; the profile is renormalised to hit annual kWh target.
_OP_MAX_KW: float = 580.0
_COOL_MAX_KW: float = 320.0
_BASE_KW: float = 400.0  # refrigeration + base lighting + always-on security

# Cumulative hours at start of each month (non-leap 2025, 365 × 24 = 8 760)
_MONTH_DAYS: list[int] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MONTH_START_HOUR: list[int] = [
    sum(_MONTH_DAYS[:m]) * 24 for m in range(12)
]  # [0, 744, 1416, 2160, ...]

# 2025-01-01 is a Wednesday; weekday 0=Mon … 6=Sun → Wednesday = 2
_JAN1_WEEKDAY: int = 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hour_month_weekday_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return three (8760,) integer arrays: month (1-12), weekday (0-6), hod (0-23).

    month    – calendar month, 1 = January
    weekday  – 0 = Monday … 6 = Sunday
    hod      – hour of day 0–23
    """
    hours = np.arange(8760, dtype=np.int32)
    day = hours // 24
    hod = hours % 24

    # Month array via cumulative day-count lookup
    month_arr = np.ones(8760, dtype=np.int32)
    for m_idx, start_day in enumerate(d // 24 for d in _MONTH_START_HOUR):
        month_arr[start_day * 24 :] = m_idx + 1

    weekday = (_JAN1_WEEKDAY + day) % 7  # 0=Mon … 6=Sun
    return month_arr, weekday, hod


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def synthesize_load(brand: str, climate_zone: int) -> np.ndarray:
    """Generate a deterministic 8760-hour load profile (kW) for a store.

    Args:
        brand:         "Walmart" or "SamsClub"
        climate_zone:  IECC climate zone 1–8

    Returns:
        np.ndarray of shape (8760,) with hourly kW values.
        Σ profile[h] == annual kWh target (since Δt = 1 hr).
        All values are ≥ 0; dtype = float64.

    Raises:
        ValueError: if brand or climate_zone are not recognised.
    """
    if brand not in _ANNUAL_KWH:
        raise ValueError(f"Unknown brand '{brand}'. Expected 'Walmart' or 'SamsClub'.")
    if not (1 <= climate_zone <= 8):
        raise ValueError(f"climate_zone must be 1–8, got {climate_zone}.")

    brand_scale = _BRAND_SCALE[brand]
    cz_cool_mult = _CZ_COOL_MULT[climate_zone]

    base_kw = _BASE_KW * brand_scale
    op_max_kw = _OP_MAX_KW * brand_scale
    cool_max_kw = _COOL_MAX_KW * brand_scale * cz_cool_mult

    month_arr, weekday, hod = _hour_month_weekday_arrays()

    # Operational load (shaped by hour-of-day)
    op_kw = op_max_kw * _HOUR_OP[hod]

    # Weekend reduction
    is_weekend = weekday >= 5  # Saturday=5, Sunday=6
    op_kw = np.where(is_weekend, op_kw * _WEEKEND_FACTOR, op_kw)

    # Cooling load (shaped by hour-of-day × monthly fraction)
    cool_monthly_frac = _MONTH_COOL_FRAC[month_arr - 1]  # broadcast to 8760
    cool_kw = cool_max_kw * _HOUR_COOL[hod] * cool_monthly_frac
    cool_kw = np.where(is_weekend, cool_kw * _WEEKEND_FACTOR, cool_kw)

    raw = base_kw + op_kw + cool_kw

    # Normalise to hit annual kWh target exactly
    target_kwh = _ANNUAL_KWH[brand]
    scale = target_kwh / raw.sum()
    profile = raw * scale

    return profile


def monthly_stats(profile: np.ndarray) -> list[dict[str, float]]:
    """Return a list of 12 dicts with kWh and peak_kw per month.

    Convenience function for diagnostics; not used by the engine directly.
    """
    stats = []
    for m_idx, start in enumerate(_MONTH_START_HOUR):
        end = start + _MONTH_DAYS[m_idx] * 24
        month_slice = profile[start:end]
        stats.append(
            {
                "month": m_idx + 1,
                "kwh": float(month_slice.sum()),
                "peak_kw": float(month_slice.max()),
            }
        )
    return stats
