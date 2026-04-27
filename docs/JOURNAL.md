# Project Journal — VoltRegistry

## Executive Snapshot

**Current focus:** Tariff Inputs Registry pipeline — for every utility serving Walmart/Sam's Club stores, identify which of 15 standardized billing inputs each applicable rate schedule requires.

**What's built and working:**
- Territory pipeline replaced: Census TIGER 2023 + EIA-861 Service Territory. Yields 3,093 polygons, 233 distinct utilities mapped to stores, 84.4% match rate. Hard validation gates prevent silent degradation.
- `utility_tariff_url` + `utility_tariff_inputs` tables migrated and in DB.
- 46 URLs seeded for top 30 gap utilities.
- `scripts/extract_tariff_inputs.py` ready to run from Jason's Mac.

**Next session priorities:**
1. Run extraction from Jason's Mac: `python scripts/extract_tariff_inputs.py --limit 5 --eia-id 38,45,4,35,25` — verify output before bulk run
2. Review extracted rows with `confidence < 0.7`; manually correct if needed
3. Bulk run: `python scripts/extract_tariff_inputs.py` across all 46 pending URLs
4. Golden tests still use hand-calculated expected values — replace with utility-published sample bills before production

---

## Historical Log

### 2026-04-26 (Territory pipeline fix + CLAUDE.md hardened)

**Problem diagnosed:** Territory join was producing only 74 distinct utilities instead of expected 400–800+. Root cause: the HIFLD ArcGIS REST endpoint for Electric Retail Service Territories was permanently removed from `services1.arcgis.com` in 2024/2025. Bootstrap was silently falling back to state bounding-box seed approximations (~74 utilities, ~121 polygons). Tell-tale sign: "Dunn County Electric Coop" appearing as dominant utility for large numbers of stores.

**What changed:**

*`src/voltregistry/ingest/hifld_territories.py` — complete rewrite:*
- Replaced defunct HIFLD endpoint with Census TIGER 2023 county boundaries + EIA-861 Service Territory xlsx.
- `_load_service_territory()` — reads `Service_Territory_YYYY.xlsx` from `data/raw/eia861_YYYY.zip`; 11,782 rows, 2,912 utility EIA IDs.
- `_download_census_counties()` — downloads `cb_2023_us_county_20m.zip` from `census.gov` (~900 KB), returns GeoDataFrame of all US counties.
- `_build_territory_gdf()` — primary-utility-per-county heuristic: for multi-utility counties, assign the utility with the most counties in that state.
- `_validate_territories()` — hard validation gate: raises `RuntimeError` if < 1500 polygons or < 200 distinct utility EIA IDs. No silent fallback.
- `_build_seed_territories()` stub now raises `RuntimeError` — seed approximation permanently removed.
- `POLYGON_COUNT_MIN = 1500`, `EIA_ID_COUNT_MIN = 200` exported as constants.

*`src/voltregistry/ingest/eia_form_861.py`:*
- `_is_valid_zip()` — validates PK magic bytes before caching downloaded content.
- `_download_zip()` — deletes and re-downloads if cached file is not a valid zip; raises `ValueError` instead of caching HTML error pages.
- `_find_utility_xlsx()` + `_xlsx_rows_to_dicts()` — xlsx support for EIA-861 2023+ (EIA switched from CSV).

*`scripts/bootstrap.py`:*
- Imports `POLYGON_COUNT_MIN, EIA_ID_COUNT_MIN` from `hifld_territories` — no more hardcoded thresholds.
- `sys.exit(1)` if territory checks fail; `sys.exit(1)` if distinct utilities mapped to stores < 75.
- Summary block always prints polygon count and distinct utility count.

*`CLAUDE.md`:*
- Added **Territory Data** section documenting Census TIGER + EIA-861 approach, hard validation thresholds, expected bootstrap numbers, county-level ceiling explanation (~230 utilities mapped to stores), and `--force-refresh` recovery command.
- Updated Critical Rule 1: changed HIFLD reference to Census TIGER.
- Updated bootstrap docstring: "HIFLD" → "Census TIGER + EIA-861".

**Bootstrap results after fix:**
```
Territory polygons  :  3,093
Territory utilities :    278  distinct EIA IDs in territory data
Site→utility match  :  84.4%  (4,793 / 5,685 stores)
Distinct utils mapped:   233  county-level ceiling
```

**Delivery Standard:** ruff clean, mypy clean, pytest 94/94.

**What's next:** Run tariff extraction pipeline from Jason's Mac.

---

### 2026-04-26 (Tariff Inputs Registry pivot — Phases 0+1+2 pipeline built)

**Decision made:** Instead of building full tariff bundles for the 30 gap utilities, build a lighter "inputs registry" — for each utility + schedule, classify which of 15 standardized billing inputs are required. This is a classification task, not a full tariff digitization, and can be done at scale with Claude.

**What changed:**

*Phase 0 — Input taxonomy + schema:*
- `src/voltregistry/tariffs/input_types.py` (NEW) — `TariffInputType` enum (15 types: monthly_kwh, billing_demand_kw, onpeak_demand_kw, offpeak_demand_kw, onpeak_kwh, offpeak_kwh, shoulder_kwh, coincident_peak_kw, reactive_demand_kvar, power_factor_pct, contract_demand_kw, ratchet_demand_kw, voltage_level, load_factor_pct, billing_period_days) + `INPUT_TYPE_DESCRIPTIONS` dict.
- `src/voltregistry/models.py` (UPDATED) — Added `UtilityTariffUrlTable` (tracks tariff source URLs per utility, status lifecycle: pending → extracted/failed/reviewed) and `UtilityTariffInputsTable` (one row per schedule: schedule_code, schedule_name, applicability_min/max_kw, voltage_levels, inputs_required, confidence, raw_extraction).
- `alembic/versions/f7a9b2c1d3e4_add_tariff_url_and_inputs_tables.py` (NEW) — migration creating both tables. **Not yet applied.**

*Phase 1 — URL Registry:*
- `scripts/seed_tariff_urls.py` (NEW) — seeds ~68 URL rows covering 29 EIA IDs (top 30 gap utilities by store count). Two URL types per utility where applicable: utility tariff page (`tariff_page`) and state PUC portal (`portal`). Idempotent — safe to re-run. **Not yet run.**

*Phase 2 — Extraction pipeline:*
- `scripts/extract_tariff_inputs.py` (NEW) — CLI script. Fetches pending URLs (HTML or PDF), calls Claude API with a structured extraction prompt targeting Walmart-scale (≥100 kW) schedules, parses JSON response, validates inputs against `VALID_INPUT_TYPES`, writes to `utility_tariff_inputs`. Flags `confidence < 0.7` rows as `needs_review`. Flags are: `--eia-id`, `--dry-run`, `--limit`, `--reprocess`, `--model`. **Not yet run.**

**Status:** All code written, none of it applied or executed. Must run alembic upgrade → seed → extraction in that order.

**What's next:** Apply migration + run extraction for top 5 gap utilities first; verify output before bulk run across all 30.

---

### 2026-04-26 (Phase 4 complete + full URDB sweep + coverage analysis)

**What changed:**

*Phase 4 — eligibility, comparison, API:*
- `src/voltregistry/tariffs/eligibility.py` (NEW) — `check_eligibility()` returns `EligibilityResult(eligible, reasons, warnings)`. Hard exclusions: closed_to_new, expired, wrong voltage, below min_kw, above max_kw, wrong customer_class. Soft warnings: load_factor_min, term_commitment_months, missing estimated_peak_kw.
- `src/voltregistry/comparison/compare.py` (NEW) — `run_comparison(site_id, session) → ComparisonResult`. State→IECC climate zone map for all 50 states. Synthesizes load profile, filters eligible tariffs, runs TariffEngine, ranks by delivery cost. `ComparisonResult.to_dict()` serializes to §11 API shape.
- `src/voltregistry/api/main.py` (UPDATED) — Added `GET /sites` (paginated, filterable by state/brand), `GET /sites/{site_id}` (site + utility + current tariff summary), `POST /sites/{site_id}/compare` (full §11 response).
- `tests/test_eligibility.py` (NEW) — 22 unit tests covering all hard exclusions, soft warnings, edge cases.

*EIA ID bug fixes (3 of 5 reference utilities had wrong IDs from seed_data.py):*
- Entergy AR: 6452 → 814; FPL: 6455 → 6452; Oncor: 40229 → 44372
- Fixed in JSON files, `seed_data.py` (REFERENCE_UTILITIES + STATE_PRIMARY_UTILITIES), and `bootstrap.py` (_upsert_tariffs now updates utility_eia_id on existing rows).
- `scripts/reload_reference_tariffs.py` (NEW) — standalone script to reload reference tariffs into DB without running full bootstrap.

*URDB infrastructure:*
- `src/voltregistry/ingest/urdb_client.py` — added per-request delay (0.1s for real key, 2.0s for DEMO_KEY), retry loop with 429 handling and Retry-After header support.
- `scripts/ingest_urdb_bulk.py` — added `--list EIA_ID,...` and `--skip-cached` flags.
- `scripts/coverage_report.py` (NEW) — per-state store coverage breakdown, top-N gap utilities, priority EIA ID list.

*Full URDB sweep results (NREL API key, 4s delay, ~2,700s elapsed):*
- 1,778 utilities processed, 1,169 with commercial tariffs (65.7% URDB coverage).
- **34,497 URDB tariffs across 1,155 utilities** now in DB.
- 0 fetch errors, 0 conversion failures.
- Rate-limit lesson: NREL free key allows ~38 req/min sustained; 0.1s delay hits 429 after ~570 requests with 900s Retry-After. Use URDB_REQUEST_DELAY=4 for clean uninterrupted runs.

**Coverage finding:**
- Store coverage: **19.7% (1,203/6,119 stores)** — this is the URDB ceiling, not a data quality issue.
- The 63 major IOUs that serve 80% of Walmart stores (TVA, NSP MN, KCPL, Appalachian Power, PG&E, etc.) return 0 results from URDB.
- Top 5 gap utilities: TVA (308 stores), KCPL (227), NSP MN (173), Appalachian/AEP (161), Nevada Power (160) = 1,029 stores uncovered.
- States with coverage: AL 69%, NC 88%, SC 79%, FL 64%, GA 63%, PR 100%, AR 53%, TX 20%, MS 21%, TN 22%.

**Delivery Standard:**
- `ruff check src/ tests/` — clean
- `mypy src/voltregistry` — no issues
- `pytest -q` — 94/94 pass
- `pytest -q tests/golden/` — 25/25 pass, all 0.0000% variance

**What's next:** Decision — build utility PDF/HTML scrapers for top 30 gap utilities vs. proceed to Phase 5.

---

### 2026-04-26 (EIA-861 xlsx fix + bootstrap prerequisite)

**What changed:**
- `CLAUDE.md` — clarified terminal command policy: two explicit exceptions (git always Jason's Mac; external-network scripts like bootstrap/URDB also Jason's Mac due to proxy restrictions). Default is still to run everything from sandbox.
- `src/voltregistry/ingest/eia_form_861.py`:
  - `_download_zip` — added zip magic-byte validation (`PK` header check) before caching.
  - `_find_utility_xlsx(zf)` — new helper to locate `Utility_Data_*.xlsx` inside the ZIP (EIA switched from CSV to xlsx in 2023+).
  - `_xlsx_rows_to_dicts(raw)` — new helper: reads xlsx with pandas, renames columns to CSV-compatible keys.
  - `load_utilities` — tries xlsx fallback when no CSV found in ZIP.
- `pyproject.toml` — added `openpyxl>=3.1.0`.

**Bootstrap results:** EIA-861 2023 downloaded; 74 seed → 1,712 utilities upserted.

**Delivery Standard:** ruff clean, mypy clean, pytest 72/72 pass.

**What's next:** Phase 4 — eligibility engine, comparison engine, API endpoints.

---

### 2026-04-25 (environment setup + live URDB smoke test)

**What changed:**
- `src/voltregistry/db.py` — added `get_db_url() -> str` helper.
- `.venv/` created on Jason's Mac using `/opt/homebrew/bin/python3.11`.

**Live smoke test results:** 1,178 tariffs inserted from 74-utility seed in 21.6s. 9.5% URDB coverage on seed set.

**Delivery Standard:** pytest 72/72, golden 25/25 at 0.0000%.

**What's next:** Full bootstrap + URDB sweep, then Phase 4.

---

### 2026-04-25 (URDB adapter + bulk ingestion)

**What changed:**
- `src/voltregistry/ingest/urdb_to_bundle.py` — `urdb_to_bundle(raw, eia_id, market_structure) -> TariffBundle | None`. Handles fixed, flat/TOU/tiered energy, NCP demand, CP demand (proxied), minimum-charge rules, TOU schedule from 12×24 matrices.
- `scripts/ingest_urdb_bulk.py` — CLI bulk ingest with `--eia`, `--limit`, `--refresh`, `--debug` flags.
- `tests/test_urdb_adapter.py` — 20 unit tests across 5 fixture classes.
- `src/voltregistry/tariffs/models.py` — `HourRange.end` constraint changed from `le=23` to `le=24` (end-exclusive convention).

**Delivery Standard:** ruff clean, mypy clean, pytest 72/72, golden 25/25 at 0.0000%.

**What's next:** Phase 4 — eligibility engine, comparison engine, API endpoints.

---

### 2026-04-25 (Phase 3 build)

**What changed:**
- `src/voltregistry/load/synthetic.py` — `synthesize_load(brand, climate_zone) -> np.ndarray[8760]`. Formula-driven, deterministic. Targets: Walmart 6,500 MWh/yr, Sam's Club 5,700 MWh/yr.
- `src/voltregistry/engine/calculations.py` — stateless helpers: `build_tou_map` (NERC holiday-aware), fixed/energy/demand/rider calculations, all returning `MonthlyLineItem` with `calculation_basis`.
- `src/voltregistry/engine/rules.py` — `apply_demand_ratchet` (rolling + seasonal) and `apply_minimum_charge`.
- `src/voltregistry/engine/tariff_engine.py` — `TariffEngine` class. Calculation order: aggregate → TOU → ratchet → fixed → energy → demand → riders → delivery filter → `CalculationResult`.
- `tests/golden/test_golden.py` — 25 golden tests (5 tariffs × 5 checks). All 0.0000% variance.

**Golden variance report (constant 1,000 kW profile):**
| Tariff | Expected | Actual | Variance |
|---|---|---|---|
| entergy-ar-lgs1 | $152,820.00 | $152,820.00 | 0.0000% |
| oncor-distr | $114,528.24 | $114,528.24 | 0.0000% |
| duke-carolinas-lgs | $185,640.00 | $185,640.00 | 0.0000% |
| georgia-power-lps | $293,373.66 | $293,373.66 | 0.0000% |
| fpl-gsld | $137,355.60 | $137,355.60 | 0.0000% |

**Delivery Standard:** ruff clean, mypy clean, pytest 34/34, golden 25/25 at 0.0000%.

**What's next:** Phase 4.

---

### 2026-04-25 (Phase 2 build)

**What changed:**
- `TariffBundle` + `TariffTable` models. Alembic migration for tariff table.
- 5 reference tariff JSON bundles (Entergy AR, Duke Carolinas, Oncor, Georgia Power, FPL).
- `src/voltregistry/tariffs/classifier.py` — delivery/supply classifier per §7.
- `src/voltregistry/api/main.py` — FastAPI with healthz, utility, and tariff endpoints.
- `scripts/bootstrap.py` Step 8 — loads reference JSONs into DB.

**Delivery Standard:** ruff clean, mypy clean, pytest 9/9.

**What's next:** Phase 3.

---

### 2026-04-25 (GitHub push + housekeeping)

- Pushed Phase 1 to GitHub. Updated CLAUDE.md terminal command policy.
- Key constraint: sandbox cannot authenticate to GitHub — git push from Jason's Mac only.

---

### 2026-04-25 (Phase 1 build)

- Built entire Phase 1 from scratch: package skeleton, Pydantic models, EIA-861 ingester, store scrapers, HIFLD territory ingester, GeoPandas territory join, SQLite, bootstrap.py, demo.py.
- 100% site-to-utility match rate (6,119/6,119). Tests 9/9.

---

### 2026-04-25 (initial planning + spec)

- Reviewed PRDs, surfaced gaps in TOU model, charge model, calibration approach, eligibility, versioning.
- Decided: Walmart + Sam's Club specific, public data first, URDB as schema baseline.
- Picked 5 reference utilities. Wrote VoltRegistry_v0_Spec.md and CLAUDE.md.
