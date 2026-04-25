"""Pydantic v2 schema models for VoltRegistry tariff structures.

These implement the §6 data model from VoltRegistry_v0_Spec.md verbatim.
They are used for:
  - Validating tariff JSON files under tariffs/reference/
  - Serialising/deserialising tariff payloads stored in SQLite JSON columns
  - Input/output types for the engine and comparison APIs

URDB IDs are kept as foreign keys for traceability (§6, Rule 6).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Brand(str, Enum):
    walmart = "Walmart"
    sams_club = "SamsClub"


class VoltageLevel(str, Enum):
    secondary = "secondary"
    primary = "primary"
    transmission = "transmission"


class MarketStructure(str, Enum):
    regulated_vertical = "regulated_vertical"
    regulated_with_choice = "regulated_with_choice"
    deregulated_delivery_only = "deregulated_delivery_only"
    municipal = "municipal"
    cooperative = "cooperative"


class RegulatoryJurisdiction(str, Enum):
    state_puc = "state_puc"
    ferc = "ferc"
    municipal = "municipal"


class InputTier(str, Enum):
    tier_1_8760 = "TIER_1_8760"
    tier_2_peak_kw = "TIER_2_PEAK_KW"
    tier_3_volumetric = "TIER_3_VOLUMETRIC"


class TariffAvailability(str, Enum):
    mandatory = "mandatory"
    optional = "optional"
    closed_to_new = "closed_to_new"


class DeliverySupplyCategory(str, Enum):
    delivery = "delivery"
    supply = "supply"
    ambiguous = "ambiguous"


class ClassificationMethod(str, Enum):
    rule_based = "rule_based"
    manual = "manual"
    llm = "llm"


class DeliverySupplyReviewStatus(str, Enum):
    auto_classified = "auto_classified"
    manually_reviewed = "manually_reviewed"
    flagged = "flagged"


class IngestionMethod(str, Enum):
    urdb = "urdb"
    manual = "manual"
    llm_extracted = "llm_extracted"


class ChargeType(str, Enum):
    fixed = "fixed"
    energy = "energy"
    demand = "demand"
    rider = "rider"


class DemandBasis(str, Enum):
    ncp = "ncp"
    cp = "cp"
    contract = "contract"


class ChargeUnit(str, Enum):
    dollar_per_kw = "$/kW"
    dollar_per_kwh = "$/kWh"
    dollar_per_month = "$/month"
    percent = "percent"


class Season(str, Enum):
    summer = "summer"
    winter = "winter"
    shoulder = "shoulder"
    all = "all"


class TouPeriodName(str, Enum):
    on_peak = "on_peak"
    mid_peak = "mid_peak"
    off_peak = "off_peak"
    super_off_peak = "super_off_peak"
    shoulder = "shoulder"


class WeekdayMask(str, Enum):
    weekdays = "weekdays"
    weekends = "weekends"
    all = "all"


class HolidayCalendar(str, Enum):
    nerc = "nerc"
    federal = "federal"
    utility_specific = "utility_specific"


class TierBasis(str, Enum):
    kwh_monthly = "kwh_monthly"
    kw_billed = "kw_billed"
    load_factor_pct = "load_factor_pct"


class RuleType(str, Enum):
    demand_ratchet = "demand_ratchet"
    minimum_charge = "minimum_charge"
    term_commitment = "term_commitment"


class RatchetWindowType(str, Enum):
    rolling = "rolling"
    seasonal = "seasonal"
    contract_anniversary = "contract_anniversary"


class DataSource(str, Enum):
    scraped = "scraped"
    uploaded = "uploaded"
    manual = "manual"


# ---------------------------------------------------------------------------
# §6.1  Site
# ---------------------------------------------------------------------------


class Site(BaseModel):
    site_id: str
    brand: Brand
    store_number: str
    address: str
    city: str
    state: str
    lat: float
    lng: float
    voltage_level: VoltageLevel = VoltageLevel.secondary
    estimated_peak_kw: float | None = None
    utility_eia_id: str | None = None
    current_tariff_id: str | None = None
    data_source: DataSource = DataSource.scraped
    last_updated: str  # ISO 8601


# ---------------------------------------------------------------------------
# §6.2  Utility
# ---------------------------------------------------------------------------


class Utility(BaseModel):
    eia_id: str
    name: str
    state: str
    regulatory_jurisdiction: RegulatoryJurisdiction
    market_structure: MarketStructure
    input_tier: InputTier = InputTier.tier_2_peak_kw
    tariff_ids: list[str] = Field(default_factory=list)
    service_territory_geom: str | None = None  # WKT


# ---------------------------------------------------------------------------
# §6.5  TOU Schedule
# ---------------------------------------------------------------------------


class HourRange(BaseModel):
    start: int = Field(ge=0, le=23)
    end: int = Field(ge=0, le=23)


class TouPeriod(BaseModel):
    name: TouPeriodName
    season_months: list[int] = Field(default_factory=list)  # 1–12
    hour_ranges: list[HourRange] = Field(default_factory=list)
    weekday_mask: WeekdayMask = WeekdayMask.all
    holidays_off_peak: bool = True


class TouSchedule(BaseModel):
    tou_schedule_id: str
    periods: list[TouPeriod]
    holiday_calendar: HolidayCalendar = HolidayCalendar.federal
    holiday_dates: list[str] = Field(default_factory=list)  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# §6.4  Charge
# ---------------------------------------------------------------------------


class TierBlock(BaseModel):
    min: float
    max: float | None = None
    rate: float
    tier_basis: TierBasis = TierBasis.kwh_monthly


class AppliesTo(BaseModel):
    season: Season = Season.all
    tou_period: str | None = None
    applied_to_charge_ids: list[str] = Field(default_factory=list)


class ChargeClassification(BaseModel):
    category: DeliverySupplyCategory
    confidence: float = Field(ge=0.0, le=1.0)
    method: ClassificationMethod = ClassificationMethod.rule_based
    reasoning: str = ""


class Charge(BaseModel):
    charge_id: str
    tariff_id: str
    name: str
    type: ChargeType
    demand_basis: DemandBasis | None = None
    unit: ChargeUnit
    value: float | None = None  # None for tiered charges
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    tiers: list[TierBlock] = Field(default_factory=list)
    classification: ChargeClassification


# ---------------------------------------------------------------------------
# §6.6  Rule
# ---------------------------------------------------------------------------


class RuleParameters(BaseModel):
    ratchet_percent: float | None = None
    ratchet_window_months: int | None = None
    ratchet_window_type: RatchetWindowType | None = None
    ratchet_source_months: list[int] = Field(default_factory=list)
    minimum_monthly_charge: float | None = None


class Rule(BaseModel):
    rule_id: str
    type: RuleType
    parameters: RuleParameters


# ---------------------------------------------------------------------------
# §6.3  Tariff eligibility + full tariff
# ---------------------------------------------------------------------------


class TariffEligibility(BaseModel):
    min_kw: float | None = None
    max_kw: float | None = None
    min_kwh_annual: float | None = None
    max_kwh_annual: float | None = None
    voltage_required: list[VoltageLevel] = Field(default_factory=list)
    customer_classes: list[str] = Field(default_factory=list)
    load_factor_min: float | None = None
    term_commitment_months: int | None = None
    notes: str = ""


class Tariff(BaseModel):
    tariff_id: str
    utility_eia_id: str
    urdb_id: str | None = None
    name: str
    rate_code: str
    effective_date: str  # YYYY-MM-DD
    end_date: str | None = None
    version: str = "1"
    availability: TariffAvailability = TariffAvailability.optional
    eligibility: TariffEligibility = Field(default_factory=TariffEligibility)
    tou_schedule_id: str | None = None
    charges: list[str] = Field(default_factory=list)  # charge_ids
    rules: list[str] = Field(default_factory=list)  # rule_ids
    source_document: str = ""
    ingestion_method: IngestionMethod = IngestionMethod.manual
    delivery_supply_review_status: DeliverySupplyReviewStatus = (
        DeliverySupplyReviewStatus.auto_classified
    )
