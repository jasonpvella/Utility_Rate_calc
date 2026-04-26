"""Tariff rule implementations for the VoltRegistry engine.

Covers:
  - Demand ratchet  (§6.6 RuleType.demand_ratchet)
  - Minimum charge  (§6.6 RuleType.minimum_charge)

Math is deterministic Python/NumPy; no LLM logic here.

Ratchet semantics
-----------------
A demand ratchet ensures the utility recovers revenue even in months where
the customer's measured demand is low.  The billed demand in any month is
the greater of:
  (a) the actual NCP demand this month, or
  (b) ratchet_percent% of the highest demand seen within the ratchet window.

Two window types are supported:

  rolling   – highest demand in any of the preceding ratchet_window_months
              (or the full history if fewer months have elapsed)
  seasonal  – highest demand in the specified ratchet_source_months
              (summer months only, rolling over the prior 12 months)

For v0, the engine calculates the ratchet over the provided 12-month profile
only (i.e. no billing history from prior years).
"""

from __future__ import annotations

from voltregistry.tariffs.models import RatchetWindowType, Rule, RuleType


def apply_demand_ratchet(
    rule: Rule,
    monthly_ncp_kw: list[float],
) -> list[float]:
    """Return a list of 12 billed demand values (kW) after applying the ratchet.

    For each month, the billed demand is:
      max(actual_ncp_kw, ratchet_percent/100 × reference_peak_kw)

    where reference_peak_kw is determined by the window type.

    Args:
        rule:             A Rule with type == demand_ratchet.
        monthly_ncp_kw:   List of 12 actual monthly NCP demand values (kW).

    Returns:
        List of 12 billed demand values (kW).

    Raises:
        ValueError:  if rule.type is not demand_ratchet.
    """
    if rule.type != RuleType.demand_ratchet:
        raise ValueError(f"Expected demand_ratchet rule, got {rule.type!r}")

    params = rule.parameters
    ratchet_frac = (params.ratchet_percent or 0.0) / 100.0
    window_months = params.ratchet_window_months or 12
    window_type = params.ratchet_window_type or RatchetWindowType.rolling
    source_months = set(params.ratchet_source_months or [])

    billed = []

    for m_idx in range(12):
        actual = monthly_ncp_kw[m_idx]

        if window_type == RatchetWindowType.rolling:
            # Reference peak = max NCP in the past window_months (inclusive of
            # current month per utility convention for rolling ratchets).
            start_idx = max(0, m_idx - window_months + 1)
            reference_peak = max(monthly_ncp_kw[start_idx : m_idx + 1])

        elif window_type == RatchetWindowType.seasonal:
            # Reference peak = max NCP in the prior 12 months (or available
            # history) for the ratchet_source_months only.
            # Typically summer months, e.g. [6, 7, 8, 9] for Georgia Power.
            peaks_in_source: list[float] = []
            for i in range(12):  # look at all 12 months of the profile
                month_num = i + 1  # 1-indexed month
                if not source_months or month_num in source_months:
                    peaks_in_source.append(monthly_ncp_kw[i])
            reference_peak = max(peaks_in_source) if peaks_in_source else actual

        else:
            # contract_anniversary treated as rolling for v0
            start_idx = max(0, m_idx - window_months + 1)
            reference_peak = max(monthly_ncp_kw[start_idx : m_idx + 1])

        ratchet_floor = reference_peak * ratchet_frac
        billed.append(max(actual, ratchet_floor))

    return billed


def apply_minimum_charge(
    rule: Rule,
    monthly_totals: list[float],
) -> list[float]:
    """Apply a minimum monthly charge, returning adjusted totals.

    Args:
        rule:             A Rule with type == minimum_charge.
        monthly_totals:   List of 12 pre-ratchet monthly charge totals ($).

    Returns:
        List of 12 adjusted monthly totals ($).
    """
    if rule.type != RuleType.minimum_charge:
        raise ValueError(f"Expected minimum_charge rule, got {rule.type!r}")

    min_charge = rule.parameters.minimum_monthly_charge or 0.0
    return [max(t, min_charge) for t in monthly_totals]
