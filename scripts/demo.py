"""VoltRegistry Phase 1 demo — P1 gate validation.

Given a site_id, returns the utility assignment for that site.

Usage:
    python scripts/demo.py --site-id WMT-0001
    python scripts/demo.py --site-id SAM-0042
    python scripts/demo.py --list-sample 10    # show 10 random sites

Requires bootstrap.py to have been run first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from sqlmodel import Session, select

from voltregistry.db import engine
from voltregistry.models import SiteTable, UtilityTable


def lookup_site(site_id: str) -> dict:
    """Return site + utility info for the given site_id."""
    with Session(engine) as session:
        site = session.get(SiteTable, site_id)
        if site is None:
            return {"error": f"Site '{site_id}' not found. Run bootstrap.py first."}

        utility = None
        if site.utility_eia_id:
            utility = session.get(UtilityTable, site.utility_eia_id)

        return {
            "site_id": site.site_id,
            "brand": site.brand,
            "store_number": site.store_number,
            "address": site.address,
            "city": site.city,
            "state": site.state,
            "lat": site.lat,
            "lng": site.lng,
            "utility_eia_id": site.utility_eia_id,
            "utility_name": utility.name if utility else None,
            "utility_state": utility.state if utility else None,
            "market_structure": utility.market_structure if utility else None,
            "data_source": site.data_source,
        }


def list_sample(n: int = 10) -> list[dict]:
    """Return n sites, sampling across states."""
    with Session(engine) as session:
        sites = session.exec(select(SiteTable).limit(n * 5)).all()
        # pick one per state up to n
        seen_states: set[str] = set()
        sample = []
        for s in sites:
            if s.state not in seen_states:
                seen_states.add(s.state)
                utility = session.get(UtilityTable, s.utility_eia_id) if s.utility_eia_id else None
                sample.append(
                    {
                        "site_id": s.site_id,
                        "state": s.state,
                        "utility_eia_id": s.utility_eia_id,
                        "utility_name": utility.name if utility else None,
                    }
                )
            if len(sample) >= n:
                break
        return sample


def main() -> None:
    parser = argparse.ArgumentParser(description="VoltRegistry Phase 1 demo")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--site-id", help="Site ID to look up (e.g. WMT-0001)")
    group.add_argument("--list-sample", type=int, metavar="N", help="List N sample sites")
    args = parser.parse_args()

    if args.site_id:
        result = lookup_site(args.site_id)
        print(json.dumps(result, indent=2))
        if "error" in result:
            sys.exit(1)
    else:
        results = list_sample(args.list_sample)
        print(json.dumps(results, indent=2))
        print(f"\n[{len(results)} sites shown]")


if __name__ == "__main__":
    main()
