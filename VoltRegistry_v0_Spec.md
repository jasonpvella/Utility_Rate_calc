# VoltRegistry v0 — Build Spec

**Owner:** Jason
**Builder:** Claude Code (autonomous)
**Audience:** Internal Walmart energy team (eventual), built on 100% public data
**Status:** Draft for build

---

## 0. North Star

Given a Walmart or Sam's Club store address, return:

1. The serving utility (EIA ID, name, regulatory jurisdiction)
2. The currently applicable large-commercial delivery tariff
3. The simulated annual **delivery-only** cost against a synthetic Walmart load profile
4. A ranked list of alternative tariffs the site is eligible for, with simulated annual delivery cost for each
5. A line-item audit trail showing how every charge was computed, with citations

All of the above must run end-to-end **without any real Walmart data**. The system must be designed so that, when real interval data and bills become available, they slot in as an upgrade path rather than a redesign.

## 1. What Changed From Prior PRDs

This spec deliberately diverges from earlier VoltRegistry PRDs in five places:

1. **URDB is the foundation, not a fallback.** OpenEI URDB has ~50K US tariffs already in structured JSON. v0 inherits their schema and extends it. Phase 1.5 (LLM PDF extraction) is deferred to v1 and is only needed for the muni/co-op tail URDB doesn't cover.
2. **No calibration mode in v0.** The "anchor bill / adjustment factor" approach is dropped. Validation in v0 is entirely against (a) utility-published sample bill calculations and (b) URDB's own rate calculator — both public, both deterministic.
3. **Storage is SQLite, not "JSON Firestore-like."** Same effort, scales to the full footprint, has transactions, and SQLite has good JSON column support for tariff structures.
4. **Charge model is broader.** Adds explicit support for coincident-peak demand, percentage riders, tier basis (kWh/kW/load-factor), and tariff effective-date windows. The four omissions that would have bitten at tariff #5–10 in the prior PRD.
5. **Scope is narrower.** v0 covers 5 utilities deeply, not 1,000 utilities shallowly. Footprint expansion is v1.

## 2. v0 Scope (Explicit)

### In scope

- Walmart + Sam's Club site dataset (~5,200 stores) from public store finders
- Geospatial mapping of every site to its serving utility EIA ID
- Tariff library covering 5 reference utilities (see §5)
- Delivery/supply classifier for tariff charges
- Eligibility engine (voltage, kW, customer class, mandatory/optional)
- Tariff execution engine for: fixed, energy (flat + TOU + tiered), demand (NCP + simple ratchet), percentage riders
- Synthetic Walmart Supercenter load profile (8760 hours), parameterized by climate zone
- Comparison API + HTML report generator
- Golden test suite (≥1 utility-published sample bill per supported tariff, ≤1% variance)

### Explicitly out of scope (deferred to v1+)

- Real Walmart bills, calibration, variance tuning
- LLM-based PDF extraction (URDB-only for v0)
- Coincident-peak demand based on system peak (modeled as field, not computed)
- HUD tiers, kVAR/power-factor, NEM/onsite generation, capacity tags (PJM PLC), economic development riders
- Tariffs outside the top 5 reference utilities
- UI dashboard, auth, multi-user, real-time anything
- Optimization (only ranking; no recommendation logic beyond cost)

## 3. Tech Stack

- **Language:** Python 3.11+
- **API:** FastAPI
- **Storage:** SQLite via SQLModel/SQLAlchemy. JSON columns for tariff payloads. Migrations via Alembic.
- **Geospatial:** GeoPandas + Shapely for territory joins
- **Math:** NumPy for 8760 vectorized calculations; no Pandas in the hot path
- **Testing:** pytest, with golden-bill cases as parameterized tests
- **Reporting:** Jinja2 → HTML (no JS framework; static output)
- **Schema:** Pydantic v2 throughout, including for URDB ingestion

## 4. Data Sources (All Public)

| Source | Use | License |
|---|---|---|
| Walmart Store Finder (walmart.com/store-finder) | Site addresses + lat/lng | Scrape responsibly; cache locally |
| Sam's Club Locator | Sam's Club sites | Same |
| HIFLD Electric Retail Service Territories shapefile | Polygon → utility join | Public |
| EIA Form 861 (annual) | Utility metadata, customer class breakdown | Public |
| OpenEI URDB API | Tariff structures | CC-BY-3.0 |
| URDB Rate Calculator | Cross-check engine output | Public |
| NREL ComStock | Synthetic commercial load profiles | Public |
| Utility tariff filings (PUC websites) | Worked example bill calculations for golden tests | Public |

## 5. Reference Utilities for v0

Picked to span market structures and tariff complexity. Each must have a tariff applicable to large-commercial Walmart-sized loads.

| Utility | State | Market | Why included |
|---|---|---|---|
| Entergy Arkansas | AR | Regulated, vertically integrated | Clean baseline; LGS-1 well-documented |
| Duke Energy Carolinas | NC/SC | Regulated | Multi-rider complexity |
| Oncor | TX | Deregulated, delivery-only | Forces clean delivery/supply boundary |
| Georgia Power | GA | Regulated | Heavy rider stack; tests percentage rider math |
| Florida Power & Light | FL | Regulated | Tiered demand structure |

These five together exercise: regulated vs deregulated bill structure, simple vs heavy rider stacks, NCP demand with ratchet, TOU energy, tiered demand, and percentage-based gross-receipts/franchise riders.

## 6. Data Models

Start from URDB's schema (`openei.org/services/doc/rest/util_rates/?version=8`) and extend. Internal schema below; URDB IDs are kept as foreign keys for traceability.

### 6.1 Site

```json
{
  "site_id": "string",
  "brand": "Walmart | SamsClub",
  "store_number": "string",
  "address": "string",
  "city": "string",
  "state": "string",
  "lat": "float",
  "lng": "float",
  "voltage_level": "secondary | primary | transmission",
  "estimated_peak_kw": "float | null",
  "utility_eia_id": "string",
  "current_tariff_id": "string | null",
  "data_source": "scraped | uploaded | manual",
  "last_updated": "ISO 8601"
}
```

### 6.2 Utility

```json
{
  "eia_id": "string",
  "name": "string",
  "state": "string",
  "regulatory_jurisdiction": "state_puc | ferc | municipal",
  "market_structure": "regulated_vertical | regulated_with_choice | deregulated_delivery_only | municipal | cooperative",
  "input_tier": "TIER_1_8760 | TIER_2_PEAK_KW | TIER_3_VOLUMETRIC",
  "tariff_ids": ["string"],
  "service_territory_geom": "WKT string"
}
```

### 6.3 Tariff

```json
{
  "tariff_id": "string",
  "utility_eia_id": "string",
  "urdb_id": "string | null",
  "name": "string",
  "rate_code": "string",
  "effective_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD | null",
  "version": "string",
  "availability": "mandatory | optional | closed_to_new",
  "eligibility": {
    "min_kw": "float | null",
    "max_kw": "float | null",
    "min_kwh_annual": "float | null",
    "max_kwh_annual": "float | null",
    "voltage_required": ["secondary", "primary", "transmission"] ,
    "customer_classes": ["string"],
    "load_factor_min": "float | null",
    "term_commitment_months": "int | null",
    "notes": "string"
  },
  "tou_schedule_id": "string | null",
  "charges": ["charge_id"],
  "rules": ["rule_id"],
  "source_document": "URL or citation",
  "ingestion_method": "urdb | manual | llm_extracted",
  "delivery_supply_review_status": "auto_classified | manually_reviewed | flagged"
}
```

### 6.4 Charge

```json
{
  "charge_id": "string",
  "tariff_id": "string",
  "name": "string",
  "type": "fixed | energy | demand | rider",
  "demand_basis": "ncp | cp | contract | null",
  "unit": "$/kW | $/kWh | $/month | percent",
  "value": "float | null",
  "applies_to": {
    "season": "summer | winter | shoulder | all",
    "tou_period": "string | null",
    "applied_to_charge_ids": ["string"]
  },
  "tiers": [
    {
      "min": "float",
      "max": "float | null",
      "rate": "float",
      "tier_basis": "kwh_monthly | kw_billed | load_factor_pct"
    }
  ],
  "classification": {
    "category": "delivery | supply | ambiguous",
    "confidence": "0.0 - 1.0",
    "method": "rule_based | manual | llm",
    "reasoning": "string"
  }
}
```

Three new fields vs prior PRDs:
- `demand_basis` — distinguishes NCP / CP / contract demand
- `unit: percent` + `applied_to_charge_ids` — for percentage riders
- `tier_basis` — clarifies what dimension the tiers are on

### 6.5 TOU Schedule

```json
{
  "tou_schedule_id": "string",
  "periods": [
    {
      "name": "on_peak | mid_peak | off_peak | super_off_peak | shoulder",
      "season_months": [1, 2, ..., 12],
      "hour_ranges": [{"start": 13, "end": 19}],
      "weekday_mask": "weekdays | weekends | all",
      "holidays_off_peak": true
    }
  ],
  "holiday_calendar": "nerc | federal | utility_specific",
  "holiday_dates": ["YYYY-MM-DD"]
}
```

Replaces the binary on/off-peak structure. Multiple named periods, explicit hour ranges (supports midnight crossover by allowing `start > end`), explicit holiday list.

### 6.6 Rule

```json
{
  "rule_id": "string",
  "type": "demand_ratchet | minimum_charge | term_commitment",
  "parameters": {
    "ratchet_percent": "float",
    "ratchet_window_months": "int",
    "ratchet_window_type": "rolling | seasonal | contract_anniversary",
    "ratchet_source_months": [6, 7, 8, 9],
    "minimum_monthly_charge": "float"
  }
}
```

## 7. Delivery/Supply Classifier (Core IP)

This is the part URDB does not give you. It runs over every charge in every imported tariff.

### 7.1 Rule-based first pass

Three pattern lists, applied in order:

**Always delivery (confidence 0.95):**
- Names containing: "distribution", "transmission", "delivery", "facility charge", "metering"
- Type=demand in regulated markets
- Named riders matching utility-specific delivery rider patterns

**Always supply (confidence 0.95):**
- Names containing: "fuel", "purchased power", "generation", "energy cost recovery", "fuel adjustment"
- Type=energy in deregulated markets where utility is delivery-only

**Ambiguous (confidence 0.5–0.7, flagged for review):**
- Names like "service charge", "customer charge", "base rate", "demand charge" without market-context disambiguation

### 7.2 Market-context override

In `deregulated_delivery_only` utilities (Oncor, ComEd, etc.), default to delivery for all charges unless explicitly identified as a non-bypassable supply pass-through.

### 7.3 Output

Every charge gets a classification + confidence + reasoning string. Anything below confidence 0.8 is included only if `delivery_supply_review_status == manually_reviewed`. v0 ships with the 5 reference utilities pre-reviewed.

## 8. Eligibility Engine

Run before any cost calculation. For (site, tariff) pair:

```
ELIGIBLE unless any of:
  - site.voltage_level not in tariff.eligibility.voltage_required
  - site.estimated_peak_kw < tariff.eligibility.min_kw
  - site.estimated_peak_kw > tariff.eligibility.max_kw
  - tariff.availability == "closed_to_new"
  - site.customer_class not in tariff.eligibility.customer_classes
  - tariff.end_date < today
```

Output per pair: `{eligible: bool, reasons: [string], warnings: [string]}`. Warnings cover soft rules (load factor minimums, term commitments) — flagged but not exclusionary.

## 9. Synthetic Walmart Load Profile

Walmart Supercenters are the dominant store format (~3,500 of 4,600 sites). v0 ships with a single parameterized Supercenter profile:

- Building type: NREL ComStock "Standalone Retail" prototype, scaled to ~150,000 sq ft
- Climate zone: parameter (1–8 IECC), defaults to climate zone of the site
- Annual energy: ~6,500 MWh
- Peak demand: ~2.5 MW (summer-peaking, mid-afternoon)
- Load factor: ~70%
- Output: 8760 array of hourly kW values

Sam's Club uses a similar but smaller profile (~135,000 sq ft, ~2.2 MW peak).

Profile generation should be a single function: `synthesize_load(brand, climate_zone) -> np.ndarray[8760]`.

## 10. Tariff Execution Engine

### 10.1 Interface

```python
class TariffEngine:
    def __init__(self, tariff: Tariff, charges: list[Charge],
                 rules: list[Rule], tou: TouSchedule | None):
        ...

    def calculate(self, load_profile: np.ndarray) -> CalculationResult:
        """
        load_profile: shape (8760,), hourly kW values
        Returns CalculationResult with line-itemized breakdown.
        """
```

### 10.2 Calculation order

1. Aggregate load profile to monthly kWh and monthly NCP demand
2. Apply TOU mapping if tariff has a TOU schedule (split kWh by period)
3. Compute fixed charges (per month × 12)
4. Compute energy charges (flat, TOU-split, or tiered per `tier_basis`)
5. Compute demand charges (NCP per month, then apply ratchet rule if present)
6. Compute riders, in two passes:
   - Pass 1: dollar-amount riders (`unit: $/kWh` or `$/kW`)
   - Pass 2: percentage riders (`unit: percent`, applied to subtotal of `applied_to_charge_ids`)
7. Filter to delivery-only per classifier output
8. Sum and emit `CalculationResult`

### 10.3 Result shape

```json
{
  "tariff_id": "string",
  "annual_total_delivery": "float",
  "monthly": [
    {
      "month": 1,
      "total_delivery": "float",
      "line_items": [
        {
          "charge_id": "string",
          "name": "string",
          "category": "delivery | supply",
          "included": true,
          "amount": "float",
          "calculation_basis": "string (e.g. '2,450 kW × $12.50/kW')"
        }
      ]
    }
  ],
  "warnings": ["string"],
  "schema_limitations": ["string (e.g. 'Power factor adjustment not modeled')"]
}
```

The `calculation_basis` field is non-negotiable. Every cost line must show its math in human-readable form.

## 11. Comparison API

### Endpoints

```
GET  /sites                          List sites (paginated)
GET  /sites/{site_id}                Site detail with utility + current tariff
GET  /utilities/{eia_id}/tariffs     List tariffs for a utility
POST /sites/{site_id}/compare        Run comparison
GET  /sites/{site_id}/compare/{run_id}/report.html   Rendered report
```

### `/sites/{site_id}/compare` response

```json
{
  "site_id": "string",
  "utility_eia_id": "string",
  "load_profile_used": "synthetic_supercenter_cz4a",
  "current_tariff": {
    "tariff_id": "string",
    "annual_delivery_cost": "float"
  },
  "alternatives": [
    {
      "tariff_id": "string",
      "name": "string",
      "eligible": true,
      "annual_delivery_cost": "float",
      "delta_vs_current": "float",
      "warnings": ["string"]
    }
  ],
  "ineligible": [
    {"tariff_id": "string", "reasons": ["string"]}
  ]
}
```

## 12. Validation Strategy (No Real Bills)

Three validation layers, in order of authority:

1. **Utility-published sample bill calculations.** Most regulated utility tariff filings include worked examples ("a customer using 500,000 kWh at 1,200 kW would pay $X"). These are the gold standard — the utility itself certifies the math. Find at least one per supported tariff and encode as a golden test. Variance tolerance: ≤1%.
2. **URDB calculator cross-check.** For URDB-imported tariffs, run the same load profile through URDB's own calculator and compare. Variance tolerance: ≤2% (URDB has known limitations on rider compounding).
3. **Plausibility band.** Synthetic Walmart load profile against any tariff should produce annual delivery cost in the range $0.04–$0.12/kWh delivered. Outside that band → engine bug or schema error.

Every supported tariff must pass layer 1. Layers 2 and 3 are continuous integration safeguards.

## 13. Project Structure

```
voltregistry/
├── pyproject.toml
├── alembic/                          # DB migrations
├── data/
│   ├── raw/                          # Cached external data
│   ├── territories.gpkg              # HIFLD shapefile
│   └── eia_form_861.csv
├── src/voltregistry/
│   ├── api/                          # FastAPI routes
│   ├── ingest/
│   │   ├── walmart_scraper.py
│   │   ├── samsclub_scraper.py
│   │   ├── eia_form_861.py
│   │   ├── hifld_territories.py
│   │   └── urdb_client.py
│   ├── mapping/
│   │   └── territory_join.py
│   ├── tariffs/
│   │   ├── models.py                 # Pydantic
│   │   ├── classifier.py             # Delivery/supply
│   │   ├── eligibility.py
│   │   └── reference/                # Hand-curated for 5 utilities
│   │       ├── entergy_ar_lgs1.json
│   │       ├── duke_carolinas_*.json
│   │       ├── oncor_*.json
│   │       ├── georgia_power_*.json
│   │       └── fpl_*.json
│   ├── load/
│   │   └── synthetic.py              # ComStock-derived profiles
│   ├── engine/
│   │   ├── tariff_engine.py
│   │   ├── calculations.py
│   │   └── rules.py                  # Ratchet, minimums, etc.
│   ├── comparison/
│   │   └── compare.py
│   └── reporting/
│       └── html_report.py
├── tests/
│   ├── golden/
│   │   ├── entergy_ar_lgs1_sample.json    # From tariff filing
│   │   └── ...
│   ├── test_engine.py
│   ├── test_classifier.py
│   └── test_eligibility.py
└── scripts/
    ├── bootstrap.py                  # Run all ingestion
    └── demo.py                       # End-to-end demo for one site
```

## 14. Build Sequence

Five phases. Each ends with a runnable demo.

| Phase | Deliverable | Demo gate |
|---|---|---|
| P1 (Week 1) | Site dataset + utility mapping | `python scripts/demo.py --site-id 1234` returns `{utility_eia_id, name}` for any of 5,200 sites |
| P2 (Week 2) | URDB ingestion + 5 reference tariffs encoded | `GET /utilities/{eia_id}/tariffs` returns structured tariff list |
| P3 (Week 3) | Tariff engine + delivery/supply classifier | `engine.calculate(tariff, load)` matches utility sample bill ≤1% for all 5 reference tariffs |
| P4 (Week 4) | Eligibility + comparison engine | `POST /sites/{id}/compare` returns full alternatives list |
| P5 (Week 5) | HTML report + polish | Open report.html in browser, see ranked delivery-cost comparison with line-itemized math |

Demo gates are non-negotiable. If P3 doesn't pass golden tests, do not proceed to P4.

## 15. Upgrade Path to Real Walmart Data

Designed in from day zero so the eventual transition is additive, not destructive:

- The Site model has `current_tariff_id` (nullable) and `estimated_peak_kw` — these become real values when internal data lands
- The synthetic load profile is a single function call that can be swapped for real interval data without engine changes
- The `data_source` field tracks provenance so synthetic and real sites can coexist
- The validation layer can add a fourth tier ("real bill match within X%") without re-architecting tests
- The schema's `effective_date` / `end_date` already supports point-in-time bill recalculation for retrospective audits

What gets unlocked when real data arrives: calibration mode, bill audit (variance attribution per line item), historical retrospective ("what would last year have cost on Tariff X?"), and the eventual recommendation layer.

## 16. Out-of-Scope but Documented

So future contributors don't relitigate: the following are real but deliberately not in v0. Each has a one-paragraph design note in `docs/future/`:

- Coincident-peak demand (system-peak-hour billing) — needs ISO/RTO data feeds
- HUD tiers / energy rates dependent on load factor — needs schema extension
- kVAR / power factor adjustments — needs reactive power in load profile
- PJM PLC / capacity tag charges — separate billing layer entirely
- Onsite generation (NEM, NEM 3.0, virtual NEM) — changes engine math fundamentally
- LLM-based tariff PDF extraction — Phase 1.5; activated when muni/co-op coverage becomes the gating constraint

## 17. Initialization Prompt for Claude Code

```
Build the Project VoltRegistry v0 system per the spec at
VoltRegistry_v0_Spec.md. Implement strictly per §6 schemas, §10 engine
contract, and §13 project structure. Start with Phase 1 only (site
dataset + utility mapping) and stop at the P1 demo gate for review
before proceeding. Use SQLite + SQLModel + FastAPI. All Pydantic v2.
For the 5 reference tariffs, hand-curate JSON files under
src/voltregistry/tariffs/reference/ — do not attempt LLM extraction in
v0. Validation must pass utility-published sample bills (golden tests)
at ≤1% variance before any tariff is considered shipped.
```

---

**Decision points still open** (none of which block starting Phase 1):

- Whether to scrape Walmart store finder live or use a one-time cached snapshot. Recommend cached snapshot, refreshed quarterly.
- Whether the HTML report includes embedded charts (recharts/plotly) or is text-only. Recommend text-only for v0; charts in v1.
- Whether to include a CLI in addition to the API. Recommend yes — `voltregistry compare --site-id` is the fastest demo path.
