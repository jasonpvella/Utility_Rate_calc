# VoltRegistry — Claude Code Instructions

## Terminal Commands

Always run terminal commands directly via Bash rather than asking Jason to run them. The one exception: if a command is trivial to type (e.g. a single short `git` command) and running it from the sandbox would cost significantly more tokens than it saves in back-and-forth, hand it to Jason with the exact command to paste — but this should be rare. Default is always to run it yourself.

Use Bash directly for:
- Type check: `mypy src/voltregistry`
- Lint: `ruff check src/ tests/`
- Format: `ruff format src/ tests/`
- Tests: `pytest -q`
- Tests with golden bills only: `pytest -q tests/golden/`
- Run API: `uvicorn voltregistry.api.main:app --reload`
- DB migrations (create): `alembic revision --autogenerate -m "<message>"`
- DB migrations (apply): `alembic upgrade head`
- Demo CLI: `python -m voltregistry.cli compare --site-id <id>`
- Git: `git add`, `git commit`, `git push`

---

## Project Structure

Backend Python package lives under `src/voltregistry/`. All commands run from repo root unless noted. Reference tariff JSON files live under `src/voltregistry/tariffs/reference/` and are hand-curated, not LLM-generated, in v0.

The full v0 build spec is in `VoltRegistry_v0_Spec.md` at the repo root. Read it at the start of any session that touches schemas, the engine, or the comparison flow.

---

## Critical Rules

These are non-negotiable for VoltRegistry. Never relax them without an explicit decision recorded in `docs/JOURNAL.md`.

1. **No real Walmart data in this repo.** v0 is built on 100% public sources (URDB, HIFLD, EIA Form 861, NREL ComStock). No bills, no account numbers, no internal interval data. If real data is introduced later, it lives outside this repo and is loaded at runtime, never committed.
2. **Math is deterministic. LLMs do not calculate.** Tariff math, eligibility logic, and the delivery/supply classifier rules run as plain Python. LLMs may assist with classifying an ambiguous charge name into delivery/supply, but the final cost is never produced by a model.
3. **Delivery-only filter is enforced at the engine boundary.** Charges classified as `supply` are excluded from the delivery total. Ambiguous charges (confidence < 0.8) are excluded unless the tariff has been manually reviewed (`delivery_supply_review_status: manually_reviewed`).
4. **No tariff ships without a golden test.** Every tariff in `src/voltregistry/tariffs/reference/` must have at least one matching test in `tests/golden/` that compares engine output against a utility-published sample bill calculation, ≤1% variance.
5. **Schema changes require running golden tests for every existing tariff.** The schema is the contract; if it changes, all five reference tariffs re-validate before merge.
6. **URDB is the schema baseline.** New tariff fields are added by extending URDB's structure, not by designing in parallel. Keep `urdb_id` populated wherever a URDB equivalent exists.

---

## Docs

| Doc | Contents |
|---|---|
| [VoltRegistry_v0_Spec.md](VoltRegistry_v0_Spec.md) | v0 build spec — scope, schemas, engine contract, validation strategy, build phases |
| [docs/JOURNAL.md](docs/JOURNAL.md) | Session log, executive snapshot, decision log |

Read `docs/JOURNAL.md` at the start of any non-trivial session.

---

## Save Project

When Jason says "Save Project":
1. Scan the session for new decisions, scope changes, schema edits, and validation results.
2. Rewrite `## Executive Snapshot` in `docs/JOURNAL.md` — current focus + next session priorities.
3. Append a dated `### YYYY-MM-DD` entry to `## Historical Log` with the session delta (decisions, what changed, what's next).
4. If schemas or engine logic changed: confirm `pytest -q` passes, then run `pytest -q tests/golden/` and report the results in the journal entry.
5. From repo root: `git add -A && git commit -m "chore: save project $(date +%Y-%m-%d)" && git push`

(No deploy step in v0 — VoltRegistry is a backend-only project until a UI is added.)

---

## Delivery Standard

Before marking any task done:
1. Run `ruff check src/ tests/` — fix all errors.
2. Run `mypy src/voltregistry` — fix all errors.
3. Run `pytest -q` — all tests must pass.
4. If the change touches the engine, classifier, eligibility, or any tariff file: run `pytest -q tests/golden/` and report variance for every reference tariff.
5. State what changed, what depends on it, and how regressions were ruled out.

Scale to the change: a new charge type or rule type touches the engine and requires the full golden run. A docstring fix does not.

---

## Model Selection

- **Bulk / repetitive** (URDB ingestion scripts, bulk site geocoding, batch mapping) → `claude-haiku-4-5-20251001`
- **Default work** (engine features, API endpoints, test fixtures, refactors) → `claude-sonnet-4-6`
- **High-stakes** (schema changes, delivery/supply classifier rules, eligibility logic, tariff JSON for a new utility, anything in `tests/golden/`) → `claude-opus-4-6`

The reasoning: schema and classifier mistakes silently propagate into every comparison. Spend more on those decisions and less on plumbing.
