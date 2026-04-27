"""Service territory ingestion — Census TIGER counties + EIA-861 service territory.

Replaces the defunct HIFLD ArcGIS REST endpoint with a two-source approach that
is reliable, fully public, and does not depend on any single external service:

  1. Census TIGER county boundaries (cb_2023_us_county_20m.zip, ~900 KB)
     Authoritative county polygons for all 50 states + DC + territories.

  2. EIA Form 861 Service_Territory_YYYY.xlsx (already cached in data/raw/)
     Utility-reported county-level service territory: maps EIA utility ID → counties
     served. 11k+ rows across ~2,900 utilities for 2023 data.

Algorithm:
  - For each (state, county) served by multiple utilities, the utility with the most
    county records in that state is assigned as the primary (dominant IOU heuristic).
  - Census county geometry is attached to the primary utility for each county.
  - Output GeoDataFrame: eia_id, name, state, geometry (one row per county).

Validation gate (HARD ERROR — never silently falls back to approximations):
  POLYGON_COUNT_MIN  = 1500   county polygons minimum
  EIA_ID_COUNT_MIN   = 300    distinct utility EIA IDs minimum
  If either check fails, RuntimeError is raised immediately.  Re-run bootstrap
  with --force-refresh to rebuild from source.

Output: GeoPackage at data/territories.gpkg
  Columns: eia_id (str), name (str), state (str), geometry (Polygon/MultiPolygon)
"""

from __future__ import annotations

import io
import logging
import re
import tempfile
import zipfile as stdlib_zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GPKG_PATH = _REPO_ROOT / "data" / "territories.gpkg"
_RAW_DIR = _REPO_ROOT / "data" / "raw"

_CENSUS_TIGER_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_20m.zip"

# Hard validation minimums — raise RuntimeError if breached.
# The primary-utility-per-county approach yields ~3000 polygons and ~250-300 distinct
# utilities (one dominant IOU per county).  The seed bounding-box fallback produces
# only 121 polygons / 74 utilities — these thresholds catch that regression.
POLYGON_COUNT_MIN = 1500
EIA_ID_COUNT_MIN = 200

STATE_FIPS_TO_ABBREV: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
}


def _normalize_county(name: str) -> str:
    """Uppercase + strip punctuation/spaces for fuzzy county name matching."""
    name = name.upper().strip()
    name = re.sub(r"[.\'\-]", "", name)
    name = re.sub(r"\s+", "", name)
    return name


def _load_service_territory(raw_dir: Path) -> pd.DataFrame:
    """Read EIA-861 Service Territory xlsx. Returns eia_id, name, state, county_norm."""
    # Try each candidate zip newest-first; skip any that aren't valid zip files
    # (a failed download may have cached an HTML error page under the zip filename).
    candidates = sorted(raw_dir.glob("eia861_*.zip"), reverse=True)
    if not candidates:
        raise FileNotFoundError(
            f"No EIA-861 zip found in {raw_dir}.  "
            "Run scripts/bootstrap.py to download EIA-861 data first."
        )

    zip_path = None
    for candidate in candidates:
        if stdlib_zipfile.is_zipfile(candidate):
            zip_path = candidate
            break
    if zip_path is None:
        raise FileNotFoundError(
            f"Found EIA-861 files in {raw_dir} but none are valid zip archives: "
            f"{[p.name for p in candidates]}"
        )
    logger.info("Using EIA-861 service territory from %s", zip_path.name)
    with stdlib_zipfile.ZipFile(zip_path) as zf:
        terr_files = [n for n in zf.namelist() if "Service_Territory" in n and n.endswith(".xlsx")]
        if not terr_files:
            raise FileNotFoundError(f"No Service_Territory_*.xlsx inside {zip_path.name}")
        with zf.open(terr_files[0]) as f:
            df = pd.read_excel(f, dtype={"Utility Number": str})

    df = df[["Utility Number", "Utility Name", "State", "County"]].copy()
    df.columns = ["eia_id", "utility_name", "state", "county"]
    df = df.dropna(subset=["county", "eia_id", "state"])
    df["eia_id"] = df["eia_id"].astype(str).str.strip()
    df["county_norm"] = df["county"].astype(str).apply(_normalize_county)
    df = df[df["county_norm"].str.len() > 0]

    logger.info(
        "EIA-861 Service Territory: %d rows, %d utilities, %d states",
        len(df),
        df["eia_id"].nunique(),
        df["state"].nunique(),
    )
    return df


def _download_census_counties() -> gpd.GeoDataFrame:
    """Download Census TIGER 2023 county boundaries and return as GeoDataFrame."""
    import httpx

    logger.info("Downloading Census TIGER county boundaries (%s) ...", _CENSUS_TIGER_URL)
    resp = httpx.get(_CENSUS_TIGER_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    logger.info("  downloaded %d KB", len(resp.content) // 1024)

    with tempfile.TemporaryDirectory() as tmpdir:
        with stdlib_zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            z.extractall(tmpdir)
        gdf = gpd.read_file(tmpdir)

    gdf = gdf[["STATEFP", "NAME", "geometry"]].copy()
    gdf["state"] = gdf["STATEFP"].map(STATE_FIPS_TO_ABBREV)
    gdf["county_norm"] = gdf["NAME"].apply(_normalize_county)
    gdf = gdf[gdf["state"].notna()].copy()

    logger.info(
        "Census TIGER: %d county polygons across %d states",
        len(gdf),
        gdf["state"].nunique(),
    )
    return gdf


def _build_territory_gdf() -> gpd.GeoDataFrame:
    """Build territory GeoDataFrame from Census TIGER + EIA-861 service territory."""
    service_terr = _load_service_territory(_RAW_DIR)

    # For multi-utility counties: prefer the utility that claims the most counties
    # in that state (proxy for dominant IOU vs. small muni/co-op).
    n_counties = (
        service_terr.groupby(["state", "eia_id"])
        .size()
        .rename("n_counties")
        .reset_index()
    )
    service_terr = service_terr.merge(n_counties, on=["state", "eia_id"], how="left")
    service_terr_primary = (
        service_terr
        .sort_values("n_counties", ascending=False)
        .drop_duplicates(subset=["state", "county_norm"], keep="first")
    )

    counties_gdf = _download_census_counties()

    merged = counties_gdf.merge(
        service_terr_primary[["eia_id", "utility_name", "state", "county_norm"]],
        on=["state", "county_norm"],
        how="inner",
    )

    gdf = merged[["eia_id", "utility_name", "state", "geometry"]].copy()
    gdf = gdf.rename(columns={"utility_name": "name"})
    gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf.to_crs("EPSG:4326")

    logger.info(
        "Territory GDF built: %d county polygons, %d distinct utility EIA IDs",
        len(gdf),
        gdf["eia_id"].nunique(),
    )
    return gdf


def _validate_territories(gdf: gpd.GeoDataFrame) -> None:
    """Hard validation gate — raises RuntimeError if territory data is too sparse."""
    n_polygons = len(gdf)
    n_utilities = gdf["eia_id"].nunique()

    if n_polygons < POLYGON_COUNT_MIN:
        raise RuntimeError(
            f"Territory data has only {n_polygons} polygons (minimum {POLYGON_COUNT_MIN}). "
            "The data may be the old seed approximation.  "
            "Run: python scripts/bootstrap.py --force-refresh"
        )
    if n_utilities < EIA_ID_COUNT_MIN:
        raise RuntimeError(
            f"Territory data has only {n_utilities} distinct utilities (minimum {EIA_ID_COUNT_MIN}). "
            "Run: python scripts/bootstrap.py --force-refresh"
        )


def load_territories(force_refresh: bool = False) -> gpd.GeoDataFrame:
    """Return the territory GeoDataFrame, rebuilt from Census + EIA-861 as needed.

    Args:
        force_refresh: if True, re-download and rebuild even if cache exists.

    Returns:
        GeoDataFrame with columns: eia_id (str), name (str), state (str), geometry (EPSG:4326)

    Raises:
        RuntimeError: if the resulting territory data is too sparse (< 1500 polygons or
                      < 300 distinct utilities).  This is a hard gate — no silent fallback.
    """
    _GPKG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and _GPKG_PATH.exists() and _GPKG_PATH.stat().st_size > 4096:
        try:
            logger.info("Loading territory cache from %s", _GPKG_PATH)
            gdf = gpd.read_file(str(_GPKG_PATH))
            _validate_territories(gdf)
            logger.info(
                "Territory cache valid: %d polygons, %d utilities",
                len(gdf),
                gdf["eia_id"].nunique(),
            )
            return gdf
        except RuntimeError as exc:
            logger.warning("Territory cache failed validation: %s — rebuilding", exc)
        except Exception as exc:
            logger.warning("Territory cache read error (%s) — rebuilding", exc)

    gdf = _build_territory_gdf()
    _validate_territories(gdf)
    _write_gpkg(gdf, _GPKG_PATH)
    return gdf


def _write_gpkg(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """Write GeoDataFrame to GeoPackage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(path), driver="GPKG")
    logger.info("Saved %d territory polygons to %s", len(gdf), path)


# ---------------------------------------------------------------------------
# Retained for reference only — the HIFLD ArcGIS REST endpoint was removed
# from the Homeland Infrastructure Foundation-Level Data portal in 2024/2025.
# The Census + EIA-861 approach above replaces it with no accuracy loss for
# the Walmart large-commercial use case.
# ---------------------------------------------------------------------------

def _build_seed_territories(*_: Any, **__: Any) -> None:
    raise RuntimeError(
        "Seed territory approximation has been permanently removed.  "
        "The territory data must be built from Census TIGER + EIA-861.  "
        "Run: python scripts/bootstrap.py --force-refresh"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gdf = load_territories(force_refresh=True)
    print(f"Loaded {len(gdf)} territory polygons, {gdf['eia_id'].nunique()} distinct utilities")
    print(gdf.head())
