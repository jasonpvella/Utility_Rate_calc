"""Comparison engine for VoltRegistry.

Given a site_id, runs the TariffEngine against each eligible tariff for the
site's utility and returns a ranked delivery-cost comparison per §11.

Response shape (serialisable to the §11 API contract):
  ComparisonResult.to_dict() → matches POST /sites/{site_id}/compare response
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from sqlmodel import Session, select

from voltregistry.engine.tariff_engine import TariffEngine
from voltregistry.load.synthetic import synthesize_load
from voltregistry.models import SiteTable, TariffTable
from voltregistry.tariffs.eligibility import check_eligibility
from voltregistry.tariffs.models import TariffBundle

# ---------------------------------------------------------------------------
# State → IECC climate zone (single dominant zone per state)
# ---------------------------------------------------------------------------

_STATE_CZ: dict[str, int] = {
    "AK": 7, "AL": 2, "AR": 3, "AZ": 2, "CA": 3,
    "CO": 5, "CT": 5, "DC": 4, "DE": 4, "FL": 2,
    "GA": 3, "HI": 1, "IA": 5, "ID": 5, "IL": 5,
    "IN": 5, "KS": 4, "KY": 4, "LA": 2, "MA": 5,
    "MD": 4, "ME": 6, "MI": 5, "MN": 6, "MO": 4,
    "MS": 2, "MT": 6, "NC": 3, "ND": 6, "NE": 5,
    "NH": 6, "NJ": 4, "NM": 3, "NV": 3, "NY": 5,
    "OH": 5, "OK": 3, "OR": 4, "PA": 5, "RI": 5,
    "SC": 3, "SD": 6, "TN": 4, "TX": 2, "UT": 5,
    "VA": 4, "VT": 6, "WA": 4, "WI": 6, "WV": 5,
    "WY": 6,
}

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class TariffComparisonEntry:
    tariff_id: str
    name: str
    annual_delivery_cost: float
    delta_vs_current: float | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class IneligibleEntry:
    tariff_id: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult:
    site_id: str
    utility_eia_id: str
    load_profile_used: str
    current_tariff: dict | None
    alternatives: list[TariffComparisonEntry]
    ineligible: list[IneligibleEntry]

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "utility_eia_id": self.utility_eia_id,
            "load_profile_used": self.load_profile_used,
            "current_tariff": self.current_tariff,
            "alternatives": [asdict(a) for a in self.alternatives],
            "ineligible": [asdict(i) for i in self.ineligible],
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_comparison(site_id: str, session: Session) -> ComparisonResult:
    """Run a full tariff comparison for a site.

    Loads all tariffs for the site's utility, filters to eligible ones,
    runs TariffEngine for each, and returns results ranked by delivery cost.

    Raises:
        ValueError: if site not found or has no utility mapping or tariffs.
    """
    site = session.get(SiteTable, site_id)
    if site is None:
        raise ValueError(f"Site {site_id!r} not found.")
    if not site.utility_eia_id:
        raise ValueError(f"Site {site_id!r} has no utility mapping.")

    tariff_rows = session.exec(
        select(TariffTable).where(TariffTable.utility_eia_id == site.utility_eia_id)
    ).all()

    if not tariff_rows:
        raise ValueError(
            f"No tariffs found for utility {site.utility_eia_id!r}."
        )

    # Synthesize the load profile for this site
    brand = site.brand  # "Walmart" | "SamsClub"
    climate_zone = _STATE_CZ.get(site.state, 4)
    load_profile = synthesize_load(brand, climate_zone)
    profile_label = f"synthetic_{brand.lower()}_cz{climate_zone}"

    # Use profile peak as fallback when site has no estimated_peak_kw
    profile_peak_kw = float(np.max(load_profile))
    effective_peak_kw = (
        site.estimated_peak_kw
        if site.estimated_peak_kw is not None
        else profile_peak_kw
    )

    # Partition tariffs into eligible / ineligible
    eligible: list[tuple[TariffTable, TariffBundle, list[str]]] = []
    ineligible: list[IneligibleEntry] = []

    for row in tariff_rows:
        try:
            bundle = TariffBundle.model_validate_json(row.payload_json)
        except Exception:
            ineligible.append(
                IneligibleEntry(
                    tariff_id=row.tariff_id,
                    reasons=["Failed to parse tariff payload."],
                )
            )
            continue

        result = check_eligibility(
            voltage_level=site.voltage_level,
            estimated_peak_kw=effective_peak_kw,
            customer_class="commercial",
            availability=bundle.tariff.availability.value,
            eligibility=bundle.tariff.eligibility,
            end_date=bundle.tariff.end_date,
        )

        if result.eligible:
            eligible.append((row, bundle, result.warnings))
        else:
            ineligible.append(
                IneligibleEntry(tariff_id=row.tariff_id, reasons=result.reasons)
            )

    # Calculate delivery cost for each eligible tariff
    entries: list[TariffComparisonEntry] = []
    current_tariff: dict | None = None

    for row, bundle, elig_warnings in eligible:
        engine = TariffEngine(
            tariff=bundle.tariff,
            charges=bundle.charges,
            rules=bundle.rules,
            tou=bundle.tou_schedule,
        )
        calc = engine.calculate(load_profile)
        cost = round(calc.annual_total_delivery, 2)

        entries.append(
            TariffComparisonEntry(
                tariff_id=row.tariff_id,
                name=bundle.tariff.name,
                annual_delivery_cost=cost,
                delta_vs_current=None,  # resolved below
                warnings=elig_warnings + calc.warnings,
            )
        )

        if row.tariff_id == site.current_tariff_id:
            current_tariff = {
                "tariff_id": row.tariff_id,
                "annual_delivery_cost": cost,
            }

    # Handle case where current_tariff_id is set but landed in ineligible
    if site.current_tariff_id and current_tariff is None:
        current_tariff = {
            "tariff_id": site.current_tariff_id,
            "annual_delivery_cost": None,
        }

    # Fill delta_vs_current
    current_cost = current_tariff["annual_delivery_cost"] if current_tariff else None
    for entry in entries:
        if current_cost is not None:
            entry.delta_vs_current = round(entry.annual_delivery_cost - current_cost, 2)

    # Rank cheapest first
    entries.sort(key=lambda e: e.annual_delivery_cost)

    return ComparisonResult(
        site_id=site_id,
        utility_eia_id=site.utility_eia_id,
        load_profile_used=profile_label,
        current_tariff=current_tariff,
        alternatives=entries,
        ineligible=ineligible,
    )
