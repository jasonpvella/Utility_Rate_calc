"""Unit tests for the eligibility engine (§8 of VoltRegistry_v0_Spec.md)."""

from datetime import date

from voltregistry.tariffs.eligibility import EligibilityResult, check_eligibility
from voltregistry.tariffs.models import TariffEligibility, VoltageLevel


def _basic_eligibility(**overrides) -> TariffEligibility:
    defaults = dict(
        min_kw=500.0,
        max_kw=None,
        voltage_required=[VoltageLevel.secondary, VoltageLevel.primary],
        customer_classes=["commercial", "industrial"],
        load_factor_min=None,
        term_commitment_months=None,
    )
    defaults.update(overrides)
    return TariffEligibility(**defaults)


def _check(**overrides) -> EligibilityResult:
    defaults = dict(
        voltage_level="secondary",
        estimated_peak_kw=1000.0,
        customer_class="commercial",
        availability="optional",
        eligibility=_basic_eligibility(),
        end_date=None,
        today=date(2026, 4, 26),
    )
    defaults.update(overrides)
    return check_eligibility(**defaults)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_eligible_all_checks_pass():
    result = _check()
    assert result.eligible is True
    assert result.reasons == []


def test_eligible_no_constraints():
    result = _check(eligibility=TariffEligibility())
    assert result.eligible is True


# ---------------------------------------------------------------------------
# Hard exclusions
# ---------------------------------------------------------------------------


def test_ineligible_closed_to_new():
    result = _check(availability="closed_to_new")
    assert result.eligible is False
    assert any("closed to new" in r for r in result.reasons)


def test_ineligible_expired():
    result = _check(end_date="2025-12-31")
    assert result.eligible is False
    assert any("expired" in r for r in result.reasons)


def test_not_ineligible_future_end_date():
    result = _check(end_date="2030-01-01")
    assert result.eligible is True


def test_ineligible_wrong_voltage():
    result = _check(
        voltage_level="transmission",
        eligibility=_basic_eligibility(
            voltage_required=[VoltageLevel.secondary, VoltageLevel.primary]
        ),
    )
    assert result.eligible is False
    assert any("transmission" in r for r in result.reasons)


def test_eligible_matching_voltage():
    result = _check(
        voltage_level="primary",
        eligibility=_basic_eligibility(
            voltage_required=[VoltageLevel.secondary, VoltageLevel.primary]
        ),
    )
    assert result.eligible is True


def test_ineligible_below_min_kw():
    result = _check(estimated_peak_kw=400.0)
    assert result.eligible is False
    assert any("400 kW is below" in r or "below" in r for r in result.reasons)


def test_eligible_at_min_kw():
    result = _check(estimated_peak_kw=500.0)
    assert result.eligible is True


def test_ineligible_above_max_kw():
    result = _check(
        estimated_peak_kw=2500.0,
        eligibility=_basic_eligibility(max_kw=2000.0),
    )
    assert result.eligible is False
    assert any("exceeds" in r for r in result.reasons)


def test_eligible_at_max_kw():
    result = _check(
        estimated_peak_kw=2000.0,
        eligibility=_basic_eligibility(max_kw=2000.0),
    )
    assert result.eligible is True


def test_ineligible_wrong_customer_class():
    result = _check(
        customer_class="residential",
        eligibility=_basic_eligibility(customer_classes=["commercial", "industrial"]),
    )
    assert result.eligible is False
    assert any("residential" in r for r in result.reasons)


def test_eligible_correct_customer_class():
    result = _check(customer_class="industrial")
    assert result.eligible is True


def test_eligible_empty_customer_class_list():
    result = _check(
        customer_class="residential",
        eligibility=_basic_eligibility(customer_classes=[]),
    )
    assert result.eligible is True


# ---------------------------------------------------------------------------
# Multiple failures accumulate
# ---------------------------------------------------------------------------


def test_multiple_reasons_accumulated():
    result = _check(
        availability="closed_to_new",
        end_date="2025-01-01",
        voltage_level="transmission",
        estimated_peak_kw=100.0,
    )
    assert result.eligible is False
    assert len(result.reasons) >= 3


# ---------------------------------------------------------------------------
# Soft warnings — do not affect eligibility
# ---------------------------------------------------------------------------


def test_warning_no_peak_kw_with_min_kw():
    result = _check(estimated_peak_kw=None)
    assert result.eligible is True
    assert any("estimated peak kW" in w for w in result.warnings)


def test_warning_no_peak_kw_with_max_kw():
    result = _check(
        estimated_peak_kw=None,
        eligibility=_basic_eligibility(min_kw=None, max_kw=2000.0),
    )
    assert result.eligible is True
    assert any("estimated peak kW" in w for w in result.warnings)


def test_warning_load_factor_min():
    result = _check(eligibility=_basic_eligibility(load_factor_min=0.6))
    assert result.eligible is True
    assert any("load factor" in w for w in result.warnings)


def test_warning_term_commitment():
    result = _check(eligibility=_basic_eligibility(term_commitment_months=12))
    assert result.eligible is True
    assert any("12-month" in w for w in result.warnings)


def test_warning_no_voltage_required_skips_check():
    result = _check(
        voltage_level="transmission",
        eligibility=_basic_eligibility(voltage_required=[]),
    )
    assert result.eligible is True


# ---------------------------------------------------------------------------
# Georgia Power LPS reference case: transmission-only tariff
# ---------------------------------------------------------------------------


def test_georgia_power_lps_voltage_secondary_ineligible():
    result = _check(
        voltage_level="secondary",
        estimated_peak_kw=1000.0,
        eligibility=_basic_eligibility(
            min_kw=900.0,
            voltage_required=[VoltageLevel.primary, VoltageLevel.transmission],
        ),
    )
    assert result.eligible is False
    assert any("secondary" in r for r in result.reasons)


def test_georgia_power_lps_primary_eligible():
    result = _check(
        voltage_level="primary",
        estimated_peak_kw=1000.0,
        eligibility=_basic_eligibility(
            min_kw=900.0,
            voltage_required=[VoltageLevel.primary, VoltageLevel.transmission],
        ),
    )
    assert result.eligible is True
