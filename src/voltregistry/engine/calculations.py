"""Pure calculation helpers for the VoltRegistry tariff engine.

All functions are stateless and deterministic (no LLM calls, no random).
They consume aggregated load data and tariff charge objects, and return
MonthlyLineItem lists with human-readable calculation_basis strings.

Spec §10 calculation order:
  1. Aggregate load profile → monthly kWh + NCP kW
  2. Apply TOU mapping (if tariff has a schedule)
  3. Fixed charges
  4. Energy charges (flat / TOU-split / tiered)
  5. Demand charges (flat / tiered)
  6. Riders — pass 1: $/kWh and $/kW riders
             pass 2: percentage riders (applied to named charge subtotals)
  7. Delivery filter (applied in TariffEngine, not here)
  8. Sum and emit CalculationResult
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from voltregistry.load.synthetic import _MONTH_DAYS, _MONTH_START_HOUR
from voltregistry.tariffs.models import (
    Charge,
    Season,
    TouSchedule,
)

# ---------------------------------------------------------------------------
# Reference year constants (non-leap 2025, starts Wednesday)
# ---------------------------------------------------------------------------

_JAN1_WEEKDAY = 2  # Wednesday; 0=Mon … 6=Sun

# Default summer months for regulated utilities without a TOU schedule
# (June–September per SERC/Southeast convention)
_DEFAULT_SUMMER_MONTHS: frozenset[int] = frozenset([6, 7, 8, 9])

# NERC holiday dates for 2025 (used when holiday_calendar == "nerc")
_NERC_HOLIDAYS_2025: frozenset[str] = frozenset(
    [
        "2025-01-01",  # New Year's Day
        "2025-05-26",  # Memorial Day
        "2025-07-04",  # Independence Day
        "2025-09-01",  # Labor Day
        "2025-11-27",  # Thanksgiving
        "2025-12-25",  # Christmas
    ]
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MonthlyLoad:
    """Aggregated load data for a single calendar month."""

    month: int  # 1–12
    kwh: float  # total kWh consumed
    ncp_kw: float  # non-coincident peak kW (max hourly value)
    tou_kwh: dict[str, float] = field(default_factory=dict)
    # key = tou_period_name (e.g. "on_peak"), value = kWh in that period


@dataclass
class MonthlyLineItem:
    """Single charge line item for one calendar month."""

    charge_id: str
    name: str
    category: str  # "delivery" | "supply" | "ambiguous"
    included: bool  # True if included in delivery total
    amount: float  # $ amount for this line item this month
    calculation_basis: str  # human-readable math string


# ---------------------------------------------------------------------------
# Monthly aggregation
# ---------------------------------------------------------------------------


def aggregate_monthly_profile(load_profile: np.ndarray) -> list[MonthlyLoad]:
    """Slice an 8760-element load profile into 12 MonthlyLoad objects.

    Args:
        load_profile: shape (8760,) array of hourly kW values

    Returns:
        List of 12 MonthlyLoad objects (January … December).
    """
    assert load_profile.shape == (8760,), (
        f"Expected shape (8760,), got {load_profile.shape}"
    )
    result = []
    for m_idx, start in enumerate(_MONTH_START_HOUR):
        end = start + _MONTH_DAYS[m_idx] * 24
        slc = load_profile[start:end]
        result.append(
            MonthlyLoad(
                month=m_idx + 1,
                kwh=float(slc.sum()),
                ncp_kw=float(slc.max()),
            )
        )
    return result


# ---------------------------------------------------------------------------
# TOU mapping
# ---------------------------------------------------------------------------


def _hour_to_date_parts(h: int) -> tuple[int, int, int]:
    """Return (month 1-12, weekday 0-6, hour_of_day 0-23) for hour index h."""
    day = h // 24
    hod = h % 24
    weekday = (_JAN1_WEEKDAY + day) % 7
    month = 1
    for m_idx, start in enumerate(_MONTH_START_HOUR):
        if h >= start:
            month = m_idx + 1
    return month, weekday, hod


def _hour_in_range(hod: int, start: int, end: int) -> bool:
    """Return True if hod falls in [start, end) wrapping at midnight.

    The spec uses end-exclusive ranges.  If start < end it is a simple
    interval; if start > end the range wraps midnight.
    """
    if start < end:
        return start <= hod < end
    # midnight-crossing range, e.g. start=22, end=6 → 22,23,0,1,2,3,4,5
    return hod >= start or hod < end


def build_tou_map(
    load_profile: np.ndarray,
    tou_schedule: TouSchedule,
) -> list[dict[str, float]]:
    """Return per-month TOU kWh splits.

    Args:
        load_profile:  shape (8760,) array of hourly kW values
        tou_schedule:  TouSchedule object from the tariff bundle

    Returns:
        List of 12 dicts (one per month), each mapping
        tou_period_name (str) → kWh consumed in that period that month.
        Hours that match no period are assigned to "off_peak".
    """
    assert load_profile.shape == (8760,), (
        f"Expected shape (8760,), got {load_profile.shape}"
    )

    # Build set of holiday date strings for fast lookup
    holiday_strs: frozenset[str]
    if tou_schedule.holiday_dates:
        holiday_strs = frozenset(tou_schedule.holiday_dates)
    elif tou_schedule.holiday_calendar.value in ("nerc", "federal"):
        holiday_strs = _NERC_HOLIDAYS_2025
    else:
        holiday_strs = frozenset()

    # Pre-compute date string for each day (for holiday lookup)
    # 2025-01-01 is day 0
    from datetime import date, timedelta

    base_date = date(2025, 1, 1)

    # Accumulate kWh per (month, period_name)
    monthly_tou: list[dict[str, float]] = [{} for _ in range(12)]

    for h in range(8760):
        day = h // 24
        hod = h % 24
        weekday = (_JAN1_WEEKDAY + day) % 7  # 0=Mon … 6=Sun

        month = 1
        for m_idx, start in enumerate(_MONTH_START_HOUR):
            if h >= start:
                month = m_idx + 1

        is_weekend = weekday >= 5
        d_str = (base_date + timedelta(days=day)).isoformat()
        is_holiday = d_str in holiday_strs

        # Determine TOU period for this hour
        matched_period = "off_peak"  # default if no period matches
        for period in tou_schedule.periods:
            # Check season
            if period.season_months and month not in period.season_months:
                continue
            # Check weekday mask
            mask = period.weekday_mask.value
            if mask == "weekdays" and (is_weekend or is_holiday):
                continue
            if mask == "weekends" and not is_weekend and not is_holiday:
                continue
            # If holidays_off_peak and this is a holiday, skip non-off_peak periods
            if period.holidays_off_peak and is_holiday and period.name.value != "off_peak":
                continue
            # Check hour ranges
            for hr in period.hour_ranges:
                if _hour_in_range(hod, hr.start, hr.end):
                    matched_period = period.name.value
                    break
            if matched_period != "off_peak":
                break

        kwh = float(load_profile[h])  # kW × 1 hr = kWh
        m_dict = monthly_tou[month - 1]
        m_dict[matched_period] = m_dict.get(matched_period, 0.0) + kwh

    return monthly_tou


# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------


def get_summer_months(tou_schedule: TouSchedule | None) -> frozenset[int]:
    """Return the set of month numbers (1-12) that count as 'summer'.

    If a TOU schedule is present, derive summer from the months associated
    with TOU periods that have limited season coverage (i.e. not all 12).
    Falls back to the default [6,7,8,9] if the schedule is all-year.
    """
    if tou_schedule is None:
        return _DEFAULT_SUMMER_MONTHS

    for period in tou_schedule.periods:
        months = period.season_months
        if months and len(months) < 12:
            # The months assigned to a restricted period define summer
            # (Typically on_peak summer = [6,7,8,9])
            return frozenset(months)

    return _DEFAULT_SUMMER_MONTHS


def month_season(month: int, summer_months: frozenset[int]) -> Season:
    """Return Season.summer or Season.winter for a given month."""
    return Season.summer if month in summer_months else Season.winter


# ---------------------------------------------------------------------------
# Fixed charge
# ---------------------------------------------------------------------------


def calc_fixed(
    charge: Charge,
    included: bool,
) -> list[MonthlyLineItem]:
    """Return 12 monthly line items for a fixed $/month charge."""
    assert charge.value is not None, f"Fixed charge {charge.charge_id} has no value"
    items = []
    for _m in range(1, 13):
        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=charge.value,
                calculation_basis=f"${charge.value:,.2f}/month (fixed)",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Energy charges
# ---------------------------------------------------------------------------


def calc_energy_flat(
    charge: Charge,
    monthly_loads: list[MonthlyLoad],
    summer_months: frozenset[int],
    included: bool,
) -> list[MonthlyLineItem]:
    """Flat $/kWh energy charge, optionally season-specific."""
    assert charge.value is not None, f"Energy charge {charge.charge_id} has no value"
    rate = charge.value
    charge_season = charge.applies_to.season

    items = []
    for ml in monthly_loads:
        # Skip months that don't match the charge's season
        if charge_season == Season.summer and ml.month not in summer_months:
            items.append(_zero_item(charge, ml.month, included, "n/a (off-season)"))
            continue
        if charge_season == Season.winter and ml.month in summer_months:
            items.append(_zero_item(charge, ml.month, included, "n/a (off-season)"))
            continue

        amount = ml.kwh * rate
        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=f"{ml.kwh:,.0f} kWh × ${rate:.5f}/kWh",
            )
        )
    return items


def calc_energy_tou(
    charge: Charge,
    monthly_loads: list[MonthlyLoad],
    summer_months: frozenset[int],
    included: bool,
) -> list[MonthlyLineItem]:
    """TOU energy charge: $/kWh for a specific season + period combination."""
    assert charge.value is not None, f"TOU energy charge {charge.charge_id} has no value"
    rate = charge.value
    charge_season = charge.applies_to.season
    tou_period = charge.applies_to.tou_period  # e.g. "on_peak"

    items = []
    for ml in monthly_loads:
        # Season gate
        if charge_season == Season.summer and ml.month not in summer_months:
            items.append(_zero_item(charge, ml.month, included, "n/a (off-season)"))
            continue
        if charge_season == Season.winter and ml.month in summer_months:
            items.append(_zero_item(charge, ml.month, included, "n/a (off-season)"))
            continue

        period_kwh = ml.tou_kwh.get(tou_period or "off_peak", 0.0)
        amount = period_kwh * rate
        period_label = tou_period or "off_peak"
        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=(
                    f"{period_kwh:,.0f} kWh ({period_label}) × ${rate:.5f}/kWh"
                ),
            )
        )
    return items


def calc_energy_tiered(
    charge: Charge,
    monthly_loads: list[MonthlyLoad],
    included: bool,
) -> list[MonthlyLineItem]:
    """Tiered energy charge (tier_basis = kwh_monthly)."""
    items = []
    for ml in monthly_loads:
        kwh = ml.kwh
        amount = 0.0
        basis_parts = []
        remaining = kwh

        for tier in charge.tiers:
            tier_min = tier.min
            tier_max = tier.max
            tier_kw = tier_max - tier_min if tier_max is not None else remaining
            applied = min(remaining, tier_kw)
            if applied <= 0:
                break
            tier_amount = applied * tier.rate
            amount += tier_amount
            basis_parts.append(f"{applied:,.0f} kWh × ${tier.rate:.5f}/kWh")
            remaining -= applied
            if remaining <= 0:
                break

        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=" + ".join(basis_parts) if basis_parts else "$0.00",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Demand charges
# ---------------------------------------------------------------------------


def calc_demand_flat(
    charge: Charge,
    monthly_billed_kw: list[float],
    included: bool,
) -> list[MonthlyLineItem]:
    """Flat $/kW demand charge applied to billed demand (post-ratchet)."""
    assert charge.value is not None, f"Demand charge {charge.charge_id} has no value"
    rate = charge.value
    items = []
    for _m_idx, billed_kw in enumerate(monthly_billed_kw):
        amount = billed_kw * rate
        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=f"{billed_kw:,.1f} kW × ${rate:.5f}/kW",
            )
        )
    return items


def calc_demand_tiered(
    charge: Charge,
    monthly_billed_kw: list[float],
    included: bool,
) -> list[MonthlyLineItem]:
    """Tiered demand charge (tier_basis = kw_billed)."""
    items = []
    for billed_kw in monthly_billed_kw:
        amount = 0.0
        basis_parts = []
        remaining = billed_kw

        for tier in charge.tiers:
            tier_min = tier.min
            tier_max = tier.max
            tier_width = (tier_max - tier_min) if tier_max is not None else remaining
            applied = min(remaining, tier_width)
            if applied <= 0:
                break
            tier_amount = applied * tier.rate
            amount += tier_amount
            basis_parts.append(f"{applied:,.1f} kW × ${tier.rate:.5f}/kW")
            remaining -= applied
            if remaining <= 0:
                break

        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=" + ".join(basis_parts) if basis_parts else "$0.00",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Riders
# ---------------------------------------------------------------------------


def calc_rider_per_kwh(
    charge: Charge,
    monthly_loads: list[MonthlyLoad],
    included: bool,
) -> list[MonthlyLineItem]:
    """Flat $/kWh rider applied to all monthly kWh."""
    assert charge.value is not None, f"Rider {charge.charge_id} has no value"
    rate = charge.value
    items = []
    for ml in monthly_loads:
        amount = ml.kwh * rate
        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=f"{ml.kwh:,.0f} kWh × ${rate:.5f}/kWh",
            )
        )
    return items


def calc_rider_percent(
    charge: Charge,
    monthly_subtotals: list[float],
    included: bool,
) -> list[MonthlyLineItem]:
    """Percentage rider applied to a per-month subtotal of named charges.

    Args:
        charge:             The percentage rider Charge object.
        monthly_subtotals:  List of 12 pre-computed subtotals (one per month)
                            covering the charges listed in applied_to_charge_ids.
        included:           Whether this charge is included in delivery total.
    """
    assert charge.value is not None, f"Pct rider {charge.charge_id} has no value"
    pct = charge.value  # e.g. 0.029 for 2.9%
    items = []
    for _m_idx, subtotal in enumerate(monthly_subtotals):
        amount = subtotal * pct
        items.append(
            MonthlyLineItem(
                charge_id=charge.charge_id,
                name=charge.name,
                category=charge.classification.category.value,
                included=included,
                amount=amount,
                calculation_basis=(
                    f"{pct * 100:.2f}% × ${subtotal:,.2f} (delivery subtotal)"
                ),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zero_item(
    charge: Charge,
    month: int,
    included: bool,
    basis: str,
) -> MonthlyLineItem:
    """Return a zero-dollar line item (e.g. off-season)."""
    return MonthlyLineItem(
        charge_id=charge.charge_id,
        name=charge.name,
        category=charge.classification.category.value,
        included=included,
        amount=0.0,
        calculation_basis=basis,
    )
