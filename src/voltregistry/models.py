"""SQLModel table models for VoltRegistry Phase 1.

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
