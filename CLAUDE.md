VoltRegistry — Claude Code Instructions
1. Terminal Essentials
Run directly via Bash. Hand to user only if a single short git command.
	• Health: ruff check src/, mypy src/voltregistry, pytest -q
	• Golden Tests: pytest -q tests/golden/ (Required for any engine/tariff change)
	• Ops: alembic upgrade head, python scripts/bootstrap.py --force-refresh
2. The Hard Rules
	• Public Data Only: No real Walmart bills or interval data in-repo, but we have pulled the 4500+ stores and additional Sam's Club. 
	• Deterministic Math: LLMs never calculate. Engine math is pure Python.
	• Delivery-Only: Exclude supply charges. Exclude ambiguous charges unless manually_reviewed.
	• Golden Standard: Reference tariffs must match utility samples within ≤1% variance.
	• URDB Baseline: Use URDB schema fields as the primary contract.
3. Territory Pipeline
HIFLD is dead. Use Census TIGER 2023 + EIA Form 861.
	• Validation: Must result in >1,500 polygons and >200 EIA IDs.
	• Resolution: County-level. IOU heuristic used for multi-utility counties.
4. Workflow & Models
	• Pre-Done Check: Run lint/tests. If engine touched, report Golden Bill variance.
	• Save Project: Update docs/JOURNAL.md (Snapshot + Log), clear git locks, and push.
	• Selection:
		○ Opus: Schemas, Classifier logic, Golden Tests.
		○ Sonnet: General features, API, refactors.
		○ Haiku: Ingestion, scripts, bulk tasks.