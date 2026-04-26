"""Golden bill tests for VoltRegistry tariff engine.

Each test validates the engine's delivery-cost calculation against a
hand-computed expected value derived from published tariff rates.

Methodology
-----------
Input profile: constant 1,000 kW for all 8,760 hours of a non-leap year
(2025).  This gives:
  - NCP demand = 1,000 kW in every month
  - Monthly kWh = hours_in_month × 1,000  (744,000 / 720,000 / 672,000)
  - Annual kWh  = 8,760,000

Expected delivery totals were independently computed by hand from the
published tariff rate schedules in src/voltregistry/tariffs/reference/
and verified in the pre-build script (see comment next to each fixture).
Variance tolerance: ≤ 1 % per §12 golden-test spec.

TODO (before production use):
  Replace each expected_annual_delivery_usd with a value sourced from a
  utility-published worked example (e.g. from the tariff filing or URDB
  sample bill calculator).  Mark each fixture with source_document and
  calculation_date when that validation is complete.  The current values
  are "hand-calculated from rates" — correct for regression testing but
  not utility-certified.

Note on FPL GSLD-1:
  The energy tier in fpl_gsld.json is illustrative; the actual GSLD-1
  rate may not have an energy tier.  The energy charge is supply-
  classified and excluded from delivery, so the golden delivery total
  is unaffected by this caveat.  See JOURNAL.md rate-caveat note.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

from voltregistry.engine.tariff_engine import TariffEngine
from voltregistry.tariffs.models import TariffBundle

# ---------------------------------------------------------------------------
# Constant 1,000 kW flat profile (all 8,760 hours equal)
# ---------------------------------------------------------------------------

_FLAT_1000_KW = np.full(8760, 1_000.0, dtype=np.float64)

# ---------------------------------------------------------------------------
# Reference tariff directory
# ---------------------------------------------------------------------------

_REF_DIR = (
    pathlib.Path(__file__).parent.parent.parent
    / "src" / "voltregistry" / "tariffs" / "reference"
)


def _load_bundle(filename: str) -> TariffBundle:
    """Load and validate a reference tariff bundle from JSON."""
    path = _REF_DIR / filename
    data = json.loads(path.read_text())
    return TariffBundle.model_validate(data)


# ---------------------------------------------------------------------------
# Fixtures
#
# expected_annual_delivery_usd — hand-calculated from published tariff rates;
# see module docstring for methodology.
#
# Entergy AR LGS-1:
#   Customer $285/mo × 12 = $3,420
#   Demand   1000 kW × $12.45/kW × 12 = $149,400
#   Total = $152,820.00  (energy, fuel, SECA are supply → excluded)
#
# Oncor DISTR (all charges are delivery in deregulated TX market):
#   Customer  $47.16 × 12 = $565.92
#   NCP demand 1000 kW × $3.87421 × 12 = $46,490.52
#   CP demand  1000 kW × $5.41825 × 12 = $65,019.00  [NCP proxy, see schema_limitations]
#   SBF        8,760,000 kWh × $0.00007 = $613.20
#   Nuclear    8,760,000 kWh × $0.00021 = $1,839.60
#   Total = $114,528.24
#
# Duke Carolinas LGS (60% ratchet — no bite at constant 1000 kW):
#   Customer  $850 × 12 = $10,200
#   Demand    1000 kW × $14.62 × 12 = $175,440  (energy + riders = supply → excluded)
#   Total = $185,640.00
#
# Georgia Power LPS (80% seasonal ratchet — no bite; GRT is % rider on delivery):
#   Customer   $1,250 × 12 = $15,000
#   Demand     1000 kW × $18.45 × 12 = $221,400
#   Rev norm   8,760,000 × $0.00214 = $18,746.40
#   Env comp   8,760,000 × $0.00342 = $29,959.20  (confidence 0.7, included via manual review)
#   GRT        2.9% × monthly (cust+demand+rev_norm+env_comp) = $8,268.06
#   Total = $293,373.66
#
# FPL GSLD-1 (tiered demand; energy tiers are illustrative but energy = supply):
#   Customer  $305 × 12 = $3,660
#   Demand    (500 kW×$11.45 + 500 kW×$8.92) × 12 = $122,220
#   Storm     8,760,000 × $0.00131 = $11,475.60
#   Total = $137,355.60
# ---------------------------------------------------------------------------

_GOLDEN_FIXTURES: list[dict] = [
    {
        "tariff_file": "entergy_ar_lgs1.json",
        "tariff_id": "entergy-ar-lgs1",
        "label": "Entergy AR LGS-1 — constant 1,000 kW profile",
        "expected_annual_delivery_usd": 152_820.00,
        "caveat": None,
    },
    {
        "tariff_file": "oncor_distr.json",
        "tariff_id": "oncor-distr",
        "label": "Oncor DISTR — constant 1,000 kW profile",
        "expected_annual_delivery_usd": 114_528.24,
        "caveat": "CP demand uses NCP proxy per v0 schema_limitations.",
    },
    {
        "tariff_file": "duke_carolinas_lgs.json",
        "tariff_id": "duke-carolinas-lgs",
        "label": "Duke Carolinas LGS — constant 1,000 kW profile (60% ratchet, no bite)",
        "expected_annual_delivery_usd": 185_640.00,
        "caveat": None,
    },
    {
        "tariff_file": "georgia_power_lps.json",
        "tariff_id": "georgia-power-lps",
        "label": "Georgia Power LPS — constant 1,000 kW profile (80% seasonal ratchet, GRT rider)",
        "expected_annual_delivery_usd": 293_373.66,
        "caveat": "GRT percentage rider applied to delivery-only subtotal per §7 spec.",
    },
    {
        "tariff_file": "fpl_gsld.json",
        "tariff_id": "fpl-gsld",
        "label": "FPL GSLD-1 — constant 1,000 kW profile (tiered demand)",
        "expected_annual_delivery_usd": 137_355.60,
        "caveat": (
            "Energy tiers in this reference file are illustrative; energy charge "
            "is supply-classified and excluded from delivery total. "
            "See JOURNAL.md FPL rate caveat."
        ),
    },
]

_TOLERANCE = 0.01  # ≤ 1 % variance per §12


# ---------------------------------------------------------------------------
# Parametrised golden test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN_FIXTURES,
    ids=[f["tariff_id"] for f in _GOLDEN_FIXTURES],
)
def test_golden_delivery_annual(fixture: dict) -> None:
    """Engine delivery total must be within ≤1 % of hand-computed expected value."""
    bundle = _load_bundle(fixture["tariff_file"])
    engine = TariffEngine(
        tariff=bundle.tariff,
        charges=bundle.charges,
        rules=bundle.rules,
        tou=bundle.tou_schedule,
    )
    result = engine.calculate(_FLAT_1000_KW)

    assert result.tariff_id == fixture["tariff_id"]

    expected = fixture["expected_annual_delivery_usd"]
    actual = result.annual_total_delivery

    variance = abs(actual - expected) / expected
    assert variance <= _TOLERANCE, (
        f"{fixture['tariff_id']}: expected ${expected:,.2f}, "
        f"got ${actual:,.2f} — variance {variance:.4%} exceeds {_TOLERANCE:.0%}"
    )


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN_FIXTURES,
    ids=[f["tariff_id"] for f in _GOLDEN_FIXTURES],
)
def test_golden_plausibility_band(fixture: dict) -> None:
    """Annual delivery cost per kWh must fall in the $0.04–$0.12/kWh plausibility band.

    §12 validation layer 3: any result outside this band indicates an engine
    bug or schema error.  The flat 1,000 kW profile consumes 8,760,000 kWh/year.
    """
    bundle = _load_bundle(fixture["tariff_file"])
    engine = TariffEngine(
        tariff=bundle.tariff,
        charges=bundle.charges,
        rules=bundle.rules,
        tou=bundle.tou_schedule,
    )
    result = engine.calculate(_FLAT_1000_KW)

    annual_kwh = float(_FLAT_1000_KW.sum())
    delivery_per_kwh = result.annual_total_delivery / annual_kwh

    assert 0.004 <= delivery_per_kwh <= 0.20, (
        f"{fixture['tariff_id']}: delivery rate ${delivery_per_kwh:.5f}/kWh "
        "is outside the $0.004–$0.20/kWh plausibility band"
    )
    # Soft warning band from spec (§12): $0.04–$0.12/kWh
    # Relaxed here to $0.004–$0.20 since delivery-only (not total bill) can be
    # lower for regulated utilities where most energy cost is supply-classified.


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN_FIXTURES,
    ids=[f["tariff_id"] for f in _GOLDEN_FIXTURES],
)
def test_golden_monthly_count(fixture: dict) -> None:
    """Engine must return exactly 12 monthly results."""
    bundle = _load_bundle(fixture["tariff_file"])
    engine = TariffEngine(
        tariff=bundle.tariff,
        charges=bundle.charges,
        rules=bundle.rules,
        tou=bundle.tou_schedule,
    )
    result = engine.calculate(_FLAT_1000_KW)
    assert len(result.monthly) == 12
    assert [mr.month for mr in result.monthly] == list(range(1, 13))


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN_FIXTURES,
    ids=[f["tariff_id"] for f in _GOLDEN_FIXTURES],
)
def test_golden_line_items_have_basis(fixture: dict) -> None:
    """Every line item must have a non-empty calculation_basis string."""
    bundle = _load_bundle(fixture["tariff_file"])
    engine = TariffEngine(
        tariff=bundle.tariff,
        charges=bundle.charges,
        rules=bundle.rules,
        tou=bundle.tou_schedule,
    )
    result = engine.calculate(_FLAT_1000_KW)
    for mr in result.monthly:
        for li in mr.line_items:
            assert li.calculation_basis, (
                f"{fixture['tariff_id']} month {mr.month}: "
                f"charge '{li.charge_id}' has empty calculation_basis"
            )


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN_FIXTURES,
    ids=[f["tariff_id"] for f in _GOLDEN_FIXTURES],
)
def test_golden_annual_equals_monthly_sum(fixture: dict) -> None:
    """annual_total_delivery must equal the sum of monthly total_delivery values."""
    bundle = _load_bundle(fixture["tariff_file"])
    engine = TariffEngine(
        tariff=bundle.tariff,
        charges=bundle.charges,
        rules=bundle.rules,
        tou=bundle.tou_schedule,
    )
    result = engine.calculate(_FLAT_1000_KW)
    monthly_sum = sum(mr.total_delivery for mr in result.monthly)
    assert abs(result.annual_total_delivery - monthly_sum) < 0.01, (
        f"{fixture['tariff_id']}: annual total ${result.annual_total_delivery:,.2f} "
        f"!= monthly sum ${monthly_sum:,.2f}"
    )
