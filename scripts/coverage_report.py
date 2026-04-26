"""VoltRegistry Coverage Report

Shows, for every utility that serves at least one Walmart/Sam's Club store,
how many stores it serves and whether it has any tariff data in the DB.

Output:
  - Per-state summary (stores covered vs. gap)
  - Top N utilities with stores but no tariff data (the priority fetch list)
  - Overall coverage %

Usage:
    python scripts/coverage_report.py
    python scripts/coverage_report.py --top 50
    VOLTREGISTRY_DB=/tmp/voltregistry.db python scripts/coverage_report.py
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

import os  # noqa: E402
import sqlite3  # noqa: E402


def get_db_path() -> str:
    return os.environ.get("VOLTREGISTRY_DB", str(_REPO_ROOT / "data" / "voltregistry.db"))


def run(top_n: int = 30) -> None:
    db_path = get_db_path()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Sites with utility mapping
    sites = con.execute("""
        SELECT s.site_id, s.brand, s.state, s.utility_eia_id,
               u.name AS utility_name, u.market_structure
        FROM site s
        LEFT JOIN utility u ON s.utility_eia_id = u.eia_id
        WHERE s.utility_eia_id IS NOT NULL
    """).fetchall()

    # Utilities that have at least one tariff
    tariff_utils = set(
        r[0] for r in con.execute(
            "SELECT DISTINCT utility_eia_id FROM tariff"
        ).fetchall()
    )

    # Aggregate: utility → {store_count, name, market_structure, state, has_tariff}
    util_stats: dict[str, dict] = {}
    state_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "covered": 0})

    for site in sites:
        eid = site["utility_eia_id"]
        if eid not in util_stats:
            util_stats[eid] = {
                "eia_id": eid,
                "name": site["utility_name"] or "Unknown",
                "market_structure": site["market_structure"] or "",
                "states": set(),
                "store_count": 0,
                "has_tariff": eid in tariff_utils,
            }
        util_stats[eid]["store_count"] += 1
        util_stats[eid]["states"].add(site["state"])

        state = site["state"]
        state_stats[state]["total"] += 1
        if eid in tariff_utils:
            state_stats[state]["covered"] += 1

    total_stores = len(sites)
    covered_stores = sum(1 for s in sites if s["utility_eia_id"] in tariff_utils)
    total_utils = len(util_stats)
    covered_utils = sum(1 for u in util_stats.values() if u["has_tariff"])

    # --- Overall banner ---
    print("\n" + "=" * 70)
    print("  VoltRegistry Coverage Report")
    print("=" * 70)
    print(f"  Total stores mapped to a utility : {total_stores:>6,}")
    print(f"  Stores with tariff data          : {covered_stores:>6,}  ({covered_stores/max(total_stores,1)*100:.1f}%)")
    print(f"  Utilities serving stores         : {total_utils:>6,}")
    print(f"  Utilities with tariff data       : {covered_utils:>6,}  ({covered_utils/max(total_utils,1)*100:.1f}%)")
    print()

    # --- State breakdown ---
    print("-" * 70)
    print(f"  {'State':<6}  {'Stores':>6}  {'Covered':>8}  {'Pct':>6}  {'Gap':>5}")
    print("-" * 70)
    for state in sorted(state_stats):
        s = state_stats[state]
        pct = s["covered"] / max(s["total"], 1) * 100
        gap = s["total"] - s["covered"]
        bar = "█" * int(pct / 5)
        print(f"  {state:<6}  {s['total']:>6}  {s['covered']:>8}  {pct:>5.0f}%  {gap:>5}  {bar}")
    print()

    # --- Top utilities with stores but no tariff data ---
    gaps = sorted(
        [u for u in util_stats.values() if not u["has_tariff"]],
        key=lambda u: -u["store_count"],
    )
    print("-" * 70)
    print(f"  Top {top_n} utilities with NO tariff data (by store count)")
    print("-" * 70)
    print(f"  {'EIA ID':<8}  {'Stores':>6}  {'Market':<28}  {'St':<4}  Utility Name")
    print("-" * 70)
    for u in gaps[:top_n]:
        states_str = ",".join(sorted(u["states"]))[:4]
        mkt = u["market_structure"][:26] if u["market_structure"] else ""
        print(f"  {u['eia_id']:<8}  {u['store_count']:>6}  {mkt:<28}  {states_str:<4}  {u['name'][:40]}")

    print()
    print(f"  ... {len(gaps) - top_n} more utilities with gaps" if len(gaps) > top_n else "")

    # --- Priority EIA IDs for URDB fetch ---
    priority_eids = [u["eia_id"] for u in gaps[:top_n]]
    if priority_eids:
        print("-" * 70)
        print("  Priority EIA IDs for next URDB sweep (paste into ingest_urdb_bulk.py):")
        print("  " + " ".join(priority_eids[:20]))
        if len(priority_eids) > 20:
            print("  " + " ".join(priority_eids[20:]))
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoltRegistry store coverage report.")
    parser.add_argument("--top", type=int, default=30, metavar="N",
                        help="Show top N gap utilities (default 30)")
    args = parser.parse_args()
    run(top_n=args.top)
