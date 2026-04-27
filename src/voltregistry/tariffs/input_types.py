"""Standardized billing input taxonomy for large C&I tariff rate calculators.

Every rate schedule in the system is tagged with a subset of these 15 types.
This is the canonical vocabulary used by both the extraction pipeline and the
comparison engine to understand what billing determinants a tariff requires.
"""

from enum import Enum


class TariffInputType(str, Enum):
    monthly_kwh = "monthly_kwh"
    billing_demand_kw = "billing_demand_kw"
    onpeak_demand_kw = "onpeak_demand_kw"
    offpeak_demand_kw = "offpeak_demand_kw"
    onpeak_kwh = "onpeak_kwh"
    offpeak_kwh = "offpeak_kwh"
    shoulder_kwh = "shoulder_kwh"
    coincident_peak_kw = "coincident_peak_kw"
    reactive_demand_kvar = "reactive_demand_kvar"
    power_factor_pct = "power_factor_pct"
    contract_demand_kw = "contract_demand_kw"
    ratchet_demand_kw = "ratchet_demand_kw"
    voltage_level = "voltage_level"
    load_factor_pct = "load_factor_pct"
    billing_period_days = "billing_period_days"


INPUT_TYPE_DESCRIPTIONS: dict[str, str] = {
    TariffInputType.monthly_kwh: "Total energy consumed in the billing period (kWh)",
    TariffInputType.billing_demand_kw: "Peak 15- or 30-minute demand in the billing period (kW)",
    TariffInputType.onpeak_demand_kw: "Peak demand during defined on-peak hours (kW)",
    TariffInputType.offpeak_demand_kw: "Peak demand during off-peak hours (kW)",
    TariffInputType.onpeak_kwh: "Energy consumed during on-peak hours (kWh)",
    TariffInputType.offpeak_kwh: "Energy consumed during off-peak hours (kWh)",
    TariffInputType.shoulder_kwh: "Energy consumed during shoulder/mid-peak hours (kWh)",
    TariffInputType.coincident_peak_kw: "Customer demand at the utility's system peak hour (kW)",
    TariffInputType.reactive_demand_kvar: "Reactive power demand (kVAR)",
    TariffInputType.power_factor_pct: "Average power factor percentage",
    TariffInputType.contract_demand_kw: "Contractually reserved capacity (kW)",
    TariffInputType.ratchet_demand_kw: "Highest demand across current and trailing N months (kW)",
    TariffInputType.voltage_level: "Delivery voltage tier (transmission/subtransmission/primary/secondary)",
    TariffInputType.load_factor_pct: "Monthly load factor percentage (kWh / (peak_kW × hours))",
    TariffInputType.billing_period_days: "Number of days in the billing period",
}

VALID_INPUT_TYPES: set[str] = {t.value for t in TariffInputType}
