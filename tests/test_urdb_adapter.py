"""Unit tests for the URDB → TariffBundle adapter.

All tests use synthetic URDB fixtures — no network access required.
The fixtures are designed to represent the most common URDB tariff patterns
encountered for large-commercial accounts.

Fixtures
--------
FLAT_RATE_RAW     Simple flat energy + flat demand, no TOU.
                  Represents a small IOU commercial tariff with minimal
                  structure (e.g. many rural cooperative rates).

TOU_SUMMER_RAW    TOU energy (summer on/off-peak, flat winter) + NCP demand
                  + minimum monthly charge.
                  Represents the majority of large-commercial IOU tariffs.

TIERED_DEMAND_RAW TOU energy (same as above) + tiered NCP demand with
                  explicit tier bounds.

DEREGULATED_RAW   Flat energy + NCP demand for a delivery-only utility.
                  All charges should classify as delivery after the §7.2
                  market-context override.
"""

from __future__ import annotations

import pytest

from voltregistry.ingest.urdb_to_bundle import urdb_to_bundle
from voltregistry.tariffs.models import (
    ChargeType,
    ChargeUnit,
    DeliverySupplyCategory,
    DeliverySupplyReviewStatus,
    IngestionMethod,
    RuleType,
    TariffBundle,
)

# ---------------------------------------------------------------------------
# Synthetic URDB fixture helpers
# ---------------------------------------------------------------------------


def _flat_schedule() -> list[list[int]]:
    """12×24 matrix with all zeros (single period, no TOU)."""
    return [[0] * 24 for _ in range(12)]


def _tou_summer_wd_schedule() -> list[list[int]]:
    """12×24 weekday schedule:
    - Summer (Jun–Sep, months 5–8 zero-indexed): period 1 during hours 14–19,
      period 0 otherwise.
    - All other months: period 0 all day.
    """
    sched = []
    for m in range(12):
        row = [0] * 24
        if m in (5, 6, 7, 8):  # Jun–Sep (0-indexed)
            for h in range(14, 20):  # 2 pm–8 pm
                row[h] = 1
        sched.append(row)
    return sched


def _tou_summer_we_schedule() -> list[list[int]]:
    """Weekend: all period 0."""
    return [[0] * 24 for _ in range(12)]


# ---------------------------------------------------------------------------
# Synthetic URDB raw items
# ---------------------------------------------------------------------------

FLAT_RATE_RAW: dict = {
    "label": "test-flat-0001",
    "name": "General Service — Large",
    "utility": "Test Electric Cooperative",
    "eiaid": "99001",
    "sector": ["Commercial", "Industrial"],
    "startdate": 1704067200,  # 2024-01-01
    "enddate": None,
    "source": "https://example-utility.com/rates/large-general",
    "sourcetitle": "Large General Service Rate Schedule",
    "peakkwcapacitythreshold": 200,
    "fixedmonthlycharge": 350.0,
    "minmonthlycharge": None,
    "energyratestructure": [[{"rate": 0.045, "unit": "kWh"}]],
    "energyweekdayschedule": _flat_schedule(),
    "energyweekendschedule": _flat_schedule(),
    "demandratestructure": [[{"rate": 11.25, "unit": "kW"}]],
    "demandweekdayschedule": _flat_schedule(),
    "demandweekendschedule": _flat_schedule(),
    "flatdemandstructure": None,
    "flatdemandmonths": None,
    "coincidentratestructure": None,
}

TOU_SUMMER_RAW: dict = {
    "label": "test-tou-0002",
    "name": "Large Power Time-of-Use",
    "utility": "Test IOU",
    "eiaid": "99002",
    "sector": "Commercial",
    "startdate": 1704067200,
    "enddate": None,
    "source": "https://example-iou.com/rates/lp-tou",
    "sourcetitle": "LP-TOU Rate Schedule",
    "peakkwcapacitythreshold": 500,
    "fixedmonthlycharge": 900.0,
    "minmonthlycharge": 1500.0,
    # Period 0 = off-peak (0.03 $/kWh), Period 1 = on-peak (0.07 $/kWh)
    "energyratestructure": [
        [{"rate": 0.030, "unit": "kWh"}],
        [{"rate": 0.070, "unit": "kWh"}],
    ],
    "energyweekdayschedule": _tou_summer_wd_schedule(),
    "energyweekendschedule": _tou_summer_we_schedule(),
    "demandratestructure": [[{"rate": 15.50, "unit": "kW"}]],
    "demandweekdayschedule": _flat_schedule(),
    "demandweekendschedule": _flat_schedule(),
    "flatdemandstructure": None,
    "flatdemandmonths": None,
    "coincidentratestructure": None,
}

TIERED_DEMAND_RAW: dict = {
    "label": "test-tiered-0003",
    "name": "Commercial Service Tiered Demand",
    "utility": "Test IOU Tiered",
    "eiaid": "99003",
    "sector": ["Commercial"],
    "startdate": 1704067200,
    "enddate": None,
    "source": "https://example-iou.com/rates/cs-tiered",
    "sourcetitle": "CS Tiered Rate",
    "peakkwcapacitythreshold": 100,
    "fixedmonthlycharge": 200.0,
    "minmonthlycharge": None,
    "energyratestructure": [[{"rate": 0.038, "unit": "kWh"}]],
    "energyweekdayschedule": _flat_schedule(),
    "energyweekendschedule": _flat_schedule(),
    # Tiered demand: 0–500 kW at $9.50, 500+ kW at $7.20
    "demandratestructure": [
        [
            {"rate": 9.50, "unit": "kW", "max": 500},
            {"rate": 7.20, "unit": "kW"},
        ]
    ],
    "demandweekdayschedule": _flat_schedule(),
    "demandweekendschedule": _flat_schedule(),
    "flatdemandstructure": None,
    "flatdemandmonths": None,
    "coincidentratestructure": None,
}

DEREGULATED_RAW: dict = {
    "label": "test-dereg-0004",
    "name": "Distribution Service — Commercial",
    "utility": "Test Wires Co",
    "eiaid": "99004",
    "sector": ["Commercial", "Industrial"],
    "startdate": 1704067200,
    "enddate": None,
    "source": "https://example-wires.com/rates/dist",
    "sourcetitle": "Distribution Service Rate",
    "peakkwcapacitythreshold": None,
    "fixedmonthlycharge": 50.0,
    "minmonthlycharge": None,
    "energyratestructure": [[{"rate": 0.010, "unit": "kWh"}]],
    "energyweekdayschedule": _flat_schedule(),
    "energyweekendschedule": _flat_schedule(),
    "demandratestructure": [[{"rate": 4.50, "unit": "kW"}]],
    "demandweekdayschedule": _flat_schedule(),
    "demandweekendschedule": _flat_schedule(),
    "flatdemandstructure": None,
    "flatdemandmonths": None,
    "coincidentratestructure": None,
}

RESIDENTIAL_RAW: dict = {
    "label": "test-res-0005",
    "name": "Residential Service",
    "utility": "Test IOU",
    "eiaid": "99005",
    "sector": "Residential",
    "fixedmonthlycharge": 10.0,
    "energyratestructure": [[{"rate": 0.12, "unit": "kWh"}]],
    "energyweekdayschedule": _flat_schedule(),
    "energyweekendschedule": _flat_schedule(),
}


# ---------------------------------------------------------------------------
# Basic conversion tests
# ---------------------------------------------------------------------------


class TestFlatRateTariff:
    def setup_method(self):
        self.bundle = urdb_to_bundle(FLAT_RATE_RAW, "99001")

    def test_returns_bundle(self):
        assert isinstance(self.bundle, TariffBundle)

    def test_tariff_id(self):
        assert self.bundle.tariff.tariff_id == "urdb-test-flat-0001"

    def test_urdb_id_preserved(self):
        assert self.bundle.tariff.urdb_id == "test-flat-0001"

    def test_ingestion_method(self):
        assert self.bundle.tariff.ingestion_method == IngestionMethod.urdb

    def test_review_status_auto_classified(self):
        assert (
            self.bundle.tariff.delivery_supply_review_status
            == DeliverySupplyReviewStatus.auto_classified
        )

    def test_has_fixed_charge(self):
        fixed = [c for c in self.bundle.charges if c.type == ChargeType.fixed]
        assert len(fixed) == 1
        assert fixed[0].value == pytest.approx(350.0)
        assert fixed[0].unit == ChargeUnit.dollar_per_month

    def test_has_energy_charge(self):
        energy = [c for c in self.bundle.charges if c.type == ChargeType.energy]
        assert len(energy) == 1
        assert energy[0].value == pytest.approx(0.045)

    def test_has_demand_charge(self):
        demand = [c for c in self.bundle.charges if c.type == ChargeType.demand]
        assert len(demand) == 1
        assert demand[0].value == pytest.approx(11.25)

    def test_no_tou_schedule(self):
        # Single-period flat schedule → no TOU
        assert self.bundle.tou_schedule is None

    def test_no_rules(self):
        assert self.bundle.rules == []

    def test_eligibility_min_kw(self):
        assert self.bundle.tariff.eligibility.min_kw == pytest.approx(200.0)

    def test_charges_classified(self):
        # Classifier must have run — no charge should remain at placeholder confidence
        for charge in self.bundle.charges:
            assert charge.classification.confidence > 0.5, (
                f"{charge.charge_id} still has placeholder classification"
            )

    def test_fixed_charge_is_delivery(self):
        fixed = next(c for c in self.bundle.charges if c.type == ChargeType.fixed)
        assert fixed.classification.category == DeliverySupplyCategory.delivery

    def test_demand_charge_is_delivery(self):
        demand = next(c for c in self.bundle.charges if c.type == ChargeType.demand)
        assert demand.classification.category == DeliverySupplyCategory.delivery


class TestTouSummerTariff:
    def setup_method(self):
        self.bundle = urdb_to_bundle(TOU_SUMMER_RAW, "99002")

    def test_returns_bundle(self):
        assert isinstance(self.bundle, TariffBundle)

    def test_has_tou_schedule(self):
        assert self.bundle.tou_schedule is not None

    def test_tou_has_on_and_off_peak(self):
        period_names = {p.name.value for p in self.bundle.tou_schedule.periods}
        assert "on_peak" in period_names
        assert "off_peak" in period_names

    def test_on_peak_limited_to_summer(self):
        on_peak = [
            p for p in self.bundle.tou_schedule.periods if p.name.value == "on_peak"
        ]
        for op in on_peak:
            if op.season_months:
                # on_peak should only appear in summer months (6,7,8,9)
                assert all(m in (6, 7, 8, 9) for m in op.season_months), (
                    f"on_peak period appears in non-summer months: {op.season_months}"
                )

    def test_on_peak_hours_correct(self):
        on_peak = next(
            (p for p in self.bundle.tou_schedule.periods if p.name.value == "on_peak"),
            None,
        )
        assert on_peak is not None
        # Our fixture has on-peak during hours 14–19 (end-exclusive 20)
        hour_starts = {hr.start for hr in on_peak.hour_ranges}
        assert 14 in hour_starts

    def test_has_fixed_charge(self):
        fixed = [c for c in self.bundle.charges if c.type == ChargeType.fixed]
        assert len(fixed) == 1
        assert fixed[0].value == pytest.approx(900.0)

    def test_minimum_charge_rule(self):
        min_rules = [r for r in self.bundle.rules if r.type == RuleType.minimum_charge]
        assert len(min_rules) == 1
        assert min_rules[0].parameters.minimum_monthly_charge == pytest.approx(1500.0)

    def test_energy_charges_season(self):
        energy = [c for c in self.bundle.charges if c.type == ChargeType.energy]
        # Should have both summer (on/off-peak) and non-summer (off-peak) charges
        # At minimum, all-season or summer charges should be present
        assert len(energy) >= 1


class TestTieredDemandTariff:
    def setup_method(self):
        self.bundle = urdb_to_bundle(TIERED_DEMAND_RAW, "99003")

    def test_returns_bundle(self):
        assert isinstance(self.bundle, TariffBundle)

    def test_demand_charge_is_tiered(self):
        demand = [c for c in self.bundle.charges if c.type == ChargeType.demand]
        assert len(demand) == 1
        assert demand[0].value is None  # tiered charges have value=None
        assert len(demand[0].tiers) == 2

    def test_tier_bounds_correct(self):
        demand = next(c for c in self.bundle.charges if c.type == ChargeType.demand)
        tiers = demand.tiers
        assert tiers[0].min == pytest.approx(0.0)
        assert tiers[0].max == pytest.approx(500.0)
        assert tiers[0].rate == pytest.approx(9.50)
        assert tiers[1].min == pytest.approx(500.0)
        assert tiers[1].max is None
        assert tiers[1].rate == pytest.approx(7.20)


class TestDeregulatedTariff:
    def setup_method(self):
        self.bundle = urdb_to_bundle(
            DEREGULATED_RAW, "99004",
            market_structure="deregulated_delivery_only"
        )

    def test_returns_bundle(self):
        assert isinstance(self.bundle, TariffBundle)

    def test_all_charges_delivery(self):
        # In a deregulated delivery-only market, §7.2 forces all charges to delivery
        for charge in self.bundle.charges:
            assert charge.classification.category == DeliverySupplyCategory.delivery, (
                f"{charge.charge_id} classified as {charge.classification.category.value} "
                "in a delivery-only market"
            )


# ---------------------------------------------------------------------------
# Edge-case and guard tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_residential_tariff_skipped(self):
        result = urdb_to_bundle(RESIDENTIAL_RAW, "99005")
        assert result is None

    def test_missing_label_returns_none(self):
        raw = {**FLAT_RATE_RAW, "label": None}
        result = urdb_to_bundle(raw, "99001")
        assert result is None

    def test_empty_raw_returns_none(self):
        result = urdb_to_bundle({}, "99001")
        assert result is None

    def test_zero_fixed_charge_excluded(self):
        raw = {**FLAT_RATE_RAW, "fixedmonthlycharge": 0.0}
        bundle = urdb_to_bundle(raw, "99001")
        assert bundle is not None
        fixed = [c for c in bundle.charges if c.type == ChargeType.fixed]
        assert len(fixed) == 0

    def test_null_energy_rate_excluded(self):
        raw = {**FLAT_RATE_RAW, "energyratestructure": None}
        bundle = urdb_to_bundle(raw, "99001")
        assert bundle is not None
        energy = [c for c in bundle.charges if c.type == ChargeType.energy]
        assert len(energy) == 0

    def test_charge_ids_match_tariff_charge_list(self):
        bundle = urdb_to_bundle(FLAT_RATE_RAW, "99001")
        charge_ids_in_list = set(bundle.tariff.charges)
        charge_ids_on_charges = {c.charge_id for c in bundle.charges}
        assert charge_ids_in_list == charge_ids_on_charges

    def test_tou_schedule_id_matches_tariff(self):
        bundle = urdb_to_bundle(TOU_SUMMER_RAW, "99002")
        assert bundle.tou_schedule is not None
        assert bundle.tariff.tou_schedule_id == bundle.tou_schedule.tou_schedule_id

    def test_no_tou_schedule_tariff_has_none_id(self):
        bundle = urdb_to_bundle(FLAT_RATE_RAW, "99001")
        assert bundle.tariff.tou_schedule_id is None

    def test_eia_id_assigned(self):
        bundle = urdb_to_bundle(FLAT_RATE_RAW, "99001")
        assert bundle.tariff.utility_eia_id == "99001"

    def test_all_charge_ids_are_unique(self):
        bundle = urdb_to_bundle(TOU_SUMMER_RAW, "99002")
        ids = [c.charge_id for c in bundle.charges]
        assert len(ids) == len(set(ids)), "Duplicate charge IDs detected"

    def test_bundle_validates_pydantic(self):
        """Confirm the output parses cleanly through Pydantic round-trip."""
        from voltregistry.tariffs.models import TariffBundle as TB
        bundle = urdb_to_bundle(TOU_SUMMER_RAW, "99002")
        assert bundle is not None
        json_str = bundle.model_dump_json()
        reloaded = TB.model_validate_json(json_str)
        assert reloaded.tariff.tariff_id == bundle.tariff.tariff_id
