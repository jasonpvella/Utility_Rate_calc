"""Unit tests for the geospatial territory join.

Uses three known-location sites with mock territory polygons — no file I/O,
no external data dependencies.  Covers:
  - Standard point-in-polygon match
  - Site at territory boundary (should match one of the polygons)
  - Site with no polygon match (→ utility_eia_id = None)
"""

import geopandas as gpd
from shapely.geometry import box

from voltregistry.mapping.territory_join import join_sites_to_territories


def _make_territories() -> gpd.GeoDataFrame:
    """Two non-overlapping rectangles covering parts of Arkansas and Texas."""
    return gpd.GeoDataFrame(
        [
            {
                "eia_id": "6452",
                "name": "Entergy Arkansas LLC",
                "state": "AR",
                "geometry": box(-94.6, 33.0, -89.7, 36.5),  # Arkansas bounding box
            },
            {
                "eia_id": "40229",
                "name": "Oncor Electric Delivery",
                "state": "TX",
                "geometry": box(-106.7, 25.8, -93.5, 36.5),  # Texas bounding box
            },
        ],
        crs="EPSG:4326",
    )


def test_standard_match():
    """A site inside Arkansas polygon → Entergy Arkansas."""
    sites = [{"site_id": "WMT-AR1", "lat": 35.0, "lng": -92.0}]
    territories = _make_territories()
    results = join_sites_to_territories(sites, territories=territories)
    assert len(results) == 1
    r = results[0]
    assert r["utility_eia_id"] == "6452"
    assert r["utility_name"] == "Entergy Arkansas LLC"
    assert r["match_method"] == "spatial"


def test_texas_match():
    """A site inside Texas polygon → Oncor."""
    sites = [{"site_id": "WMT-TX1", "lat": 32.78, "lng": -97.0}]
    territories = _make_territories()
    results = join_sites_to_territories(sites, territories=territories)
    r = results[0]
    assert r["utility_eia_id"] == "40229"
    assert r["match_method"] == "spatial"


def test_no_match():
    """A site outside all polygons → utility_eia_id is None."""
    sites = [{"site_id": "WMT-HI1", "lat": 21.0, "lng": -157.0}]  # Hawaii
    territories = _make_territories()
    results = join_sites_to_territories(sites, territories=territories)
    r = results[0]
    assert r["utility_eia_id"] is None
    assert r["match_method"] == "unmatched"


def test_multiple_sites():
    """Multiple sites processed correctly with correct ordering."""
    sites = [
        {"site_id": "WMT-AR1", "lat": 35.0, "lng": -92.0},
        {"site_id": "WMT-TX1", "lat": 32.78, "lng": -97.0},
        {"site_id": "WMT-HI1", "lat": 21.0, "lng": -157.0},
    ]
    territories = _make_territories()
    results = join_sites_to_territories(sites, territories=territories)
    assert len(results) == 3
    result_map = {r["site_id"]: r for r in results}
    assert result_map["WMT-AR1"]["utility_eia_id"] == "6452"
    assert result_map["WMT-TX1"]["utility_eia_id"] == "40229"
    assert result_map["WMT-HI1"]["utility_eia_id"] is None


def test_empty_sites():
    """Empty site list returns empty results."""
    territories = _make_territories()
    results = join_sites_to_territories([], territories=territories)
    assert results == []
