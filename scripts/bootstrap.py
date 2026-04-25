"""VoltRegistry Phase 1 bootstrap — end-to-end ingestion pipeline.

Runs all Phase 1 ingest steps in order, then persists to SQLite.
Idempotent: safe to re-run.  Use --force-refresh to re-download data.

Usage:
    python scripts/bootstrap.py
    python scripts/bootstrap.py --force-refresh   # clear caches, re-fetch
    VOLTREGISTRY_DB=/tmp/voltregistry.db python scripts/bootstrap.py  # alt DB path

Pipeline steps:
    1. Load EIA Form 861 utility metadata (download or seed fallback)
    2. Load Walmart store locations (scrape or seed fallback)
    3. Load Sam's Club store locations (scrape or seed fallback)
    4. Load HIFLD electric service territory polygons (download or seed fallback)
    5. Run geospatial territory join → site → utility_eia_id
    6. Persist utilities to SQLite
    7. Persist sites (with utility assignments) to SQLite
    8. Print summary counts and match rate
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Make src/ importable when run as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from sqlmodel import Session, select

from voltregistry.db import create_db_and_tables, engine
from voltregistry.ingest.eia_form_861 import load_utilities_with_fallback
from voltregistry.ingest.hifld_territories import load_territories
from voltregistry.ingest.walmart_scraper import load_stores as load_walmart
from voltregistry.ingest.samsclub_scraper import load_stores as load_samsclub
from voltregistry.mapping.territory_join import join_sites_to_territories
from voltregistry.models import SiteTable, UtilityTable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bootstrap")


def _upsert_utilities(session: Session, utilities: list[dict]) -> int:
    """Insert or update utility rows.  Returns count inserted/updated."""
    count = 0
    for u in utilities:
        existing = session.get(UtilityTable, u["eia_id"])
        if existing:
            for k, v in u.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            existing.last_updated = datetime.utcnow()
        else:
            row = UtilityTable(
                eia_id=u["eia_id"],
                name=u["name"],
                state=u["state"],
                regulatory_jurisdiction=u.get("regulatory_jurisdiction", ""),
                market_structure=u.get("market_structure", ""),
                input_tier=u.get("input_tier", "TIER_2_PEAK_KW"),
                last_updated=datetime.utcnow(),
            )
            session.add(row)
        count += 1
    session.commit()
    return count


def _upsert_sites(
    session: Session,
    raw_stores: list[dict],
    join_results: dict[str, dict],
    brand: str,
    id_prefix: str,
) -> int:
    """Insert or update site rows.  Returns count inserted/updated."""
    count = 0
    for raw in raw_stores:
        store_num = str(raw.get("store_number", ""))
        site_id = f"{id_prefix}-{store_num}"
        join = join_results.get(site_id, {})

        existing = session.get(SiteTable, site_id)
        if existing:
            existing.lat = float(raw["lat"])
            existing.lng = float(raw["lng"])
            existing.utility_eia_id = join.get("utility_eia_id")
            existing.last_updated = datetime.utcnow()
        else:
            row = SiteTable(
                site_id=site_id,
                brand=brand,
                store_number=store_num,
                address=raw.get("address", ""),
                city=raw.get("city", ""),
                state=raw.get("state", ""),
                lat=float(raw["lat"]),
                lng=float(raw["lng"]),
                utility_eia_id=join.get("utility_eia_id"),
                data_source="scraped" if not raw.get("_seed") else "manual",
                last_updated=datetime.utcnow(),
            )
            session.add(row)
        count += 1
    session.commit()
    return count


def run(force_refresh: bool = False) -> None:
    t0 = time.time()

    # -----------------------------------------------------------------------
    # Step 1: DB setup
    # -----------------------------------------------------------------------
    logger.info("Step 1/7: Initialising database")
    create_db_and_tables()

    # -----------------------------------------------------------------------
    # Step 2: EIA Form 861 utilities
    # -----------------------------------------------------------------------
    logger.info("Step 2/7: Loading EIA utility metadata")
    utilities = load_utilities_with_fallback()
    logger.info("  → %d utilities loaded", len(utilities))

    # -----------------------------------------------------------------------
    # Step 3: Store locations
    # -----------------------------------------------------------------------
    logger.info("Step 3/7: Loading Walmart store locations")
    walmart_stores = load_walmart(force_refresh=force_refresh)
    logger.info("  → %d Walmart stores", len(walmart_stores))

    logger.info("Step 4/7: Loading Sam's Club store locations")
    samsclub_stores = load_samsclub(force_refresh=force_refresh)
    logger.info("  → %d Sam's Club stores", len(samsclub_stores))

    # Assign site_ids before join so the join result keys match
    for s in walmart_stores:
        s["site_id"] = f"WMT-{s['store_number']}"
    for s in samsclub_stores:
        s["site_id"] = f"SAM-{s['store_number']}"

    all_stores = walmart_stores + samsclub_stores
    logger.info("  → %d total stores", len(all_stores))

    # -----------------------------------------------------------------------
    # Step 5: HIFLD territories + spatial join
    # -----------------------------------------------------------------------
    logger.info("Step 5/7: Loading territory polygons")
    territories = load_territories(force_refresh=force_refresh)
    logger.info("  → %d territory polygons", len(territories))

    logger.info("Step 6/7: Running geospatial territory join")
    join_results_list = join_sites_to_territories(all_stores, territories=territories)
    join_results = {r["site_id"]: r for r in join_results_list}

    matched = sum(1 for r in join_results_list if r["utility_eia_id"] is not None)
    total = len(join_results_list)
    logger.info(
        "  → %d/%d sites matched to a utility (%.1f%%)",
        matched, total, 100.0 * matched / total if total else 0,
    )

    # -----------------------------------------------------------------------
    # Step 7: Persist to SQLite
    # -----------------------------------------------------------------------
    logger.info("Step 7/7: Persisting to SQLite")
    with Session(engine) as session:
        util_count = _upsert_utilities(session, utilities)
        logger.info("  → %d utilities upserted", util_count)

        wmt_count = _upsert_sites(session, walmart_stores, join_results, "Walmart", "WMT")
        logger.info("  → %d Walmart sites upserted", wmt_count)

        sam_count = _upsert_sites(session, samsclub_stores, join_results, "SamsClub", "SAM")
        logger.info("  → %d Sam's Club sites upserted", sam_count)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print("VoltRegistry Phase 1 Bootstrap Complete")
    print("=" * 60)
    print(f"  Utilities loaded    : {util_count:>6,}")
    print(f"  Walmart sites       : {wmt_count:>6,}")
    print(f"  Sam's Club sites    : {sam_count:>6,}")
    print(f"  Total sites         : {wmt_count + sam_count:>6,}")
    print(f"  Utility match rate  : {100.0 * matched / total:.1f}%  ({matched}/{total})")
    print(f"  Elapsed             : {elapsed:.1f}s")
    print("=" * 60)
    print(f"\nNext: python scripts/demo.py --site-id WMT-0001")


def main() -> None:
    parser = argparse.ArgumentParser(description="VoltRegistry Phase 1 bootstrap")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore caches and re-fetch all external data",
    )
    args = parser.parse_args()
    run(force_refresh=args.force_refresh)


if __name__ == "__main__":
    main()
