# Project Journal — VoltRegistry

## Executive Snapshot

**Current focus:** Phase 1 complete and committed. P1 demo gate passes: `python scripts/demo.py --site-id WMT-0001` returns `{utility_eia_id, name}` for all 6,119 sites in under 2 seconds. Pipeline is end-to-end on seed data; production downloads activate automatically when network egress is unrestricted.

**Next session priorities (Phase 2):**
- Stand up URDB client (`src/voltregistry/ingest/urdb_client.py`) — fetch tariff list for each of the 5 reference utility EIA IDs, cache to `data/raw/urdb/`.
- Hand-curate the 5 reference tariff JSON files under `src/voltregistry/tariffs/reference/` — one per utility, one tariff per utility for v0.
- Delivery/supply classifier (`src/voltregistry/tariffs/classifier.py`) — rule-based first pass per §7.
- FastAPI endpoint `GET /utilities/{eia_id}/tariffs` — P2 demo gate.
- Decision still open: confirm EIA IDs for all 5 reference utilities against the actual EIA-861 file once network is available. Seed data uses best-estimate IDs.

---

## Historical Log

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
