"""Bulk URDB tariff ingestion for VoltRegistry.

Iterates over every utility in the DB, fetches its commercial tariffs from
the OpenEI URDB API (with local cache), converts each using the
``urdb_to_bundle`` adapter, and upserts into the ``tariff`` table.

Usage (from repo root)
----------------------
    python scripts/ingest_urdb_bulk.py               # all utilities in DB
    python scripts/ingest_urdb_bulk.py --eia 6452    # single utility test
    python scripts/ingest_urdb_bulk.py --limit 20    # first N utilities
    python scripts/ingest_urdb_bulk.py --refresh     # bypass URDB cache

Environment
-----------
    VOLTREGISTRY_DB   SQLite path (default: data/voltregistry.db)
    URDB_API_KEY      OpenEI API key (default: DEMO_KEY — rate-limited)

Design notes
------------
* Errors are isolated per utility.  One bad URDB response does not abort
  the run; it is logged and skipped.
* The URDB client already caches responses to data/raw/urdb/eia_<id>.json.
  Re-runs are fast (no network hit) unless --refresh is passed.
* Tariffs are upserted (insert or update), so the script is idempotent.
* Reference tariffs (ingestion_method="manual") are NEVER overwritten by
  this script — they are identified by tariff_id prefix and skipped.
* Only tariffs where the utility exists in the DB are ingested.  Utilities
  not in EIA-861 data are excluded automatically.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup (allows running as a script without installing the package)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from sqlmodel import Session, create_engine, select  # noqa: E402

from voltregistry.db import get_db_url  # noqa: E402
from voltregistry.ingest.urdb_client import fetch_tariffs  # noqa: E402
from voltregistry.ingest.urdb_to_bundle import urdb_to_bundle  # noqa: E402
from voltregistry.models import TariffTable, UtilityTable  # noqa: E402
from voltregistry.tariffs.models import IngestionMethod  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ingest_urdb_bulk")


# ---------------------------------------------------------------------------
# Protected tariff ID prefixes that must not be overwritten
# ---------------------------------------------------------------------------

_PROTECTED_PREFIXES: tuple[str, ...] = (
    "entergy-ar-",
    "duke-carolinas-",
    "oncor-",
    "georgia-power-",
    "fpl-",
)


def _is_protected(tariff_id: str) -> bool:
    return any(tariff_id.startswith(p) for p in _PROTECTED_PREFIXES)


# ---------------------------------------------------------------------------
# Upsert helper (mirrors bootstrap._upsert_tariffs logic)
# ---------------------------------------------------------------------------


def _upsert_bundle(session: Session, bundle) -> str:
    """Insert or update a TariffTable row.  Returns 'inserted' or 'updated'."""
    t = bundle.tariff
    if _is_protected(t.tariff_id):
        return "protected"

    payload = bundle.model_dump_json()
    existing = session.get(TariffTable, t.tariff_id)

    avail = t.availability.value if hasattr(t.availability, "value") else str(t.availability)

    if existing:
        existing.name = t.name
        existing.rate_code = t.rate_code
        existing.availability = avail
        existing.effective_date = t.effective_date
        existing.end_date = t.end_date
        existing.payload_json = payload
        existing.last_updated = datetime.utcnow()
        return "updated"
    else:
        row = TariffTable(
            tariff_id=t.tariff_id,
            utility_eia_id=t.utility_eia_id,
            name=t.name,
            rate_code=t.rate_code,
            availability=avail,
            effective_date=t.effective_date,
            end_date=t.end_date,
            payload_json=payload,
            last_updated=datetime.utcnow(),
        )
        session.add(row)
        return "inserted"


# ---------------------------------------------------------------------------
# Main ingestion loop
# ---------------------------------------------------------------------------


def run_bulk_ingest(
    eia_filter: str | None = None,
    limit: int | None = None,
    force_refresh: bool = False,
) -> dict:
    """Run URDB bulk ingestion.

    Args:
        eia_filter:    If set, only process this single EIA ID.
        limit:         If set, process at most this many utilities.
        force_refresh: Bypass URDB cache and re-fetch from API.

    Returns:
        Summary dict with counts.
    """
    engine = create_engine(get_db_url())

    with Session(engine) as session:
        # Fetch all utilities from DB
        stmt = select(UtilityTable)
        if eia_filter:
            stmt = stmt.where(UtilityTable.eia_id == eia_filter)
        utilities = session.exec(stmt).all()

    if limit:
        utilities = utilities[:limit]

    logger.info(
        "Starting bulk URDB ingest for %d utilities (refresh=%s)",
        len(utilities),
        force_refresh,
    )

    stats = {
        "utilities_processed": 0,
        "utilities_with_tariffs": 0,
        "utilities_no_urdb_data": 0,
        "tariffs_inserted": 0,
        "tariffs_updated": 0,
        "tariffs_skipped_protected": 0,
        "tariffs_conversion_failed": 0,
        "utilities_fetch_error": 0,
    }
    t0 = time.time()

    for util in utilities:
        eia_id = util.eia_id
        market = util.market_structure or "regulated_vertical"
        stats["utilities_processed"] += 1

        try:
            raw_items = fetch_tariffs(eia_id, force_refresh=force_refresh)
        except Exception as exc:
            logger.warning("EIA %s: URDB fetch failed — %s", eia_id, exc)
            stats["utilities_fetch_error"] += 1
            continue

        if not raw_items:
            stats["utilities_no_urdb_data"] += 1
            logger.debug("EIA %s: no commercial tariffs in URDB", eia_id)
            continue

        util_inserted = 0
        util_updated = 0

        with Session(engine) as session:
            for raw in raw_items:
                try:
                    bundle = urdb_to_bundle(raw, eia_id, market_structure=market)
                except Exception as exc:
                    logger.warning(
                        "EIA %s label %s: adapter exception — %s",
                        eia_id, raw.get("label", "?"), exc,
                    )
                    stats["tariffs_conversion_failed"] += 1
                    continue

                if bundle is None:
                    stats["tariffs_conversion_failed"] += 1
                    continue

                outcome = _upsert_bundle(session, bundle)
                if outcome == "inserted":
                    util_inserted += 1
                    stats["tariffs_inserted"] += 1
                elif outcome == "updated":
                    util_updated += 1
                    stats["tariffs_updated"] += 1
                else:
                    stats["tariffs_skipped_protected"] += 1

            session.commit()

        if util_inserted + util_updated > 0:
            stats["utilities_with_tariffs"] += 1
            logger.info(
                "EIA %s (%s): +%d inserted, ~%d updated",
                eia_id, util.name[:40], util_inserted, util_updated,
            )

    elapsed = time.time() - t0
    _print_summary(stats, elapsed)
    return stats


def _print_summary(stats: dict, elapsed: float) -> None:
    print("\n" + "=" * 60)
    print("URDB Bulk Ingestion — Summary")
    print("=" * 60)
    print(f"  Utilities processed     : {stats['utilities_processed']:>6,}")
    print(f"  Utilities with tariffs  : {stats['utilities_with_tariffs']:>6,}")
    print(f"  Utilities no URDB data  : {stats['utilities_no_urdb_data']:>6,}")
    print(f"  Utilities fetch error   : {stats['utilities_fetch_error']:>6,}")
    print(f"  Tariffs inserted        : {stats['tariffs_inserted']:>6,}")
    print(f"  Tariffs updated         : {stats['tariffs_updated']:>6,}")
    print(f"  Tariffs protected       : {stats['tariffs_skipped_protected']:>6,}")
    print(f"  Conversion failures     : {stats['tariffs_conversion_failed']:>6,}")
    print(f"  Elapsed                 : {elapsed:>8.1f}s")
    print("=" * 60)
    coverage = stats["utilities_with_tariffs"] / max(stats["utilities_processed"], 1)
    print(f"  URDB coverage           : {coverage:>7.1%}")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk-ingest commercial tariffs from URDB into VoltRegistry DB."
    )
    parser.add_argument(
        "--eia",
        metavar="EIA_ID",
        default=None,
        help="Process a single utility EIA ID (useful for smoke-testing).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N utilities (for incremental runs).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Bypass URDB cache and re-fetch all tariff data from the API.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run_bulk_ingest(
        eia_filter=args.eia,
        limit=args.limit,
        force_refresh=args.refresh,
    )
