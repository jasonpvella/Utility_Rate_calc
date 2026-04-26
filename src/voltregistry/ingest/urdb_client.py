"""OpenEI URDB API client for VoltRegistry.

Fetches tariff metadata from the OpenEI Utility Rate Database (URDB) for a
given utility EIA ID.  Results are cached to ``data/raw/urdb/`` as JSON so
repeat runs skip the network request.

API endpoint (v8):
  https://api.openei.org/utility_rates?version=8&format=json&detail=full
      &eia=<eia_id>&api_key=<key>

API key:
  - Set URDB_API_KEY env var for authenticated access (no rate limiting).
  - Without a key the client uses "DEMO_KEY" which allows limited requests
    and is sufficient for fetching 5 reference utilities in development.
  - Register free at https://openei.org/services/doc/rest/util_rates/

Fallback:
  If the network request fails (sandbox, no credentials, rate-limit), the
  client returns cached data if available, otherwise an empty list.  A
  warning is logged so it is obvious which path ran.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _REPO_ROOT / "data" / "raw" / "urdb"
_URDB_BASE = "https://api.openei.org/utility_rates"

# Sector filter: only pull commercial/industrial tariffs
_SECTORS = ("Commercial", "Industrial", "Lighting")


def _api_key() -> str:
    return os.environ.get("URDB_API_KEY", "DEMO_KEY")


def _cache_path(eia_id: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"eia_{eia_id}.json"


def _fetch_from_api(eia_id: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Fetch all tariffs for *eia_id* from URDB API.  Returns raw item list."""
    params = {
        "version": "8",
        "format": "json",
        "detail": "full",
        "eia": eia_id,
        "api_key": _api_key(),
        "limit": "500",
    }
    logger.info("URDB: fetching tariffs for EIA ID %s", eia_id)
    resp = httpx.get(_URDB_BASE, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    items: list[dict[str, Any]] = data.get("items") or []
    logger.info("URDB: received %d tariff items for EIA ID %s", len(items), eia_id)
    return items


def _load_cache(eia_id: str) -> list[dict[str, Any]] | None:
    path = _cache_path(eia_id)
    if path.exists():
        logger.info("URDB: loading from cache %s", path)
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_cache(eia_id: str, items: list[dict[str, Any]]) -> None:
    path = _cache_path(eia_id)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    logger.info("URDB: cached %d items to %s", len(items), path)


def fetch_tariffs(
    eia_id: str,
    *,
    force_refresh: bool = False,
    commercial_only: bool = True,
) -> list[dict[str, Any]]:
    """Return URDB tariff items for the given utility EIA ID.

    Args:
        eia_id: EIA utility ID string (e.g. "6452" for Entergy Arkansas).
        force_refresh: Bypass cache and re-fetch from API.
        commercial_only: If True, filter to Commercial/Industrial/Lighting
            sectors only.  Residential tariffs are excluded.

    Returns:
        List of raw URDB tariff dicts.  Empty list if nothing found.
    """
    items: list[dict[str, Any]] | None = None

    if not force_refresh:
        items = _load_cache(eia_id)

    if items is None:
        try:
            items = _fetch_from_api(eia_id)
            _save_cache(eia_id, items)
        except Exception as exc:
            logger.warning(
                "URDB: API fetch failed for EIA ID %s (%s) — falling back to cache",
                eia_id,
                exc,
            )
            items = _load_cache(eia_id)
            if items is None:
                logger.warning("URDB: no cache available for EIA ID %s — returning []", eia_id)
                return []

    if commercial_only:
        items = [
            it for it in items
            if any(s in _SECTORS for s in _as_list(it.get("sector", [])))
        ]
        logger.debug("URDB: %d commercial/industrial tariffs after sector filter", len(items))

    return items


def get_tariff_by_label(label: str, eia_id: str) -> dict[str, Any] | None:
    """Look up a specific URDB tariff by its label within a utility's cached items.

    Fetches (and caches) the utility's tariff list if not already cached.
    Returns None if the label is not found.
    """
    items = fetch_tariffs(eia_id)
    for item in items:
        if str(item.get("label", "")) == label:
            return item
    return None


def summarise_tariffs(eia_id: str) -> list[dict[str, Any]]:
    """Return a lightweight summary list (label, name, sector, startdate) for an EIA ID."""
    items = fetch_tariffs(eia_id)
    return [
        {
            "label": it.get("label"),
            "name": it.get("name"),
            "sector": it.get("sector"),
            "startdate": it.get("startdate"),
            "enddate": it.get("enddate"),
            "source": it.get("sourcetitle"),
        }
        for it in items
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list[str]:
    """Normalise a URDB field that may be a string, list, or None."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    eid = sys.argv[1] if len(sys.argv) > 1 else "6452"
    summaries = summarise_tariffs(eid)
    print(f"\nURDB tariffs for EIA ID {eid}: {len(summaries)} commercial tariffs")
    for s in summaries[:10]:
        print(f"  [{s['label']}] {s['name']}")
