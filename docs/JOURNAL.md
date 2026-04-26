# Project Journal — VoltRegistry

## Executive Snapshot

**Current focus:** Phase 4 complete (eligibility, comparison engine, 3 API endpoints). Full URDB sweep done across all 1,778 DB utilities — 34,497 tariffs in DB. URDB store coverage ceiling reached at ~20% (1,203/6,119 stores). The major IOUs serving 80% of Walmart stores are simply not in URDB.

**Next session priorities:**
- **Decision required:** build direct utility scrapers for top 30 gap utilities (PDF/HTML tariff sheets → structured rate data) vs. proceed to Phase 5. Top 5 gaps alone (TVA, KCPL, NSP MN, Appalachian Power, Nevada Power) = 1,029 stores.
- Top 30 gap EIA IDs (sorted by store count): 38 45 4 35 25 28 27 6454 47 29 5420 44 43 15 21 48 36 37 39 3672 23 14 14469 2 33 14470 5 22 26 5418
- Golden tests use hand-calculated expected values (not utility-published sample bills). Replace with utility-certified worked examples before production.

---

## Historical Log

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
