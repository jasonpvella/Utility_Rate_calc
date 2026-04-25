"""Sam's Club store location ingestion.

Mirrors walmart_scraper.py structure — production live fetch with seed fallback.
Store ID format: SAM-{zero-padded 4-digit sequential number}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from voltregistry.ingest.seed_data import SAMSCLUB_STORE_COUNTS, STATE_BOUNDS

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RAW_DIR = _REPO_ROOT / "data" / "raw"
_CACHE_FILE = _RAW_DIR / "samsclub_stores.json"


def _fetch_live() -> list[dict[str, Any]]:
    """Attempt live fetch from samsclub.com store locator."""
    import httpx

    stores: list[dict[str, Any]] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; VoltRegistry-bot/0.1; "
            "+https://github.com/walmart-energy/voltregistry)"
        ),
        "Accept": "application/json",
    }
    for state in sorted(SAMSCLUB_STORE_COUNTS.keys()):
        url = f"https://www.samsclub.com/api/node/vivaldi/browse/v2/storelocator?singleLineAddr={state}&nbrOfStores=200"
        try:
            resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()
            raw_stores = data.get("info", []) or []
            for s in raw_stores:
                stores.append(
                    {
                        "brand": "SamsClub",
                        "store_number": str(s.get("id", "")),
                        "address": s.get("address1", ""),
                        "city": s.get("city", ""),
                        "state": s.get("state", state),
                        "lat": float(s.get("latitude", 0)),
                        "lng": float(s.get("longitude", 0)),
                    }
                )
        except Exception as exc:
            logger.debug("Sam's Club live fetch failed for %s: %s", state, exc)
            raise

    return stores


def _generate_seed() -> list[dict[str, Any]]:
    rng = np.random.default_rng(43)  # different seed from Walmart for independence
    stores: list[dict[str, Any]] = []
    counter = 1

    for state, count in sorted(SAMSCLUB_STORE_COUNTS.items()):
        if count == 0:
            continue
        if state not in STATE_BOUNDS:
            logger.warning("No bounding box for state %s — skipping", state)
            continue
        lat_min, lat_max, lng_min, lng_max = STATE_BOUNDS[state]

        lats = rng.uniform(lat_min, lat_max, count)
        lngs = rng.uniform(lng_min, lng_max, count)

        for i in range(count):
            stores.append(
                {
                    "brand": "SamsClub",
                    "store_number": f"{counter:04d}",
                    "address": f"{counter} Club Blvd",
                    "city": state,
                    "state": state,
                    "lat": round(float(lats[i]), 6),
                    "lng": round(float(lngs[i]), 6),
                    "_seed": True,
                }
            )
            counter += 1

    logger.info("Generated %d synthetic Sam's Club stores", len(stores))
    return stores


def load_stores(force_refresh: bool = False) -> list[dict[str, Any]]:
    _RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not force_refresh and _CACHE_FILE.exists():
        logger.info("Sam's Club stores: using cache %s", _CACHE_FILE)
        with _CACHE_FILE.open() as f:
            return json.load(f)

    try:
        logger.info("Sam's Club stores: attempting live fetch from samsclub.com")
        stores = _fetch_live()
        if len(stores) > 50:
            logger.info("Sam's Club stores: live fetch returned %d stores", len(stores))
            with _CACHE_FILE.open("w") as f:
                json.dump(stores, f)
            return stores
        logger.warning("Sam's Club stores: live fetch too few (%d)", len(stores))
    except Exception as exc:
        logger.info("Sam's Club stores: live fetch failed (%s), using seed data", exc)

    stores = _generate_seed()
    with _CACHE_FILE.open("w") as f:
        json.dump(stores, f)
    logger.info("Sam's Club stores: cached %d seed stores to %s", len(stores), _CACHE_FILE)
    return stores


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    stores = load_stores()
    print(f"Total Sam's Club stores: {len(stores)}")
    for s in stores[:3]:
        print(" ", s)
