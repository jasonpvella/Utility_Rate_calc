"""Geospatial territory join — site lat/lng → utility EIA ID.

Algorithm
---------
1. Load the territory GeoDataFrame (``data/territories.gpkg``).
2. Build a GeoDataFrame of site points from the sites list.
3. Run ``gpd.sjoin`` (point-in-polygon, predicate="within").
4. Handle edge cases:
   - Sites on territory boundaries → assign to first match (smallest polygon wins
     in tie-break to prefer specific territories over large catch-all polygons).
   - Sites with no polygon match → log a warning and leave utility_eia_id as None.
5. Return a list of ``(site_id, utility_eia_id, utility_name)`` tuples.

This module is the only place a spatial join runs.  The territory GeoDataFrame
is loaded once and can be passed in to avoid repeated I/O in batch runs.

Performance note: for ~5,200 points against ~3,500 territory polygons, the
STRtree-backed sjoin in GeoPandas 0.14+ completes in under 10 seconds on a
laptop CPU.  No parallelism needed for Phase 1.
"""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

logger = logging.getLogger(__name__)


def join_sites_to_territories(
    sites: list[dict[str, Any]],
    territories: gpd.GeoDataFrame | None = None,
) -> list[dict[str, Any]]:
    """Assign each site its serving utility via spatial join.

    Args:
        sites: list of site dicts, each with at minimum keys:
               ``site_id``, ``lat``, ``lng``.
        territories: optional pre-loaded territory GeoDataFrame.  If None,
                     loads via ``hifld_territories.load_territories()``.

    Returns:
        List of dicts, one per site, with keys:
          - site_id
          - utility_eia_id  (str | None)
          - utility_name    (str | None)
          - match_method    ("spatial" | "unmatched")
    """
    if territories is None:
        from voltregistry.ingest.hifld_territories import load_territories

        territories = load_territories()

    # Ensure territory GDF is in EPSG:4326
    if territories.crs is None:
        territories = territories.set_crs("EPSG:4326")
    elif territories.crs.to_epsg() != 4326:
        territories = territories.to_crs("EPSG:4326")

    if not sites:
        logger.warning("territory_join: no sites provided")
        return []

    # Build site GeoDataFrame
    site_points = gpd.GeoDataFrame(
        [
            {
                "site_id": s["site_id"],
                "lat": s["lat"],
                "lng": s["lng"],
                "geometry": Point(s["lng"], s["lat"]),  # (x=lng, y=lat)
            }
            for s in sites
        ],
        crs="EPSG:4326",
    )

    # Prepare territory index for join
    # Add area column for tie-break (prefer smaller / more specific polygon).
    # Project to an equal-area CRS for area computation, then revert to 4326.
    territories = territories.copy()
    _terr_ea = territories.to_crs("ESRI:54009")  # Mollweide equal-area
    territories["_area"] = _terr_ea.geometry.area
    territories = territories.sort_values("_area")  # ascending → specific first

    # Spatial join: each site point → territory polygon
    joined = gpd.sjoin(
        site_points,
        territories[["eia_id", "name", "_area", "geometry"]],
        how="left",
        predicate="within",
    )

    # De-duplicate: if a point falls in multiple overlapping polygons,
    # keep the row with the smallest polygon (most specific territory).
    joined = joined.sort_values("_area").drop_duplicates(subset=["site_id"], keep="first")

    # Build result index
    result_index: dict[str, dict[str, Any]] = {}
    for _, row in joined.iterrows():
        site_id = row["site_id"]
        eia_id = row.get("eia_id")
        name = row.get("name")
        matched = pd.notna(eia_id) and str(eia_id).strip() not in ("", "nan", "None", "0")
        result_index[site_id] = {
            "site_id": site_id,
            "utility_eia_id": str(eia_id).strip() if matched else None,
            "utility_name": str(name).strip() if matched else None,
            "match_method": "spatial" if matched else "unmatched",
        }

    # Build ordered output list
    results: list[dict[str, Any]] = []
    unmatched = 0
    for s in sites:
        sid = s["site_id"]
        if sid in result_index:
            results.append(result_index[sid])
            if result_index[sid]["match_method"] == "unmatched":
                unmatched += 1
                logger.debug(
                    "No territory match for site %s at (%.4f, %.4f)",
                    sid,
                    s["lat"],
                    s["lng"],
                )
        else:
            results.append(
                {
                    "site_id": sid,
                    "utility_eia_id": None,
                    "utility_name": None,
                    "match_method": "unmatched",
                }
            )
            unmatched += 1

    total = len(results)
    matched_count = total - unmatched
    logger.info(
        "Territory join complete: %d/%d sites matched (%.1f%%)",
        matched_count,
        total,
        100.0 * matched_count / total if total else 0,
    )
    if unmatched > 0:
        logger.warning(
            "%d sites could not be matched to a territory polygon — "
            "utility_eia_id will be None for these sites",
            unmatched,
        )

    return results
