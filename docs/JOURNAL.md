# Project Journal — VoltRegistry

## Executive Snapshot

**Current focus:** v0 build spec finalized at `VoltRegistry_v0_Spec.md`. Pivoted away from the original ChatGPT/Gemini PRD toward a URDB-foundational architecture with five reference utilities and no real-bill calibration. Project scaffolding (`CLAUDE.md`, `docs/JOURNAL.md`) just established.

**Next session priorities:**
- Kick off Phase 1 of the v0 spec: scrape Walmart + Sam's Club store finder, ingest HIFLD electric retail service territory shapefile, ingest EIA Form 861, and run the geospatial join to produce `(site_id → utility_eia_id)` for ~5,200 sites. Stop at the P1 demo gate before proceeding to P2.
- Decide whether the Walmart store list is scraped live or as a one-time cached snapshot (spec recommends cached).
- Stand up the empty Python package skeleton: `pyproject.toml`, `src/voltregistry/`, `alembic/`, `tests/`, `data/raw/`.

---

## Historical Log

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
