"""HIFLD Electric Retail Service Territory ingestion.

Production path
---------------
Downloads the HIFLD Electric Retail Service Territories shapefile from the
ArcGIS Open Data portal as GeoJSON and stores it at ``data/territories.gpkg``
as a GeoPackage for fast spatial queries.

HIFLD source:
  https://hifld-geoplatform.opendata.arcgis.com/
  Feature service URL (paged GeoJSON):
  https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/
    Electric_Retail_Service_Territories/FeatureServer/0/query

Each feature has attributes: ID, NAME, STATE, EIA_ID, TYPE (IOU/Coop/Muni/etc.)

The download is large (~50MB); it is broken into paged requests (1000 features
each) and assembled into a single GeoDataFrame.

Seed/fallback path
------------------
When the live download is blocked (sandboxed CI, restricted network), a
simplified GeoDataFrame is constructed from:
  - State bounding-box rectangles (one per utility per STATE_PRIMARY_UTILITIES)
  - Weighted partitioning of the bounding box among multiple utilities in a state

The spatial join will still work — just with state-level precision rather than
true territory polygons.  Boundaries between co-present utilities in the same
state are approximated by a simple east/west split of the bounding box.

Output: GeoPackage at ``data/territories.gpkg``
  Columns: eia_id (str), name (str), state (str), geometry (Polygon/MultiPolygon)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import box as shp_box

from voltregistry.ingest.seed_data import (
    MAJOR_UTILITIES,
    STATE_BOUNDS,
    STATE_PRIMARY_UTILITIES,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GPKG_PATH = _REPO_ROOT / "data" / "territories.gpkg"

# HIFLD ArcGIS REST endpoint
_HIFLD_BASE = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services"
    "/Electric_Retail_Service_Territories/FeatureServer/0/query"
)
_PAGE_SIZE = 1000


def _fetch_hifld_page(offset: int, session: Any) -> dict[str, Any]:
    params = {
        "where": "1=1",
        "outFields": "ID,NAME,STATE,EIA_ID,TYPE",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
        "resultOffset": str(offset),
        "resultRecordCount": str(_PAGE_SIZE),
    }
    resp = session.get(_HIFLD_BASE, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _download_hifld() -> gpd.GeoDataFrame:
    """Download full HIFLD territory dataset, paged."""
    import httpx

    all_features: list[dict[str, Any]] = []
    with httpx.Client(follow_redirects=True) as client:
        offset = 0
        while True:
            logger.info("HIFLD: fetching page offset=%d", offset)
            page = _fetch_hifld_page(offset, client)
            features = page.get("features", [])
            if not features:
                break
            all_features.extend(features)
            if len(features) < _PAGE_SIZE:
                break  # last page
            offset += _PAGE_SIZE

    logger.info("HIFLD: downloaded %d territory features", len(all_features))
    geojson = {"type": "FeatureCollection", "features": all_features}
    gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")

    # Normalize column names → eia_id, name, state
    col_map = {}
    for col in gdf.columns:
        low = col.lower()
        if low == "eia_id":
            col_map[col] = "eia_id"
        elif low == "name":
            col_map[col] = "name"
        elif low == "state":
            col_map[col] = "state"
    gdf = gdf.rename(columns=col_map)
    gdf["eia_id"] = gdf.get("eia_id", "").astype(str).str.strip()

    return gdf[["eia_id", "name", "state", "geometry"]]


def _build_seed_territories() -> gpd.GeoDataFrame:
    """Construct simplified territory polygons from state bounding boxes.

    For states with multiple utilities, the bounding box is partitioned
    east-to-west proportionally to each utility's ``fraction`` weight.
    """
    utility_lookup = {u["eia_id"]: u for u in MAJOR_UTILITIES}

    rows: list[dict[str, Any]] = []

    for state, utility_fractions in STATE_PRIMARY_UTILITIES.items():
        state_key = state.rstrip("_")  # strip disambiguation suffixes
        if state_key not in STATE_BOUNDS:
            continue
        lat_min, lat_max, lng_min, lng_max = STATE_BOUNDS[state_key]

        # Partition longitude range proportionally by utility fraction
        total_weight = sum(w for _, w in utility_fractions)
        lng_cursor = lng_min

        for eia_id, fraction in utility_fractions:
            lng_width = (lng_max - lng_min) * (fraction / total_weight)
            lng_next = lng_cursor + lng_width
            polygon = shp_box(lng_cursor, lat_min, lng_next, lat_max)
            lng_cursor = lng_next

            u = utility_lookup.get(eia_id, {})
            rows.append(
                {
                    "eia_id": eia_id,
                    "name": u.get("name", f"Utility {eia_id}"),
                    "state": state_key,
                    "geometry": polygon,
                }
            )

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    logger.info(
        "HIFLD seed: built %d simplified territory polygons across %d states",
        len(gdf),
        gdf["state"].nunique(),
    )
    return gdf


def load_territories(force_refresh: bool = False) -> gpd.GeoDataFrame:
    """Return the territory GeoDataFrame, cached as GeoPackage.

    Args:
        force_refresh: if True, re-download even if cache exists.

    Returns:
        GeoDataFrame with columns: eia_id, name, state, geometry (EPSG:4326)
    """
    _GPKG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Resolve effective cache path (may be redirected to /tmp on restricted filesystems)
    _pointer = _GPKG_PATH.with_suffix(".tmp_path")
    _effective_path = Path(_pointer.read_text().strip()) if _pointer.exists() else _GPKG_PATH

    if not force_refresh and _effective_path.exists() and _effective_path.stat().st_size > 4096:
        try:
            logger.info("HIFLD territories: using cache %s", _effective_path)
            return gpd.read_file(_effective_path)
        except Exception as exc:
            logger.warning("Cache read failed (%s) — regenerating", exc)

    # Production path: try real HIFLD download
    try:
        logger.info("HIFLD territories: attempting live download from ArcGIS")
        gdf = _download_hifld()
        _write_gpkg(gdf, _GPKG_PATH)
        return gdf
    except Exception as exc:
        logger.info("HIFLD live download failed (%s) — using seed territories", exc)

    # Seed path
    gdf = _build_seed_territories()
    _write_gpkg(gdf, _GPKG_PATH)
    return gdf


def _write_gpkg(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """Write GeoDataFrame to GeoPackage, falling back to /tmp on I/O error."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        gdf.to_file(path, driver="GPKG")
        logger.info("HIFLD: saved %d territories to %s", len(gdf), path)
    except Exception as exc:
        # Mounted filesystems (FUSE, NFS, some CI environments) can't handle
        # GeoPackage's embedded SQLite.  Fall back to /tmp and record the path.
        tmp = Path(tempfile.gettempdir()) / path.name
        logger.warning(
            "Could not write GPKG to %s (%s) — writing to %s instead",
            path,
            exc,
            tmp,
        )
        gdf.to_file(tmp, driver="GPKG")
        # Write a pointer so load_territories can find it next time
        pointer = path.with_suffix(".tmp_path")
        pointer.write_text(str(tmp))
        logger.info("HIFLD: saved %d territories to %s", len(gdf), tmp)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gdf = load_territories()
    print(f"Loaded {len(gdf)} territory polygons")
    print(gdf.head())
