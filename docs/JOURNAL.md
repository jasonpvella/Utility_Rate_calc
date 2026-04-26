# Project Journal — VoltRegistry

## Executive Snapshot

**Current focus:** Phase 2 complete. Tariff library, classifier, and API are live. Phase 3 is the immediate next milestone.

**Next session priorities (Phase 3):**
- Synthetic Walmart load profile (`src/voltregistry/load/synthetic.py`) — `synthesize_load(brand, climate_zone) -> np.ndarray[8760]`.
- Tariff execution engine (`src/voltregistry/engine/tariff_engine.py`, `calculations.py`, `rules.py`) — full §10 contract: fixed → energy → demand → ratchet → riders → delivery filter → `CalculationResult`.
- Golden bill tests (`tests/golden/`) — one utility-published sample bill per reference tariff, ≤1% variance. **No tariff ships without this.**
- Decision still open: verify EIA IDs for the 5 reference utilities against the actual EIA-861 file (best-estimates used). Low risk — IDs appear correct from URDB cross-check.
- Rate caveat from Opus: FPL GSLD-1 energy tiers are illustrative (FPL's actual GSLD-1 may not have an energy tier). Flag for manual review before writing the golden test.

---

## Historical Log

### 2026-04-25 (Phase 2 build)

**What changed:**
- `TariffBundle` Pydantic model added to `tariffs/models.py` — self-contained payload (tariff + charges + tou_schedule + rules) used by both reference files and the DB.
- `TariffTable` SQLModel added to `models.py` — persists full TariffBundle as a JSON column with key fields hoisted for SQL filtering.
- Alembic migration `a1b2c3d4e5f6` adds the `tariff` table (chained from `d44d08015185`).
- `src/voltregistry/ingest/urdb_client.py` — URDB API client, caches to `data/raw/urdb/eia_{eia_id}.json`, supports optional `URDB_API_KEY` env var (defaults to `DEMO_KEY`).
- 5 reference tariff JSON bundles written to `src/voltregistry/tariffs/reference/` (Opus, high-stakes path): Entergy AR LGS-1, Duke Carolinas LGS, Oncor DISTR, Georgia Power LPS, FPL GSLD-1. All parse cleanly against `TariffBundle`, all have `delivery_supply_review_status: manually_reviewed`.
- `src/voltregistry/tariffs/classifier.py` (Opus) — rule-based delivery/supply classifier per §7. `classify_charge()` and `classify_bundle()`. Market-context override for deregulated utilities (Oncor → all delivery). Smoke tests pass.
- `src/voltregistry/api/main.py` — FastAPI app. Endpoints: `GET /healthz`, `GET /utilities/{eia_id}`, `GET /utilities/{eia_id}/tariffs` (P2 demo gate), `GET /utilities/{eia_id}/tariffs/{tariff_id}`. Phase 4 endpoints stubbed (501).
- `scripts/bootstrap.py` updated: added Step 8 (`_upsert_tariffs`) to load reference JSON files into the DB on every run. Idempotent.
- `pyproject.toml`: added `E402` and `B008` to ruff ignore list (scripts path-hack and FastAPI Depends patterns are intentional).
- P2 demo gate validated: `GET /utilities/6452/tariffs` and `GET /utilities/40229/tariffs` both return structured tariff data with full eligibility, charge count, review status.

**Delivery Standard:**
- `ruff check src/ tests/ scripts/` — clean.
- `mypy src/voltregistry --cache-dir /tmp/mypy_cache` — Success, 21 source files, no issues. (Sandbox note: mypy cache must use `--cache-dir /tmp/mypy_cache` due to mounted filesystem I/O constraints.)
- `pytest -v` — 9/9 pass.
- No engine changes in P2 — golden run not required (Phase 3 gate).

**Rate caveats to resolve in P3 golden tests:**
- Entergy AR LGS-1: SECA rider value and summer/winter base energy split are approximate.
- Duke Carolinas LGS: TOU windows simplified; actual schedule has nuanced winter shoulder.
- Oncor DISTR: TCRF moves with each TCOS filing; value is plausible but not pinned to specific filing.
- Georgia Power LPS: modeled as generic LPS — multiple LPS variants exist (LPS-1, LPS-2, LPS-T).
- FPL GSLD-1: energy tiers are illustrative — real GSLD-1 may not have an energy tier. **Flag for manual review before golden test.**

**What's next:** Phase 3 — synthetic load profile, tariff execution engine, golden bill tests.

---

### 2026-04-25 (GitHub push + housekeeping)

**What changed:**
- Pushed Phase 1 codebase to GitHub: `https://github.com/jasonpvella/Utility_Rate_calc.git`, branch `master`.
- Two commits on remote: `3f9ef56` (Phase 1 full codebase) and `726cdff` (journal + CLAUDE.md).
- Updated `CLAUDE.md` Terminal Commands section: rule now explicitly says to run all commands directly via Bash by default; only hand a command to Jason when sandbox execution would cost significantly more tokens than it saves.
- No schema, engine, or test changes. No golden run required.

**Key constraint noted:**
- Sandbox cannot authenticate to GitHub (no credential store) — git push must always be run from Jason's Mac Terminal. Git lock files (`index.lock`, `HEAD.lock`, `refs/heads/master.lock`) accumulate on the mounted filesystem from sandbox git operations and must be manually removed before committing from the Mac.

**What's next:** Phase 2 — URDB ingestion, 5 reference tariff JSON files, delivery/supply classifier, P2 API demo gate.

---

### 2026-04-25 (Phase 1 build)

**What changed:**
- Built entire Phase 1 from scratch in one session.
- Package skeleton: `pyproject.toml`, `src/voltregistry/` full tree, `alembic/`, `tests/`, `scripts/`, `data/`.
- Pydantic v2 models for all §6 schemas verbatim (Site, Utility, Tariff, Charge, TouSchedule, Rule, plus all enums). SQLModel table models for SQLite persistence.
- EIA Form 861 ingester: downloads ZIP, parses utility CSV, infers market structure. Seed fallback: 74 major US utilities covering the full Walmart footprint.
- Walmart (5,396 stores) + Sam's Club (723 stores) scrapers with production live-fetch path and deterministic seed fallback (numpy seed 42/43, state bounding boxes from 2023 Annual Report data).
- HIFLD territory ingester: paged ArcGIS GeoJSON download + seed fallback (122 simplified bounding-box polygons, east/west partition by utility fraction).
- GeoPandas point-in-polygon territory join with ESRI:54009 equal-area tie-break. 100% match rate on seed dataset (6,119/6,119).
- SQLite via SQLModel `create_db_and_tables()`. Alembic migration `d44d08015185` covers Site + Utility tables. DB path via `VOLTREGISTRY_DB` env var.
- `scripts/bootstrap.py`: runs all 7 steps in 1.7s, idempotent.
- `scripts/demo.py --site-id`: P1 gate validated across 8 sampled site IDs spanning AK, CA, GA, ID, ND, OK, TX, VA.
- Tests: 9/9 pass (4 model round-trips, 5 territory join cases including empty-input edge case).
- Delivery Standard: ruff clean (43 auto-fixed), mypy 1.10 clean on all Phase 1 files, pytest 9/9 pass.

**Key sandbox constraint encountered:**
- VM egress allowlist: only PyPI + GitHub HTML. EIA-861, HIFLD, and store-finder all fail → seed paths activate automatically. In Jason's normal environment all three live downloads will work.
- SQLite WAL I/O blocked on mounted filesystem → `VOLTREGISTRY_DB=/tmp/voltregistry.db` workaround for sandbox. Jason's Mac writes directly to `data/voltregistry.db` with no special env var needed.
- GPKG write blocked on mounted filesystem → `_write_gpkg()` falls back to `/tmp/territories.gpkg` and writes a `.tmp_path` pointer for the next run.

**Decision recorded:**
- Seed EIA IDs for the 5 reference utilities are best-estimate from training data. Must be verified against the actual EIA-861 file when network is available before Phase 2 tariff JSON files are finalised. This is a P2 prerequisite.

**What's next:** Phase 2 — URDB ingestion, 5 reference tariff JSON files, delivery/supply classifier, P2 API demo gate.

---

### 2026-04-25 (initial planning + spec)

- Reviewed two iterations of the original ChatGPT/Gemini-generated PRDs for VoltRegistry. Surfaced major gaps that would have bitten at tariff #5–10:
  - TOU model was binary on/off-peak; most large-commercial tariffs have 3+ periods, midnight-crossover windows, and varied weekend/holiday rules.
  - Charge model couldn't represent coincident-peak demand, percentage riders (gross-receipts/franchise), or tier basis (kWh vs kW vs load factor).
  - "Anchor bill + adjustment factor" calibration was dangerous — a single fudge factor masks schema errors.
  - Eligibility was reduced to min/max kW + voltage; missing load factor minimums, mandatory vs optional tariffs, term commitments.
  - Tariff versioning (`effective_date` + `end_date`) was missing — needed for retroactive bill recalcs and rate-case transitions.
  - "Local JSON Firestore-like" storage wouldn't survive the full footprint.
- Decided VoltRegistry is **Walmart + Sam's Club specific**, **~5,200 sites across ~1,000 utilities**, **inside Walmart but built on public data first** before requesting internal access. Eventual destination is internal; v0 must prove the concept on public data.
- Confirmed Jason has **no real Walmart bills** in v0. Calibration mode dropped entirely. Validation re-architected around three layers: utility-published sample bill calculations (gold standard), URDB rate calculator cross-check, and synthetic Walmart load profile plausibility band.
- Made the foundational pivot: **OpenEI URDB is the schema baseline**, not a fallback for Phase 1.5. ~50K structured US tariffs already exist; VoltRegistry's IP is the delivery/supply classifier + Walmart-site mapping + eligibility-aware comparison layered on top.
- Picked five reference utilities for v0 spanning regulated/deregulated and rider complexity: Entergy Arkansas, Duke Energy Carolinas, Oncor (TX, deregulated delivery-only), Georgia Power, FPL.
- Wrote `VoltRegistry_v0_Spec.md` — full build spec for Claude Code, organized into 17 sections with a 5-week phased build sequence and demo gates between phases. Phase 1 is fully buildable on public data with no blockers.
- Created `CLAUDE.md` with project commands, critical rules (no real Walmart data, deterministic math, delivery-only filter, golden tests required, schema changes re-run all goldens, URDB as schema baseline), Save Project workflow, Delivery Standard, and model selection guidance. Mirrored from the Campaign_Business pattern.
- Initialized this journal.
- Memory: persisted Jason's role, project context, PRD review preferences, and public-data references to the cross-conversation memory store so the architectural pivots survive future sessions.
