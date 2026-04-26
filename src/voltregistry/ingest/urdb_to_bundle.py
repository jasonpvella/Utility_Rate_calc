"""URDB raw JSON → VoltRegistry TariffBundle adapter.

Translates OpenEI Utility Rate Database (URDB) API v8 response items into
TariffBundle objects that the tariff engine can calculate directly.

URDB API reference: https://openei.org/services/doc/rest/util_rates/?version=8

Key translation challenges handled here
-----------------------------------------
1. **TOU schedule reconstruction.** URDB encodes time-of-use as two 12×24
   integer matrices (weekday and weekend), where each cell is a period index.
   We reconstruct named TouPeriod objects with explicit ``season_months`` and
   ``hour_ranges`` by scanning those matrices.

2. **Period naming.** URDB does not name periods (on_peak, off_peak, …).
   We rank periods by their average energy rate: highest → on_peak,
   lowest → off_peak, middle tiers → mid_peak.

3. **Delivery/supply classification.** URDB has no classification.
   We run the existing classifier after building the charge list.
   All URDB-sourced bundles get ``delivery_supply_review_status =
   auto_classified`` and must be manually reviewed before charges with
   confidence < 0.8 contribute to delivery totals.

4. **Riders.** URDB does not systematically encode riders.  Only the
   standard fields (fuel adj sometimes folded into energy ``adj`` fields,
   min monthly charge) are captured.  The ``schema_limitations`` on any
   CalculationResult will note what was skipped.

5. **Tiers.** URDB tier objects sometimes lack explicit upper bounds.
   We derive bounds from the ``max`` field when present; otherwise single-
   tier charges are emitted as flat-rate charges.

Coverage after conversion
--------------------------
  ✓ Fixed customer charge  (``fixedmonthlycharge``)
  ✓ TOU and flat energy charges  (``energyratestructure`` + schedules)
  ✓ NCP demand charges  (``demandratestructure``, ``flatdemandstructure``)
  ✓ Coincident-peak demand  (``coincidentratestructure``) — NCP proxy in v0
  ✓ Minimum monthly charge rule  (``minmonthlycharge``)
  ✗ Utility-specific riders  (not in URDB; require manual addition)
  ✗ Reactive power / power factor charges  (v0 out-of-scope)

Output tariffs carry ``ingestion_method = urdb`` and
``delivery_supply_review_status = auto_classified``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from voltregistry.tariffs.classifier import classify_bundle
from voltregistry.tariffs.models import (
    AppliesTo,
    Charge,
    ChargeClassification,
    ChargeType,
    ChargeUnit,
    ClassificationMethod,
    DeliverySupplyCategory,
    DeliverySupplyReviewStatus,
    DemandBasis,
    HolidayCalendar,
    HourRange,
    IngestionMethod,
    Rule,
    RuleParameters,
    RuleType,
    Season,
    Tariff,
    TariffAvailability,
    TariffBundle,
    TariffEligibility,
    TierBasis,
    TierBlock,
    TouPeriod,
    TouPeriodName,
    TouSchedule,
    VoltageLevel,
    WeekdayMask,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Period-name rank order (index 0 = lowest priority → off_peak)
# Periods are ranked by average rate; the last name is assigned to the
# highest-rate period.
# ---------------------------------------------------------------------------
_PERIOD_NAMES_BY_RANK: list[TouPeriodName] = [
    TouPeriodName.off_peak,
    TouPeriodName.mid_peak,
    TouPeriodName.on_peak,
]

# Months that constitute "summer" when the schedule doesn't otherwise indicate.
_DEFAULT_SUMMER_MONTHS: frozenset[int] = frozenset([6, 7, 8, 9])

# Minimum rate threshold: periods with avg rate ≤ this are treated as
# "base/off-peak" even if another period has the same rate.
_ZERO_RATE_THRESHOLD: float = 1e-9


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def urdb_to_bundle(
    raw: dict[str, Any],
    eia_id: str,
    market_structure: str = "regulated_vertical",
) -> TariffBundle | None:
    """Convert a single URDB API item to a TariffBundle.

    Args:
        raw:              Full URDB v8 item dict (``detail=full`` response).
        eia_id:           EIA utility ID string used as the owner EIA ID.
        market_structure: The utility's market structure string from our DB
                          (e.g. ``"regulated_vertical"``,
                          ``"deregulated_delivery_only"``).  Passed to the
                          classifier for §7.2 override logic.

    Returns:
        A classified TariffBundle ready for DB upsert, or ``None`` if the
        item cannot be converted (e.g. missing label, residential sector only,
        fatal schema error).
    """
    label = raw.get("label")
    if not label:
        logger.warning("urdb_to_bundle: item has no label, skipping")
        return None

    # Sanity-check sector — skip residential-only tariffs
    sectors = _as_list(raw.get("sector", []))
    if sectors and all(s.lower() == "residential" for s in sectors):
        logger.debug("urdb_to_bundle: %s is residential-only, skipping", label)
        return None

    tariff_id = f"urdb-{label}"
    logger.info("urdb_to_bundle: converting label=%s → tariff_id=%s", label, tariff_id)

    try:
        charges: list[Charge] = []

        # ── Fixed charge ─────────────────────────────────────────────────
        fixed_charge = _extract_fixed_charge(tariff_id, raw)
        if fixed_charge:
            charges.append(fixed_charge)

        # ── TOU schedule (needed before energy/demand charges) ────────────
        tou_schedule = _extract_tou_schedule(tariff_id, raw)

        # ── Energy charges ────────────────────────────────────────────────
        energy_charges = _extract_energy_charges(tariff_id, raw, tou_schedule)
        charges.extend(energy_charges)

        # ── Demand charges ────────────────────────────────────────────────
        demand_charges = _extract_demand_charges(tariff_id, raw)
        charges.extend(demand_charges)

        # ── Rules ────────────────────────────────────────────────────────
        rules = _extract_rules(tariff_id, raw)

        # Assign placeholder classification before running classifier
        charges = _apply_placeholder_classification(charges)

        # ── Build TariffBundle and run classifier ─────────────────────────
        tariff = _build_tariff(tariff_id, label, eia_id, raw, charges, rules, tou_schedule)
        bundle = TariffBundle(
            tariff=tariff,
            charges=charges,
            tou_schedule=tou_schedule,
            rules=rules,
        )
        classified_bundle = classify_bundle(bundle, market_structure)
        logger.info(
            "urdb_to_bundle: %s → %d charges, %d rules, tou=%s",
            tariff_id,
            len(classified_bundle.charges),
            len(classified_bundle.rules),
            classified_bundle.tou_schedule is not None,
        )
        return classified_bundle

    except Exception:
        logger.exception("urdb_to_bundle: fatal error converting label=%s", label)
        return None


# ---------------------------------------------------------------------------
# Fixed charge
# ---------------------------------------------------------------------------


def _extract_fixed_charge(tariff_id: str, raw: dict) -> Charge | None:
    val = raw.get("fixedmonthlycharge")
    if val is None or float(val) <= 0:
        return None
    charge_id = f"{tariff_id}-customer"
    return Charge(
        charge_id=charge_id,
        tariff_id=tariff_id,
        name="Customer Charge",
        type=ChargeType.fixed,
        unit=ChargeUnit.dollar_per_month,
        value=float(val),
        applies_to=AppliesTo(season=Season.all),
        classification=_placeholder_clf(),
    )


# ---------------------------------------------------------------------------
# TOU schedule reconstruction
# ---------------------------------------------------------------------------


def _extract_tou_schedule(tariff_id: str, raw: dict) -> TouSchedule | None:
    """Reconstruct a TouSchedule from URDB's 12×24 schedule matrices.

    Returns None if the tariff has no meaningful TOU structure (single period
    or schedule data absent).
    """
    wd_sched: list[list[int]] | None = raw.get("energyweekdayschedule")
    we_sched: list[list[int]] | None = raw.get("energyweekendschedule")
    rate_struct = raw.get("energyratestructure") or []

    if not wd_sched or len(wd_sched) != 12:
        return None

    # Find unique period indexes across the entire schedule
    all_periods: set[int] = set()
    for row in wd_sched:
        all_periods.update(row)
    if we_sched and len(we_sched) == 12:
        for row in we_sched:
            all_periods.update(row)

    # Single period → no TOU (flat rate schedule)
    if len(all_periods) <= 1:
        return None

    # Rank periods by average energy rate (highest = on_peak)
    period_avg_rate = _rank_periods_by_rate(all_periods, rate_struct)

    # Build one or more TouPeriod objects per period index
    tou_periods: list[TouPeriod] = []
    for period_idx in sorted(all_periods):
        # Weekday hours/months for this period
        wd_hours_by_month = _period_hours_by_month(wd_sched, period_idx)
        we_hours_by_month = (
            _period_hours_by_month(we_sched, period_idx) if we_sched else {}
        )

        period_name = _period_name_for_idx(period_idx, period_avg_rate)

        # Build weekday TouPeriod entries
        wd_entries = _hours_by_month_to_tou_periods(
            period_name, wd_hours_by_month, WeekdayMask.weekdays, period_idx == 0
        )
        tou_periods.extend(wd_entries)

        # Build weekend TouPeriod entries (only if they differ from weekday)
        if we_sched:
            we_entries = _hours_by_month_to_tou_periods(
                period_name, we_hours_by_month, WeekdayMask.weekends, period_idx == 0
            )
            # Only emit weekend entries that differ from weekday pattern
            if we_hours_by_month != wd_hours_by_month:
                tou_periods.extend(we_entries)

    if not tou_periods:
        return None

    schedule_id = f"{tariff_id}-tou"
    return TouSchedule(
        tou_schedule_id=schedule_id,
        periods=tou_periods,
        holiday_calendar=HolidayCalendar.nerc,
        holiday_dates=[],
    )


def _period_hours_by_month(
    schedule: list[list[int]], period_idx: int
) -> dict[int, list[int]]:
    """Return {month_1indexed: [hours]} where the schedule has period_idx."""
    result: dict[int, list[int]] = {}
    for month_0idx, row in enumerate(schedule):
        hours = [h for h, p in enumerate(row) if p == period_idx]
        if hours:
            result[month_0idx + 1] = hours
    return result


def _hours_by_month_to_tou_periods(
    period_name: TouPeriodName,
    hours_by_month: dict[int, list[int]],
    weekday_mask: WeekdayMask,
    is_base_period: bool,
) -> list[TouPeriod]:
    """Group months with identical hour patterns into TouPeriod objects."""
    if not hours_by_month:
        return []

    # Group months that share the same hour list
    pattern_to_months: dict[tuple[int, ...], list[int]] = {}
    for month, hours in hours_by_month.items():
        key = tuple(sorted(hours))
        pattern_to_months.setdefault(key, []).append(month)

    entries: list[TouPeriod] = []
    for hours_tuple, months in pattern_to_months.items():
        hour_ranges = _hours_to_ranges(list(hours_tuple))
        entries.append(
            TouPeriod(
                name=period_name,
                season_months=sorted(months),
                hour_ranges=hour_ranges,
                weekday_mask=weekday_mask,
                holidays_off_peak=True,
            )
        )
    return entries


def _hours_to_ranges(hours: list[int]) -> list[HourRange]:
    """Convert a sorted list of hour integers to contiguous HourRange objects.

    HourRange uses end-exclusive semantics matching our engine convention.
    """
    if not hours:
        return []
    hours = sorted(set(hours))
    ranges: list[HourRange] = []
    start = hours[0]
    prev = hours[0]
    for h in hours[1:]:
        if h != prev + 1:
            ranges.append(HourRange(start=start, end=prev + 1))
            start = h
        prev = h
    ranges.append(HourRange(start=start, end=prev + 1))
    return ranges


def _rank_periods_by_rate(
    period_idxs: set[int], rate_struct: list[list[dict]]
) -> dict[int, float]:
    """Return {period_idx: avg_energy_rate} for ranking."""
    result: dict[int, float] = {}
    for idx in period_idxs:
        if idx < len(rate_struct) and rate_struct[idx]:
            rates = [float(t.get("rate", 0)) for t in rate_struct[idx]]
            result[idx] = sum(rates) / len(rates) if rates else 0.0
        else:
            result[idx] = 0.0
    return result


def _period_name_for_idx(
    period_idx: int, period_avg_rate: dict[int, float]
) -> TouPeriodName:
    """Assign on_peak / mid_peak / off_peak based on rate ranking."""
    if len(period_avg_rate) == 1:
        return TouPeriodName.off_peak

    # Sort by average rate ascending; assign names from off_peak → on_peak
    sorted_idxs = sorted(period_avg_rate, key=lambda i: period_avg_rate[i])
    rank_count = len(sorted_idxs)
    rank = sorted_idxs.index(period_idx)

    # Map rank (0=lowest rate) to period name
    if rank_count == 2:
        names = [TouPeriodName.off_peak, TouPeriodName.on_peak]
    elif rank_count == 3:
        names = [TouPeriodName.off_peak, TouPeriodName.mid_peak, TouPeriodName.on_peak]
    else:
        # 4+ periods: off_peak, mid_peak, on_peak, super_off_peak (rare)
        names = _PERIOD_NAMES_BY_RANK
        if rank >= len(names):
            rank = len(names) - 1

    return names[rank]


# ---------------------------------------------------------------------------
# Energy charges
# ---------------------------------------------------------------------------


def _extract_energy_charges(
    tariff_id: str,
    raw: dict,
    tou_schedule: TouSchedule | None,
) -> list[Charge]:
    """Extract energy charges from URDB energyratestructure."""
    rate_struct: list[list[dict]] = raw.get("energyratestructure") or []
    wd_sched: list[list[int]] | None = raw.get("energyweekdayschedule")

    if not rate_struct:
        return []

    # Determine which months belong to each period for season labelling
    month_to_period: dict[int, int] = {}  # month (1-12) → dominant period
    if wd_sched and len(wd_sched) == 12:
        for m_idx, row in enumerate(wd_sched):
            # Use the peak-hour (2 pm) as the representative hour for naming
            peak_hour_period = row[14] if len(row) > 14 else row[0]
            month_to_period[m_idx + 1] = peak_hour_period

    charges: list[Charge] = []
    emitted_ids: set[str] = set()

    for period_idx, tiers in enumerate(rate_struct):
        if not tiers:
            continue

        # Which months does this period appear in (if schedule present)?
        if month_to_period:
            period_months = [m for m, p in month_to_period.items() if p == period_idx]
            if not period_months:
                continue  # period defined but never scheduled — skip
        else:
            period_months = list(range(1, 13))

        season = _months_to_season(period_months)
        tou_period_name = (
            _period_name_for_idx(
                period_idx,
                _rank_periods_by_rate(
                    set(range(len(rate_struct))),
                    rate_struct,
                ),
            ).value
            if tou_schedule is not None and len(rate_struct) > 1
            else None
        )

        if len(tiers) == 1 or not _has_explicit_tier_bounds(tiers):
            # Single tier or no bounds → flat charge
            rate = float(tiers[0].get("rate", 0.0))
            adj = float(tiers[0].get("adj", 0.0))
            total_rate = rate + adj
            if abs(total_rate) < _ZERO_RATE_THRESHOLD:
                continue

            charge_id = _unique_id(
                tariff_id, "energy", season.value, tou_period_name, emitted_ids
            )
            emitted_ids.add(charge_id)
            charges.append(
                Charge(
                    charge_id=charge_id,
                    tariff_id=tariff_id,
                    name=_energy_charge_name(season, tou_period_name),
                    type=ChargeType.energy,
                    unit=ChargeUnit.dollar_per_kwh,
                    value=total_rate,
                    applies_to=AppliesTo(
                        season=season,
                        tou_period=tou_period_name,
                    ),
                    classification=_placeholder_clf(),
                )
            )
        else:
            # Multi-tier with explicit bounds → TierBlock list
            tier_blocks = _build_tier_blocks(tiers, TierBasis.kwh_monthly)
            if not tier_blocks:
                continue
            charge_id = _unique_id(
                tariff_id, "energy-tiered", season.value, tou_period_name, emitted_ids
            )
            emitted_ids.add(charge_id)
            charges.append(
                Charge(
                    charge_id=charge_id,
                    tariff_id=tariff_id,
                    name=_energy_charge_name(season, tou_period_name),
                    type=ChargeType.energy,
                    unit=ChargeUnit.dollar_per_kwh,
                    value=None,
                    tiers=tier_blocks,
                    applies_to=AppliesTo(season=season, tou_period=tou_period_name),
                    classification=_placeholder_clf(),
                )
            )

    return charges


# ---------------------------------------------------------------------------
# Demand charges
# ---------------------------------------------------------------------------


def _extract_demand_charges(tariff_id: str, raw: dict) -> list[Charge]:
    """Extract NCP, flat, and coincident-peak demand charges from URDB."""
    charges: list[Charge] = []
    emitted_ids: set[str] = set()

    # ── NCP demand (demandweekdayschedule present) ────────────────────────
    ncp_struct: list[list[dict]] = raw.get("demandratestructure") or []
    ncp_wd_sched: list[list[int]] | None = raw.get("demandweekdayschedule")

    for period_idx, tiers in enumerate(ncp_struct):
        if not tiers:
            continue

        # Determine which months this period applies to
        if ncp_wd_sched and len(ncp_wd_sched) == 12:
            period_months = [
                m + 1
                for m, row in enumerate(ncp_wd_sched)
                if any(p == period_idx for p in row)
            ]
        else:
            period_months = list(range(1, 13))

        if not period_months:
            continue

        season = _months_to_season(period_months)

        if len(tiers) == 1 or not _has_explicit_tier_bounds(tiers):
            rate = float(tiers[0].get("rate", 0.0))
            adj = float(tiers[0].get("adj", 0.0))
            total_rate = rate + adj
            if abs(total_rate) < _ZERO_RATE_THRESHOLD:
                continue

            charge_id = _unique_id(
                tariff_id, "demand-ncp", season.value, None, emitted_ids
            )
            emitted_ids.add(charge_id)
            charges.append(
                Charge(
                    charge_id=charge_id,
                    tariff_id=tariff_id,
                    name=_demand_charge_name(season, "NCP"),
                    type=ChargeType.demand,
                    demand_basis=DemandBasis.ncp,
                    unit=ChargeUnit.dollar_per_kw,
                    value=total_rate,
                    applies_to=AppliesTo(season=season),
                    classification=_placeholder_clf(),
                )
            )
        else:
            tier_blocks = _build_tier_blocks(tiers, TierBasis.kw_billed)
            if not tier_blocks:
                continue
            charge_id = _unique_id(
                tariff_id, "demand-ncp-tiered", season.value, None, emitted_ids
            )
            emitted_ids.add(charge_id)
            charges.append(
                Charge(
                    charge_id=charge_id,
                    tariff_id=tariff_id,
                    name=_demand_charge_name(season, "NCP"),
                    type=ChargeType.demand,
                    demand_basis=DemandBasis.ncp,
                    unit=ChargeUnit.dollar_per_kw,
                    value=None,
                    tiers=tier_blocks,
                    applies_to=AppliesTo(season=season),
                    classification=_placeholder_clf(),
                )
            )

    # ── Flat demand (flatdemandstructure) ─────────────────────────────────
    flat_struct: list[list[dict]] = raw.get("flatdemandstructure") or []
    flat_months_raw: list[int] = raw.get("flatdemandmonths") or []

    if flat_struct and flat_months_raw and len(flat_months_raw) == 12:
        # flatdemandmonths maps month (0-indexed) → period index
        for period_idx, tiers in enumerate(flat_struct):
            if not tiers:
                continue
            period_months = [
                m + 1
                for m, p in enumerate(flat_months_raw)
                if p == period_idx
            ]
            if not period_months:
                continue
            season = _months_to_season(period_months)
            rate = float(tiers[0].get("rate", 0.0)) if tiers else 0.0
            if abs(rate) < _ZERO_RATE_THRESHOLD:
                continue
            charge_id = _unique_id(
                tariff_id, "demand-flat", season.value, None, emitted_ids
            )
            emitted_ids.add(charge_id)
            charges.append(
                Charge(
                    charge_id=charge_id,
                    tariff_id=tariff_id,
                    name=_demand_charge_name(season, "Flat"),
                    type=ChargeType.demand,
                    demand_basis=DemandBasis.ncp,
                    unit=ChargeUnit.dollar_per_kw,
                    value=rate,
                    applies_to=AppliesTo(season=season),
                    classification=_placeholder_clf(),
                )
            )

    # ── Coincident-peak demand (proxied to NCP in v0) ─────────────────────
    cp_struct: list[list[dict]] = raw.get("coincidentratestructure") or []
    if cp_struct:
        for _period_idx, tiers in enumerate(cp_struct):
            if not tiers:
                continue
            rate = float(tiers[0].get("rate", 0.0)) if tiers else 0.0
            if abs(rate) < _ZERO_RATE_THRESHOLD:
                continue
            charge_id = _unique_id(
                tariff_id, "demand-cp", "all", None, emitted_ids
            )
            emitted_ids.add(charge_id)
            charges.append(
                Charge(
                    charge_id=charge_id,
                    tariff_id=tariff_id,
                    name="Transmission / Coincident Peak Demand Charge",
                    type=ChargeType.demand,
                    demand_basis=DemandBasis.cp,
                    unit=ChargeUnit.dollar_per_kw,
                    value=rate,
                    applies_to=AppliesTo(season=Season.all),
                    classification=_placeholder_clf(),
                )
            )

    return charges


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _extract_rules(tariff_id: str, raw: dict) -> list[Rule]:
    rules = []
    min_charge = raw.get("minmonthlycharge")
    if min_charge and float(min_charge) > 0:
        rules.append(
            Rule(
                rule_id=f"{tariff_id}-min-charge",
                type=RuleType.minimum_charge,
                parameters=RuleParameters(
                    minimum_monthly_charge=float(min_charge)
                ),
            )
        )
    return rules


# ---------------------------------------------------------------------------
# Tariff assembly
# ---------------------------------------------------------------------------


def _build_tariff(
    tariff_id: str,
    label: str,
    eia_id: str,
    raw: dict,
    charges: list[Charge],
    rules: list[Rule],
    tou_schedule: TouSchedule | None,
) -> Tariff:
    effective_date = _urdb_timestamp_to_date(raw.get("startdate")) or "2020-01-01"
    end_date = _urdb_timestamp_to_date(raw.get("enddate"))

    # Eligibility
    peak_kw = raw.get("peakkwcapacitythreshold")
    eligibility = TariffEligibility(
        min_kw=float(peak_kw) if peak_kw else None,
        voltage_required=[VoltageLevel.secondary, VoltageLevel.primary],
        customer_classes=["commercial", "industrial"],
        notes=(raw.get("applicability") or "")[:500],
    )

    return Tariff(
        tariff_id=tariff_id,
        utility_eia_id=str(eia_id),
        urdb_id=label,
        name=(raw.get("name") or "Unknown Tariff")[:200],
        rate_code=(raw.get("name") or "")[:40],
        effective_date=effective_date,
        end_date=end_date,
        version="1",
        availability=TariffAvailability.optional,
        eligibility=eligibility,
        tou_schedule_id=tou_schedule.tou_schedule_id if tou_schedule else None,
        charges=[c.charge_id for c in charges],
        rules=[r.rule_id for r in rules],
        source_document=(raw.get("source") or raw.get("sourcetitle") or ""),
        ingestion_method=IngestionMethod.urdb,
        delivery_supply_review_status=DeliverySupplyReviewStatus.auto_classified,
    )


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


def _has_explicit_tier_bounds(tiers: list[dict]) -> bool:
    """Return True if at least one tier has a ``max`` field."""
    return any("max" in t for t in tiers)


def _build_tier_blocks(
    tiers: list[dict],
    basis: TierBasis,
) -> list[TierBlock]:
    """Convert URDB tier list to TierBlock objects.

    URDB tier format:
        ``[{"rate": 0.05, "unit": "kWh"}, {"rate": 0.08, "unit": "kWh", "max": 500}]``

    The ``max`` field on tier N gives the upper bound of tier N.
    Tier 0 always starts at 0; the last tier has no upper bound.
    """
    blocks: list[TierBlock] = []
    current_min = 0.0

    for i, tier in enumerate(tiers):
        rate = float(tier.get("rate", 0.0)) + float(tier.get("adj", 0.0))
        tier_max = tier.get("max")
        is_last = i == len(tiers) - 1

        if is_last:
            blocks.append(
                TierBlock(min=current_min, max=None, rate=rate, tier_basis=basis)
            )
        else:
            upper = float(tier_max) if tier_max is not None else None
            if upper is None:
                # No explicit bound — cannot determine tier boundary; collapse to flat
                logger.warning(
                    "urdb_to_bundle: tier %d has no max field; collapsing to flat rate %.5f",
                    i, rate,
                )
                return []
            blocks.append(
                TierBlock(min=current_min, max=upper, rate=rate, tier_basis=basis)
            )
            current_min = upper

    return blocks


# ---------------------------------------------------------------------------
# Season / naming helpers
# ---------------------------------------------------------------------------


def _months_to_season(months: list[int]) -> Season:
    """Classify a month list as summer, winter, or all."""
    if not months:
        return Season.all
    month_set = frozenset(months)
    if month_set == frozenset(range(1, 13)):
        return Season.all
    if month_set <= _DEFAULT_SUMMER_MONTHS:
        return Season.summer
    # If it overlaps summer but isn't exactly summer, use shoulder months as winter
    if not month_set.isdisjoint(_DEFAULT_SUMMER_MONTHS):
        return Season.summer
    return Season.winter


def _energy_charge_name(season: Season, tou_period: str | None) -> str:
    parts = ["Energy Charge"]
    if season == Season.summer:
        parts.append("Summer")
    elif season == Season.winter:
        parts.append("Winter")
    if tou_period:
        parts.append(tou_period.replace("_", "-").title())
    return " — ".join(parts) if len(parts) > 1 else parts[0]


def _demand_charge_name(season: Season, demand_type: str) -> str:
    parts = ["Demand Charge"]
    if season != Season.all:
        parts.append(season.value.title())
    return " — ".join(parts) if len(parts) > 1 else parts[0]


# ---------------------------------------------------------------------------
# Placeholder classification (overwritten by classify_bundle call)
# ---------------------------------------------------------------------------


def _placeholder_clf() -> ChargeClassification:
    return ChargeClassification(
        category=DeliverySupplyCategory.ambiguous,
        confidence=0.5,
        method=ClassificationMethod.rule_based,
        reasoning="Awaiting classifier pass.",
    )


def _apply_placeholder_classification(charges: list[Charge]) -> list[Charge]:
    """Ensure every charge has a classification before classifier runs."""
    result = []
    for c in charges:
        if c.classification is None:
            c = c.model_copy(update={"classification": _placeholder_clf()})
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _urdb_timestamp_to_date(ts: Any) -> str | None:
    """Convert a URDB Unix timestamp (int) to YYYY-MM-DD string, or None."""
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _unique_id(
    tariff_id: str,
    charge_type: str,
    season: str,
    tou_period: str | None,
    seen: set[str],
) -> str:
    """Generate a collision-free charge ID."""
    parts = [tariff_id, charge_type, season]
    if tou_period:
        parts.append(tou_period)
    base = "-".join(parts)
    candidate = base
    n = 1
    while candidate in seen:
        candidate = f"{base}-{n}"
        n += 1
    return candidate
