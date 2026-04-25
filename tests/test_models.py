"""Unit tests for Pydantic v2 schema models."""

from voltregistry.tariffs.models import (
    Brand,
    Charge,
    ChargeClassification,
    ChargeType,
    ChargeUnit,
    ClassificationMethod,
    DeliverySupplyCategory,
    MarketStructure,
    Site,
    TariffEligibility,
    Utility,
    VoltageLevel,
)


def test_charge_model_round_trip():
    """Charge serialises and deserialises cleanly."""
    c = Charge(
        charge_id="c1",
        tariff_id="t1",
        name="Distribution Facilities Charge",
        type=ChargeType.demand,
        unit=ChargeUnit.dollar_per_kw,
        value=12.50,
        classification=ChargeClassification(
            category=DeliverySupplyCategory.delivery,
            confidence=0.95,
            method=ClassificationMethod.rule_based,
            reasoning="name contains 'distribution'",
        ),
    )
    data = c.model_dump()
    c2 = Charge.model_validate(data)
    assert c2.charge_id == "c1"
    assert c2.classification.confidence == 0.95


def test_tariff_eligibility_defaults():
    """TariffEligibility initialises cleanly with all-None optional fields."""
    e = TariffEligibility()
    assert e.min_kw is None
    assert e.voltage_required == []
    assert e.customer_classes == []


def test_site_model():
    """Site validates required fields."""
    s = Site(
        site_id="WMT-0001",
        brand=Brand.walmart,
        store_number="0001",
        address="1 Main St",
        city="Bentonville",
        state="AR",
        lat=36.37,
        lng=-94.21,
        last_updated="2026-04-25T00:00:00Z",
    )
    assert s.utility_eia_id is None
    assert s.voltage_level == VoltageLevel.secondary


def test_utility_model():
    """Utility validates market structure enum."""
    u = Utility(
        eia_id="40229",
        name="Oncor Electric Delivery",
        state="TX",
        regulatory_jurisdiction="state_puc",
        market_structure=MarketStructure.deregulated_delivery_only,
    )
    assert u.market_structure == MarketStructure.deregulated_delivery_only
