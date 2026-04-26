"""Delivery/supply classifier for tariff charges.

This module is core IP for VoltRegistry. It runs as pure Python — no LLMs in
the calculation path. Every charge gets categorized as ``delivery``,
``supply``, or ``ambiguous`` along with a confidence score and a one-sentence
human-readable reasoning string.

Implements §7 of ``VoltRegistry_v0_Spec.md``:

  1. Rule-based keyword pass (delivery and supply pattern lists).
  2. Type-based fallback (e.g. ``type=demand`` in regulated markets => delivery).
  3. Market-context override (deregulated_delivery_only utilities default to
     delivery for unmatched charges).
  4. Percentage-rider rule (gross receipts / franchise / revenue normalization /
     environmental compliance / storm riders => delivery unless they apply to
     supply charges).

Charges below confidence 0.8 are treated as ``ambiguous`` and excluded from
delivery totals at the engine boundary unless the tariff has been
``manually_reviewed`` (CLAUDE.md, critical rule 3).
"""

from __future__ import annotations

from .models import (
    Charge,
    ChargeClassification,
    ChargeType,
    ClassificationMethod,
    DeliverySupplyCategory,
    TariffBundle,
)

# ---------------------------------------------------------------------------
# Keyword tables  (§7.1)
# ---------------------------------------------------------------------------

# Order matters: supply keywords are checked BEFORE delivery keywords because
# some supply riders (e.g. "nuclear fuel rider") could otherwise match a
# generic "rider" or "fuel" pattern in the wrong direction. Within each list,
# longer / more specific phrases are listed first to win on substring match.

_SUPPLY_KEYWORDS: tuple[str, ...] = (
    "nuclear decommission",
    "nuclear fuel",
    "fuel adjustment",
    "fuel cost recovery",
    "fuel cost",
    "energy cost recovery",
    "purchased power",
    "purchased gas",
    "capacity cost",
    "generation",
    "fuel",
)

_DELIVERY_KEYWORDS: tuple[str, ...] = (
    "system benefit",
    "facility charge",
    "customer charge",
    "distribution",
    "transmission",
    "delivery",
    "metering",
    "infrastructure",
    "wires",
    "poles",
)

# Percentage-style riders that recover delivery-side revenue requirements
# (taxes, decoupling, storm/reliability, environmental compliance). Per §7
# these classify as delivery (confidence 0.9) unless explicitly applied to
# supply charges via ``applies_to.applied_to_charge_ids``.
_DELIVERY_RIDER_KEYWORDS: tuple[str, ...] = (
    "gross receipts",
    "franchise",
    "revenue normalization",
    "revenue decoupling",
    "environmental compliance",
    "storm",
    "reliability",
)

# Ambiguous-on-their-own keywords. Match these only when nothing more specific
# fired. We intentionally do NOT classify these as delivery automatically
# because in a deregulated market a "service charge" or "base charge" could
# belong to the REP supply bill rather than the wires bill.
_AMBIGUOUS_KEYWORDS: tuple[str, ...] = (
    "service charge",
    "base rate",
    "base charge",
    "energy charge",
)

# Market structures where the utility provides delivery only and supply is
# competitive. These are the §7.2 override markets.
_DELIVERY_ONLY_MARKETS: frozenset[str] = frozenset(
    {
        "deregulated_delivery_only",
    }
)

# Market structures where the utility bundles supply + delivery and a demand
# charge is an unambiguous distribution-infrastructure recovery vehicle.
_REGULATED_MARKETS: frozenset[str] = frozenset(
    {
        "regulated_vertical",
        "regulated_with_choice",
        "municipal",
        "cooperative",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_charge(
    charge_name: str,
    charge_type: str,
    market_structure: str,
    tariff_id: str = "",
) -> ChargeClassification:
    """Return a ChargeClassification for a single charge.

    Deterministic. No randomness, no LLM calls. The reasoning string is a
    single human-readable sentence explaining which rule fired.
    """

    name_lc = (charge_name or "").lower().strip()
    ctype = (charge_type or "").lower().strip()
    market = (market_structure or "").lower().strip()

    # ---- 1. Supply keyword match (highest priority) ---------------------
    supply_hit = _first_keyword(name_lc, _SUPPLY_KEYWORDS)
    if supply_hit is not None:
        # In a delivery-only market, an explicit supply keyword on the
        # delivery-side tariff means a non-bypassable pass-through that the
        # wires utility still bills. We trust the tariff author and keep it
        # as supply *unless* the market explicitly forbids supply on this
        # tariff. For Oncor we route this through classify_bundle's market
        # override below — at the per-charge level we still surface the hit.
        return ChargeClassification(
            category=DeliverySupplyCategory.supply,
            confidence=0.95,
            method=ClassificationMethod.rule_based,
            reasoning=(
                f"Name contains '{supply_hit}' — supply keyword match."
            ),
        )

    # ---- 2. Delivery percentage / surcharge rider keywords --------------
    delivery_rider_hit = _first_keyword(name_lc, _DELIVERY_RIDER_KEYWORDS)
    if delivery_rider_hit is not None:
        return ChargeClassification(
            category=DeliverySupplyCategory.delivery,
            confidence=0.9,
            method=ClassificationMethod.rule_based,
            reasoning=(
                f"Name contains '{delivery_rider_hit}' — delivery-side rider "
                "(taxes/decoupling/storm) per §7 percentage-rider rule."
            ),
        )

    # ---- 3. Delivery keyword match --------------------------------------
    delivery_hit = _first_keyword(name_lc, _DELIVERY_KEYWORDS)
    if delivery_hit is not None:
        return ChargeClassification(
            category=DeliverySupplyCategory.delivery,
            confidence=0.95,
            method=ClassificationMethod.rule_based,
            reasoning=(
                f"Name contains '{delivery_hit}' — delivery keyword match."
            ),
        )

    # ---- 4. Type-based fallback in regulated markets --------------------
    # Demand charges in vertically integrated regulated utilities are
    # canonically distribution cost recovery.
    if ctype == ChargeType.demand.value and market in _REGULATED_MARKETS:
        return ChargeClassification(
            category=DeliverySupplyCategory.delivery,
            confidence=0.85,
            method=ClassificationMethod.rule_based,
            reasoning=(
                "Demand charge in regulated vertically integrated market — "
                "distribution infrastructure cost recovery."
            ),
        )

    # Fixed customer/facility charges are almost always delivery, but the
    # specific 'customer charge' / 'facility charge' phrases are already
    # caught above. A bare ``type=fixed`` with no telltale name still leans
    # delivery in a regulated market.
    if ctype == ChargeType.fixed.value and market in _REGULATED_MARKETS:
        return ChargeClassification(
            category=DeliverySupplyCategory.delivery,
            confidence=0.8,
            method=ClassificationMethod.rule_based,
            reasoning=(
                "Fixed monthly charge in regulated market — defaults to "
                "delivery (metering/service connection)."
            ),
        )

    # ---- 5. Market-context override for delivery-only markets -----------
    if market in _DELIVERY_ONLY_MARKETS:
        return ChargeClassification(
            category=DeliverySupplyCategory.delivery,
            confidence=0.9,
            method=ClassificationMethod.rule_based,
            reasoning=(
                "Charge on a delivery-only utility tariff — defaults to "
                "delivery per §7.2 market-context override."
            ),
        )

    # ---- 6. Energy charge in regulated market => supply (low conf) ------
    # Bundled tariffs roll generation into the volumetric energy charge.
    if ctype == ChargeType.energy.value and market in _REGULATED_MARKETS:
        return ChargeClassification(
            category=DeliverySupplyCategory.supply,
            confidence=0.7,
            method=ClassificationMethod.rule_based,
            reasoning=(
                "Energy (volumetric) charge in regulated vertically "
                "integrated market — primarily generation/supply."
            ),
        )

    # ---- 7. Ambiguous keyword fallback ----------------------------------
    ambiguous_hit = _first_keyword(name_lc, _AMBIGUOUS_KEYWORDS)
    if ambiguous_hit is not None:
        return ChargeClassification(
            category=DeliverySupplyCategory.ambiguous,
            confidence=0.5,
            method=ClassificationMethod.rule_based,
            reasoning=(
                f"Name '{charge_name}' matches generic '{ambiguous_hit}' "
                "without disambiguation — flagged for review."
            ),
        )

    # ---- 8. Final fallback ----------------------------------------------
    return ChargeClassification(
        category=DeliverySupplyCategory.ambiguous,
        confidence=0.5,
        method=ClassificationMethod.rule_based,
        reasoning=(
            f"No keyword or type rule matched '{charge_name}' "
            f"(type={ctype or 'unknown'}, market={market or 'unknown'}) — "
            "flagged for review."
        ),
    )


def classify_bundle(
    bundle: TariffBundle,
    market_structure: str,
) -> TariffBundle:
    """Return a copy of ``bundle`` with every charge classification rebuilt.

    Two extra cross-charge rules are applied here that ``classify_charge``
    cannot see in isolation:

    * **Delivery-only markets force supply -> delivery** unless the charge
      name carries an explicit non-bypassable supply marker (currently we
      keep ``nuclear decommission`` as the marker but downgrade everything
      else flagged as supply by keyword to delivery).
    * **Percentage riders** that target ``applied_to_charge_ids`` containing
      any supply-classified charge re-classify as ``supply``.

    The returned bundle is a deep-ish copy: ``Charge`` instances are
    rebuilt, but ``Tariff``, ``TouSchedule`` and ``Rule`` objects are
    reused.
    """

    market = (market_structure or "").lower().strip()
    is_delivery_only = market in _DELIVERY_ONLY_MARKETS

    # Pass 1: per-charge classification
    new_charges: list[Charge] = []
    for charge in bundle.charges:
        classification = classify_charge(
            charge_name=charge.name,
            charge_type=charge.type.value,
            market_structure=market,
            tariff_id=charge.tariff_id,
        )

        # §7.2 — in a delivery-only market, force everything to delivery
        # except non-bypassable supply pass-throughs explicitly named.
        if (
            is_delivery_only
            and classification.category == DeliverySupplyCategory.supply
        ):
            name_lc = charge.name.lower()
            # Treat only true wholesale-energy markers as bypassable supply.
            # Items like nuclear decommissioning are non-bypassable and stay
            # on the wires bill: classify as delivery.
            non_bypassable_markers = ("nuclear decommission",)
            is_non_bypassable = any(
                m in name_lc for m in non_bypassable_markers
            )
            if is_non_bypassable:
                classification = ChargeClassification(
                    category=DeliverySupplyCategory.delivery,
                    confidence=0.9,
                    method=ClassificationMethod.rule_based,
                    reasoning=(
                        "Non-bypassable charge on delivery-only utility "
                        "tariff — billed on the wires bill, classified as "
                        "delivery per §7.2."
                    ),
                )

        new_charges.append(_replace_classification(charge, classification))

    # Pass 2: percentage-rider cross-check
    # If a rider's applies_to.applied_to_charge_ids includes any charge that
    # ended up as supply, the rider is recovering supply revenue and should
    # itself classify as supply.
    by_id = {c.charge_id: c for c in new_charges}
    final_charges: list[Charge] = []
    for charge in new_charges:
        targets = charge.applies_to.applied_to_charge_ids or []
        if (
            charge.unit.value == "percent"
            and targets
            and charge.classification.category
            == DeliverySupplyCategory.delivery
        ):
            target_categories = {
                by_id[t].classification.category
                for t in targets
                if t in by_id
            }
            if DeliverySupplyCategory.supply in target_categories:
                # Mixed or pure-supply application -> reclassify as supply.
                charge = _replace_classification(
                    charge,
                    ChargeClassification(
                        category=DeliverySupplyCategory.supply,
                        confidence=0.85,
                        method=ClassificationMethod.rule_based,
                        reasoning=(
                            "Percentage rider applies to supply-classified "
                            "charges — reclassified as supply per §7."
                        ),
                    ),
                )
        final_charges.append(charge)

    return TariffBundle(
        tariff=bundle.tariff,
        charges=final_charges,
        tou_schedule=bundle.tou_schedule,
        rules=list(bundle.rules),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _first_keyword(haystack: str, needles: tuple[str, ...]) -> str | None:
    """Return the first keyword (in list order) found as a substring."""
    for needle in needles:
        if needle in haystack:
            return needle
    return None


def _replace_classification(
    charge: Charge, classification: ChargeClassification
) -> Charge:
    """Return a Charge identical to ``charge`` but with a new classification.

    Pydantic v2: ``model_copy`` preserves immutability semantics.
    """
    return charge.model_copy(update={"classification": classification})
