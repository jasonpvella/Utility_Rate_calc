"""VoltRegistry FastAPI application.

Phase 2 endpoints:
  GET /utilities/{eia_id}/tariffs   List tariffs for a utility (P2 demo gate)

Phase 4 endpoints (stubbed, implemented in P4):
  GET  /sites                        List sites (paginated)
  GET  /sites/{site_id}              Site detail with utility + current tariff
  POST /sites/{site_id}/compare      Run comparison
  GET  /sites/{site_id}/compare/{run_id}/report.html   Rendered HTML report

Run with:
  uvicorn voltregistry.api.main:app --reload
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from sqlmodel import Session, select

from voltregistry.db import create_db_and_tables, get_session
from voltregistry.models import TariffTable, UtilityTable
from voltregistry.tariffs.models import TariffBundle

logger = logging.getLogger(__name__)

app = FastAPI(
    title="VoltRegistry API",
    version="0.2.0",
    description=(
        "Utility delivery-charge mapping and tariff comparison for the Walmart/Sam's Club footprint."
    ),
)


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": "0.2.0"}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


@app.get("/utilities/{eia_id}", tags=["utilities"])
def get_utility(
    eia_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return metadata for a single utility."""
    utility = session.get(UtilityTable, eia_id)
    if not utility:
        raise HTTPException(status_code=404, detail=f"Utility {eia_id!r} not found")
    return {
        "eia_id": utility.eia_id,
        "name": utility.name,
        "state": utility.state,
        "regulatory_jurisdiction": utility.regulatory_jurisdiction,
        "market_structure": utility.market_structure,
        "input_tier": utility.input_tier,
    }


# ---------------------------------------------------------------------------
# Tariffs (P2 demo gate)
# ---------------------------------------------------------------------------


@app.get("/utilities/{eia_id}/tariffs", tags=["tariffs"])
def get_utility_tariffs(
    eia_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """List all tariffs for a utility.

    Returns a summary list — full TariffBundle available via
    GET /utilities/{eia_id}/tariffs/{tariff_id}.
    This endpoint is the Phase 2 demo gate.
    """
    # Verify utility exists
    utility = session.get(UtilityTable, eia_id)
    if not utility:
        raise HTTPException(status_code=404, detail=f"Utility {eia_id!r} not found")

    rows = session.exec(
        select(TariffTable).where(TariffTable.utility_eia_id == eia_id)
    ).all()

    tariffs = []
    for row in rows:
        try:
            bundle = TariffBundle.model_validate_json(row.payload_json)
        except Exception as exc:
            logger.warning("Failed to parse TariffBundle for %s: %s", row.tariff_id, exc)
            continue

        tariffs.append({
            "tariff_id": row.tariff_id,
            "name": row.name,
            "rate_code": row.rate_code,
            "availability": row.availability,
            "effective_date": row.effective_date,
            "end_date": row.end_date,
            "urdb_id": bundle.tariff.urdb_id,
            "ingestion_method": bundle.tariff.ingestion_method,
            "delivery_supply_review_status": bundle.tariff.delivery_supply_review_status,
            "eligibility": bundle.tariff.eligibility.model_dump(exclude_none=True),
            "charge_count": len(bundle.charges),
            "rule_count": len(bundle.rules),
            "has_tou": bundle.tou_schedule is not None,
        })

    return {
        "eia_id": eia_id,
        "utility_name": utility.name,
        "tariff_count": len(tariffs),
        "tariffs": tariffs,
    }


@app.get("/utilities/{eia_id}/tariffs/{tariff_id}", tags=["tariffs"])
def get_tariff_detail(
    eia_id: str,
    tariff_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return the full TariffBundle for a specific tariff."""
    row = session.get(TariffTable, tariff_id)
    if not row or row.utility_eia_id != eia_id:
        raise HTTPException(
            status_code=404,
            detail=f"Tariff {tariff_id!r} not found for utility {eia_id!r}",
        )
    return json.loads(row.payload_json)


# ---------------------------------------------------------------------------
# Sites (Phase 4 stubs — not yet implemented)
# ---------------------------------------------------------------------------


@app.get("/sites", tags=["sites"])
def list_sites() -> dict[str, str]:
    """[Phase 4] List all sites — not yet implemented."""
    raise HTTPException(status_code=501, detail="Implemented in Phase 4")


@app.get("/sites/{site_id}", tags=["sites"])
def get_site(site_id: str) -> dict[str, str]:
    """[Phase 4] Get site detail — not yet implemented."""
    raise HTTPException(status_code=501, detail="Implemented in Phase 4")


@app.post("/sites/{site_id}/compare", tags=["comparison"])
def compare_site(site_id: str) -> dict[str, str]:
    """[Phase 4] Run tariff comparison for a site — not yet implemented."""
    raise HTTPException(status_code=501, detail="Implemented in Phase 4")
