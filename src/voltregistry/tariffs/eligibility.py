"""Eligibility engine for VoltRegistry.

Implements §8 of VoltRegistry_v0_Spec.md verbatim.

Hard exclusions (make a tariff ineligible):
  - tariff.availability == "closed_to_new"
  - tariff.end_date < today
  - site.voltage_level not in tariff.eligibility.voltage_required
  - site.estimated_peak_kw < tariff.eligibility.min_kw
  - site.estimated_peak_kw > tariff.eligibility.max_kw
  - site.customer_class not in tariff.eligibility.customer_classes

Soft warnings (flagged but not exclusionary):
  - load_factor_min present
  - term_commitment_months present
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from voltregistry.tariffs.models import TariffAvailability, TariffEligibility


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_eligibility(
    *,
    voltage_level: str,
    estimated_peak_kw: float | None,
    customer_class: str,
    availability: str,
    eligibility: TariffEligibility,
    end_date: str | None,
    today: date | None = None,
) -> EligibilityResult:
    """Return eligibility for a (site, tariff) pair.

    Args:
        voltage_level:      site.voltage_level ("secondary" | "primary" | "transmission")
        estimated_peak_kw:  site peak demand in kW, or None if unknown
        customer_class:     site customer class (e.g. "commercial")
        availability:       tariff.availability value
        eligibility:        TariffEligibility block from the tariff
        end_date:           tariff.end_date (YYYY-MM-DD) or None
        today:              override date.today() for testing
    """
    if today is None:
        today = date.today()

    reasons: list[str] = []
    warnings: list[str] = []

    # 1. Availability
    if availability == TariffAvailability.closed_to_new.value:
        reasons.append("Tariff is closed to new customers.")

    # 2. End date
    if end_date:
        try:
            if date.fromisoformat(end_date) < today:
                reasons.append(f"Tariff expired on {end_date}.")
        except ValueError:
            pass

    # 3. Voltage
    if eligibility.voltage_required:
        allowed = [v.value for v in eligibility.voltage_required]
        if voltage_level not in allowed:
            reasons.append(
                f"Site voltage '{voltage_level}' not in required voltages {allowed}."
            )

    # 4. Min kW
    if eligibility.min_kw is not None:
        if estimated_peak_kw is None:
            warnings.append(
                f"Site has no estimated peak kW; cannot verify minimum "
                f"requirement of {eligibility.min_kw:.0f} kW."
            )
        elif estimated_peak_kw < eligibility.min_kw:
            reasons.append(
                f"Site peak {estimated_peak_kw:.0f} kW is below tariff "
                f"minimum {eligibility.min_kw:.0f} kW."
            )

    # 5. Max kW
    if eligibility.max_kw is not None:
        if estimated_peak_kw is None:
            warnings.append(
                f"Site has no estimated peak kW; cannot verify maximum "
                f"requirement of {eligibility.max_kw:.0f} kW."
            )
        elif estimated_peak_kw > eligibility.max_kw:
            reasons.append(
                f"Site peak {estimated_peak_kw:.0f} kW exceeds tariff "
                f"maximum {eligibility.max_kw:.0f} kW."
            )

    # 6. Customer class
    if eligibility.customer_classes and customer_class not in eligibility.customer_classes:
        reasons.append(
            f"Customer class '{customer_class}' not in tariff classes "
            f"{eligibility.customer_classes}."
        )

    # Soft warnings
    if eligibility.load_factor_min is not None:
        warnings.append(
            f"Tariff requires minimum load factor of "
            f"{eligibility.load_factor_min * 100:.0f}%. Verify before enrollment."
        )

    if eligibility.term_commitment_months is not None:
        warnings.append(
            f"Tariff requires a {eligibility.term_commitment_months}-month "
            "term commitment."
        )

    return EligibilityResult(
        eligible=len(reasons) == 0,
        reasons=reasons,
        warnings=warnings,
    )
