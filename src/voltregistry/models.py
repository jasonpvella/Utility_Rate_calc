"""SQLModel table models for VoltRegistry.

These are the persistence-layer models (SQLite tables).  The richer Pydantic
schema models (Tariff, Charge, TouSchedule, Rule) live in
``voltregistry.tariffs.models`` and are stored as JSON columns.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class UtilityTable(SQLModel, table=True):
    """One row per EIA utility entity.

    ``service_territory_wkt`` is the WKT of the HIFLD polygon, stored for
    reference but not used in the hot path (GeoPandas spatial join is done
    once at ingest, not at query time).
    """

    __tablename__ = "utility"

    eia_id: str = Field(primary_key=True)
    name: str
    state: str
    regulatory_jurisdiction: str = ""  # "state_puc" | "ferc" | "municipal"
    market_structure: str = ""  # see §6.2 enum values
    input_tier: str = "TIER_2_PEAK_KW"  # "TIER_1_8760" | "TIER_2_PEAK_KW" | "TIER_3_VOLUMETRIC"
    service_territory_wkt: str | None = Field(default=None)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class SiteTable(SQLModel, table=True):
    """One row per Walmart / Sam's Club store."""

    __tablename__ = "site"

    site_id: str = Field(primary_key=True)  # e.g. "WMT-0001"
    brand: str  # "Walmart" | "SamsClub"
    store_number: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    lat: float
    lng: float
    voltage_level: str = "secondary"  # "secondary" | "primary" | "transmission"
    estimated_peak_kw: float | None = Field(default=None)
    utility_eia_id: str | None = Field(default=None, foreign_key="utility.eia_id")
    current_tariff_id: str | None = Field(default=None)
    data_source: str = "scraped"  # "scraped" | "uploaded" | "manual"
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class TariffTable(SQLModel, table=True):
    """One row per tariff.  Full TariffBundle payload stored as JSON.

    Key fields are hoisted out of the JSON for SQL-level filtering; the
    canonical source of truth is ``payload_json`` (a serialised TariffBundle).
    """

    __tablename__ = "tariff"

    tariff_id: str = Field(primary_key=True)
    utility_eia_id: str = Field(foreign_key="utility.eia_id", index=True)
    name: str
    rate_code: str = ""
    availability: str = "optional"   # mandatory | optional | closed_to_new
    effective_date: str = ""         # YYYY-MM-DD
    end_date: str | None = Field(default=None)
    payload_json: str                # JSON-serialised TariffBundle
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class UtilityTariffUrlTable(SQLModel, table=True):
    """One row per tariff source URL for a utility.

    Tracks where tariff documents live so the extraction pipeline can fetch
    and process them.  ``url_type`` distinguishes navigational portal pages
    from directly downloadable documents.
    """

    __tablename__ = "utility_tariff_url"

    id: int | None = Field(default=None, primary_key=True)
    utility_eia_id: str = Field(foreign_key="utility.eia_id", index=True)
    url: str
    url_type: str = "tariff_page"   # "portal" | "tariff_page" | "document"
    status: str = "pending"         # "pending" | "extracted" | "reviewed" | "failed"
    notes: str = ""
    last_fetched: datetime | None = Field(default=None)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class UtilityTariffInputsTable(SQLModel, table=True):
    """One row per rate schedule extracted from a tariff document.

    ``inputs_required`` is a JSON-serialised list of TariffInputType values.
    ``voltage_levels`` is a JSON-serialised list of applicable voltage tier strings.
    ``raw_extraction`` preserves the LLM output verbatim for audit and re-review.
    """

    __tablename__ = "utility_tariff_inputs"

    id: int | None = Field(default=None, primary_key=True)
    utility_eia_id: str = Field(foreign_key="utility.eia_id", index=True)
    schedule_code: str = ""          # e.g. "GS-2", "LP-1", "LPS"
    schedule_name: str               # e.g. "Large Power Service"
    applicability_min_kw: float | None = Field(default=None)
    applicability_max_kw: float | None = Field(default=None)
    applicability_notes: str = ""
    voltage_levels: str = "[]"       # JSON array of voltage tier strings
    inputs_required: str = "[]"      # JSON array of TariffInputType values
    source_url: str = ""
    extraction_status: str = "extracted"  # "extracted" | "reviewed" | "needs_review"
    confidence: float = 1.0
    raw_extraction: str = ""         # LLM output verbatim — do not truncate
    last_updated: datetime = Field(default_factory=datetime.utcnow)
