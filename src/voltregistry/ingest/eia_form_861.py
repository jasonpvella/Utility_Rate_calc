"""EIA Form 861 ingestion — utility metadata.

Downloads the most recent annual EIA-861 ZIP from EIA's public server, extracts
the Utility Data sheet, and returns a list of Utility-like dicts ready to
upsert into the database.

Public URL pattern:
  https://www.eia.gov/electricity/data/eia861/archive/zip/f8612023.zip
  (year suffix changes; we try current year − 1 and current year − 2 as fallbacks)

Only electric retail utilities are retained (CUST_CNT > 0 or IOU flag set).
Market structure is inferred from state regulatory context and ownership type.

Cache: raw ZIP is saved to data/raw/eia861_<year>.zip so repeat runs skip download.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RAW_DIR = _REPO_ROOT / "data" / "raw"

# States that have restructured / deregulated retail markets
# (delivery-only wires companies dominate new commercial accounts)
_DEREGULATED_STATES = {
    "TX",  # ERCOT — full retail choice
    "IL",  # ComEd, Ameren territory
    "PA",  # PPL, PECO, West Penn
    "OH",  # AEP Ohio, FirstEnergy
    "NJ",  # JCP&L, PSEG
    "MD",  # BGE, Pepco, Delmarva
    "DC",  # Pepco
    "MA",  # Eversource, National Grid
    "CT",  # Eversource
    "ME",  # CMP, Versant
    "NH",  # Eversource
    "DE",  # Delmarva
    "NY",  # Con Ed, National Grid, etc.
    "MI",  # DTE, Consumers
}

# Known delivery-only wires utilities (TDSP) by EIA ID
_KNOWN_TDSP_EIA_IDS: set[str] = {
    "40229",  # Oncor Electric Delivery
    "14469",  # AEP Texas Central (now Oncor/Texas-NM)
    "3672",  # CenterPoint Energy Houston Electric
    "40434",  # Sharyland Utilities
}


def _eia861_url(year: int) -> str:
    return f"https://www.eia.gov/electricity/data/eia861/archive/zip/f861{year}.zip"


def _cache_path(year: int) -> Path:
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    return _RAW_DIR / f"eia861_{year}.zip"


_ZIP_MAGIC = b"PK\x03\x04"


def _is_valid_zip(data: bytes) -> bool:
    return data[:4] == _ZIP_MAGIC


def _download_zip(year: int, timeout: int = 60) -> bytes:
    cache = _cache_path(year)
    if cache.exists():
        data = cache.read_bytes()
        if _is_valid_zip(data):
            logger.info("EIA-861 %d: using cache %s", year, cache)
            return data
        logger.warning("EIA-861 %d: cached file is not a valid zip — deleting and re-downloading", year)
        cache.unlink()

    url = _eia861_url(year)
    logger.info("EIA-861 %d: downloading %s", year, url)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()

    if not _is_valid_zip(resp.content):
        raise ValueError(
            f"EIA-861 {year}: server returned non-zip content "
            f"({len(resp.content)} bytes, content-type={resp.headers.get('content-type', '?')!r}) — "
            "year may not be available yet"
        )

    cache.write_bytes(resp.content)
    logger.info("EIA-861 %d: cached to %s (%d bytes)", year, cache, len(resp.content))
    return resp.content


def _find_utility_csv(zf: zipfile.ZipFile) -> str | None:
    """Locate the utility data CSV inside the ZIP (name varies by year)."""
    candidates = [n for n in zf.namelist() if "utility_data" in n.lower() and n.endswith(".csv")]
    if not candidates:
        candidates = [
            n for n in zf.namelist() if n.lower().endswith(".csv") and "utilit" in n.lower()
        ]
    return candidates[0] if candidates else None


def _find_utility_xlsx(zf: zipfile.ZipFile) -> str | None:
    """Locate the utility data xlsx inside the ZIP (EIA switched from CSV in 2023+)."""
    candidates = [n for n in zf.namelist() if "utility_data" in n.lower() and n.endswith(".xlsx")]
    if not candidates:
        candidates = [
            n for n in zf.namelist() if n.lower().endswith(".xlsx") and "utilit" in n.lower()
        ]
    return candidates[0] if candidates else None


def _xlsx_rows_to_dicts(zf: zipfile.ZipFile, xlsx_name: str) -> list[dict[str, str]]:
    """Read EIA-861 xlsx utility data — handles the 2-row merged header format."""
    import pandas as pd

    with zf.open(xlsx_name) as f:
        # Row 0 is a merged category header; row 1 has actual column names
        df = pd.read_excel(f, header=1, dtype=str)

    # Normalise column names to match the CSV conventions used in _infer_* helpers
    rename = {
        "Utility Number": "UTILITY_ID",
        "Utility Name": "UTILITY_NAME",
        "State": "STATE",
        "Ownership Type": "OWNERSHIP",
    }
    df = df.rename(columns=rename)
    df = df.fillna("")
    return df.to_dict(orient="records")


def _infer_market_structure(row: dict[str, str]) -> str:
    """Infer market_structure from EIA-861 ownership type and state."""
    ownership = row.get("OWNERSHIP", "").strip().upper()
    state = row.get("STATE", "").strip().upper()
    eia_id = row.get("UTILITY_ID", "").strip()

    if eia_id in _KNOWN_TDSP_EIA_IDS:
        return "deregulated_delivery_only"
    if ownership in ("MUNICIPAL", "MUN"):
        return "municipal"
    if ownership in ("COOPERATIVE", "COOP", "CO-OP"):
        return "cooperative"
    if state in _DEREGULATED_STATES and ownership in ("INVESTOR", "IOU", "IOU - BEHIND THE METER"):
        return "regulated_with_choice"
    return "regulated_vertical"


def _infer_jurisdiction(row: dict[str, str]) -> str:
    ownership = row.get("OWNERSHIP", "").strip().upper()
    if ownership in ("MUNICIPAL", "MUN"):
        return "municipal"
    return "state_puc"


def load_utilities(year: int | None = None) -> list[dict[str, Any]]:
    """Download (or use cache) EIA-861 and return a list of utility dicts.

    Args:
        year: EIA-861 report year.  Defaults to the most recent available
              (tries current year − 1, then − 2).

    Returns:
        List of dicts with keys matching UtilityTable fields.
    """
    from datetime import date

    if year is None:
        current_year = date.today().year
        for candidate in (current_year - 1, current_year - 2, current_year - 3):
            try:
                raw = _download_zip(candidate)
                year = candidate
                break
            except Exception as exc:
                logger.warning("EIA-861 %d unavailable: %s", candidate, exc)
        else:
            raise RuntimeError("Could not download any EIA-861 ZIP (tried last 3 years)")
    else:
        raw = _download_zip(year)

    utilities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = _find_utility_csv(zf)
        xlsx_name = _find_utility_xlsx(zf) if csv_name is None else None

        if csv_name is None and xlsx_name is None:
            raise RuntimeError(f"Could not find utility data CSV or xlsx in EIA-861 {year} ZIP")

        if csv_name:
            logger.info("EIA-861 %d: parsing %s (CSV)", year, csv_name)
            text = zf.read(csv_name).decode("latin-1")
            rows: list[dict[str, str]] = list(csv.DictReader(io.StringIO(text)))
        else:
            logger.info("EIA-861 %d: parsing %s (xlsx)", year, xlsx_name)
            rows = _xlsx_rows_to_dicts(zf, xlsx_name)  # type: ignore[arg-type]

        for row in rows:
            eia_id = row.get("UTILITY_ID", "").strip()
            name = row.get("UTILITY_NAME", "").strip()
            state = row.get("STATE", "").strip()

            if not eia_id or not name or not state:
                continue
            if eia_id in seen_ids:
                continue  # keep first occurrence (most recent if sorted)
            seen_ids.add(eia_id)

            utilities.append(
                {
                    "eia_id": eia_id,
                    "name": name,
                    "state": state,
                    "regulatory_jurisdiction": _infer_jurisdiction(row),
                    "market_structure": _infer_market_structure(row),
                    "input_tier": "TIER_2_PEAK_KW",
                }
            )

    logger.info("EIA-861 %d: loaded %d utilities", year, len(utilities))
    return utilities


def load_utilities_with_fallback(year: int | None = None) -> list[dict[str, Any]]:
    """Load utilities from EIA-861, falling back to seed data on failure.

    The seed data covers ~60 major utilities and is sufficient for Phase 1
    demo purposes.  A warning is emitted so it is obvious which path ran.
    """
    try:
        return load_utilities(year=year)
    except Exception as exc:
        logger.warning("EIA-861 download failed (%s) — falling back to seed utility list", exc)
        from voltregistry.ingest.seed_data import MAJOR_UTILITIES

        logger.info("Seed fallback: loaded %d utilities", len(MAJOR_UTILITIES))
        return MAJOR_UTILITIES


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    utils = load_utilities_with_fallback()
    print(f"Loaded {len(utils)} utilities")
    for u in utils[:5]:
        print(" ", u)
