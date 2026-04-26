"""TariffEngine — the central calculation class for VoltRegistry.

Implements the §10 interface from VoltRegistry_v0_Spec.md verbatim:

    class TariffEngine:
        def __init__(self, tariff, charges, rules, tou):
            ...
        def calculate(self, load_profile) -> CalculationResult:
            ...

Calculation order (§10.2):
  1. Aggregate load profile → monthly kWh + NCP demand
  2. Apply TOU mapping (if schedule present)
  3. Compute fixed charges
  4. Compute energy charges (flat / TOU-split / tiered)
  5. Compute demand charges (flat / tiered) — applying ratchet if rule present
  6. Compute riders:
       Pass 1 — dollar-amount riders ($/kWh, $/kW)
       Pass 2 — percentage riders (applied to subtotal of named charges)
  7. Filter to delivery-only (§7.3 confidence + review-status rules)
  8. Sum and emit CalculationResult

Delivery filter (§7.3):
  A charge is included in the delivery total if:
    - category == "delivery", AND
    - confidence >= 0.8 OR tariff.delivery_supply_review_status == "manually_reviewed"

Schema limitations emitted in result.schema_limitations:
  - CP demand charges (demand_basis == "cp") use NCP demand as a proxy
    (true coincident-peak computation requires ISO/RTO system-peak data; §2 out-of-scope)

Critical rule (from CLAUDE.md):
  LLMs do not calculate.  All math is deterministic Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from voltregistry.engine.calculations import (
    MonthlyLineItem,
    aggregate_monthly_profile,
    build_tou_map,
    calc_demand_flat,
    calc_demand_tiered,
    calc_energy_flat,
    calc_energy_tiered,
    calc_energy_tou,
    calc_fixed,
    calc_rider_per_kwh,
    calc_rider_percent,
    get_summer_months,
)
from voltregistry.engine.rules import apply_demand_ratchet
from voltregistry.tariffs.models import (
    Charge,
    ChargeType,
    ChargeUnit,
    DeliverySupplyCategory,
    DeliverySupplyReviewStatus,
    Rule,
    RuleType,
    Tariff,
    TouSchedule,
)

# ---------------------------------------------------------------------------
# Result types (§10.3)
# ---------------------------------------------------------------------------


@dataclass
class MonthlyResult:
    """Calculation result for a single calendar month."""

    month: int  # 1–12
    total_delivery: float  # sum of delivery-included line items ($)
    line_items: list[MonthlyLineItem] = field(default_factory=list)


@dataclass
class CalculationResult:
    """Full calculation result for a (tariff, load_profile) pair."""

    tariff_id: str
    annual_total_delivery: float
    monthly: list[MonthlyResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_limitations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TariffEngine
# ---------------------------------------------------------------------------


class TariffEngine:
    """Calculates annual and monthly delivery cost for a tariff + load profile.

    Args:
        tariff:   Tariff metadata (eligibility, review status, etc.)
        charges:  List of Charge objects belonging to this tariff.
        rules:    List of Rule objects (ratchet, minimum charge) for this tariff.
        tou:      TouSchedule, or None if the tariff has no TOU.
    """

    def __init__(
        self,
        tariff: Tariff,
        charges: list[Charge],
        rules: list[Rule],
        tou: TouSchedule | None,
    ) -> None:
        self._tariff = tariff
        self._charges: dict[str, Charge] = {c.charge_id: c for c in charges}
        self._rules = rules
        self._tou = tou

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def calculate(self, load_profile: np.ndarray) -> CalculationResult:
        """Run the full tariff calculation against a load profile.

        Args:
            load_profile: shape (8760,) array of hourly kW values

        Returns:
            CalculationResult with line-itemized monthly breakdown.
        """
        warnings: list[str] = []
        schema_limitations: list[str] = []

        # ── Step 1: aggregate load ─────────────────────────────────────
        monthly_loads = aggregate_monthly_profile(load_profile)

        # ── Step 2: TOU mapping ────────────────────────────────────────
        if self._tou is not None:
            tou_monthly = build_tou_map(load_profile, self._tou)
            for ml, tou_d in zip(monthly_loads, tou_monthly, strict=True):
                ml.tou_kwh = tou_d

        summer_months = get_summer_months(self._tou)

        # ── Step 5 prep: demand ratchet ────────────────────────────────
        ncp_kw_list = [ml.ncp_kw for ml in monthly_loads]
        ratchet_rule = next(
            (r for r in self._rules if r.type == RuleType.demand_ratchet), None
        )
        billed_ncp_kw = (
            apply_demand_ratchet(ratchet_rule, ncp_kw_list)
            if ratchet_rule
            else ncp_kw_list.copy()
        )

        if ratchet_rule and any(
            b > a for b, a in zip(billed_ncp_kw, ncp_kw_list, strict=True)
        ):
            ratchet_pct = ratchet_rule.parameters.ratchet_percent
            warnings.append(
                f"Demand ratchet ({ratchet_pct:.0f}%) increased billed demand "
                "in one or more months."
            )

        # Note: CP demand uses NCP proxy (system-peak data unavailable in v0)
        cp_charges = [c for c in self._charges.values() if c.demand_basis and
                      c.demand_basis.value == "cp"]
        if cp_charges:
            schema_limitations.append(
                "Coincident-peak (CP) demand charges use NCP demand as a proxy. "
                "True 4CP/system-peak computation requires ISO/RTO data (v0 limitation)."
            )

        # ── Step 3–6: compute all charge line items ─────────────────────
        # charge_id → list[MonthlyLineItem] (one per month)
        charge_items: dict[str, list[MonthlyLineItem]] = {}

        for charge in self._charges.values():
            included = self._is_delivery_included(charge)

            if charge.type == ChargeType.fixed:
                charge_items[charge.charge_id] = calc_fixed(charge, included)

            elif charge.type == ChargeType.energy:
                if charge.tiers:
                    charge_items[charge.charge_id] = calc_energy_tiered(
                        charge, monthly_loads, included
                    )
                elif charge.applies_to.tou_period is not None:
                    charge_items[charge.charge_id] = calc_energy_tou(
                        charge, monthly_loads, summer_months, included
                    )
                else:
                    charge_items[charge.charge_id] = calc_energy_flat(
                        charge, monthly_loads, summer_months, included
                    )

            elif charge.type == ChargeType.demand:
                if charge.tiers:
                    charge_items[charge.charge_id] = calc_demand_tiered(
                        charge, billed_ncp_kw, included
                    )
                else:
                    charge_items[charge.charge_id] = calc_demand_flat(
                        charge, billed_ncp_kw, included
                    )

            elif charge.type == ChargeType.rider:
                if charge.unit == ChargeUnit.percent:
                    # Percentage riders are handled in pass 2 below
                    pass
                else:
                    # $/kWh riders (and $/kW riders — treated as $/kWh over usage)
                    charge_items[charge.charge_id] = calc_rider_per_kwh(
                        charge, monthly_loads, included
                    )

        # Pass 2 — percentage riders
        for charge in self._charges.values():
            if charge.type == ChargeType.rider and charge.unit == ChargeUnit.percent:
                included = self._is_delivery_included(charge)
                target_ids = charge.applies_to.applied_to_charge_ids

                # Build per-month subtotals from the listed charge IDs
                monthly_subtotals: list[float] = []
                for m_idx in range(12):
                    subtotal = 0.0
                    for cid in target_ids:
                        if cid in charge_items:
                            subtotal += charge_items[cid][m_idx].amount
                    monthly_subtotals.append(subtotal)

                charge_items[charge.charge_id] = calc_rider_percent(
                    charge, monthly_subtotals, included
                )

        # ── Step 7–8: assemble monthly results ───────────────────────────
        monthly_results: list[MonthlyResult] = []
        for m_idx in range(12):
            month_num = m_idx + 1
            line_items: list[MonthlyLineItem] = []

            for charge in self._charges.values():
                if charge.charge_id in charge_items:
                    line_items.append(charge_items[charge.charge_id][m_idx])

            total_delivery = sum(li.amount for li in line_items if li.included)
            monthly_results.append(
                MonthlyResult(
                    month=month_num,
                    total_delivery=total_delivery,
                    line_items=line_items,
                )
            )

        annual_total = sum(mr.total_delivery for mr in monthly_results)

        return CalculationResult(
            tariff_id=self._tariff.tariff_id,
            annual_total_delivery=annual_total,
            monthly=monthly_results,
            warnings=warnings,
            schema_limitations=schema_limitations,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_delivery_included(self, charge: Charge) -> bool:
        """Return True if this charge should be included in the delivery total.

        Rules (§7.3):
          - Must be category == "delivery"
          - confidence >= 0.8, OR tariff has delivery_supply_review_status
            == "manually_reviewed"
        """
        clf = charge.classification
        if clf.category != DeliverySupplyCategory.delivery:
            return False
        if clf.confidence >= 0.8:
            return True
        return (
            self._tariff.delivery_supply_review_status
            == DeliverySupplyReviewStatus.manually_reviewed
        )
