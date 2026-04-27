"""Phase 2 — tariff input extraction pipeline.

Fetches tariff documents (HTML or PDF) from utility_tariff_url rows with
status='pending', sends each to Claude to identify applicable large C&I rate
schedules and their required billing inputs, then writes results to
utility_tariff_inputs.

Usage (run from repo root on Jason's Mac — requires ANTHROPIC_API_KEY):
    python scripts/extract_tariff_inputs.py
    python scripts/extract_tariff_inputs.py --eia-id 38
    python scripts/extract_tariff_inputs.py --eia-id 38,29,4
    python scripts/extract_tariff_inputs.py --dry-run          # fetch but don't call API or write
    python scripts/extract_tariff_inputs.py --limit 5          # process at most N URLs
    python scripts/extract_tariff_inputs.py --reprocess        # re-run URLs already marked extracted

Environment variables:
    ANTHROPIC_API_KEY   required
    ANTHROPIC_MODEL     optional, default claude-sonnet-4-6

Notes:
  - HTML pages: full text is extracted and truncated to ~120k chars (fits in context).
  - PDF documents: sent as base64-encoded document blocks via the Anthropic files API.
  - PDFs larger than 32 MB are skipped with a warning — split them manually first.
  - On extraction failure the URL row is marked 'failed' and the error is logged.
  - This script must be run from Jason's Mac (requires external network access to
    both utility websites and the Anthropic API).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from sqlmodel import Session, select

from voltregistry.db import create_db_and_tables, engine
from voltregistry.models import UtilityTariffInputsTable, UtilityTariffUrlTable
from voltregistry.tariffs.input_types import VALID_INPUT_TYPES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_HTML_CHARS = 120_000        # ~30k tokens — enough for a full tariff book HTML page
MAX_PDF_BYTES = 32 * 1024 * 1024  # 32 MB hard limit
REQUEST_TIMEOUT = 30.0          # seconds for HTTP fetches
RETRY_DELAY = 2.0               # seconds between retries on 429 / 5xx

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are analyzing a utility electric tariff document to identify rate schedules applicable to large commercial or industrial customers.

Target customer profile: a Walmart Supercenter or Sam's Club — typically 500–3,000 kW peak demand, 150,000–900,000 kWh per month, served at secondary (120/208V or 277/480V) or primary (4–15 kV) voltage.

For EACH rate schedule that could apply to this customer type:
1. Extract the schedule code (e.g. "GS-2", "LP", "LPS", "E-20", "Schedule 6")
2. Extract the full schedule name
3. Identify the minimum kW demand threshold for eligibility (null if none stated)
4. Identify the maximum kW demand threshold (null if none stated)
5. Note any eligibility conditions (customer class, voltage requirement, geographic limits)
6. List the applicable voltage levels from: secondary, primary, subtransmission, transmission
7. Identify which billing inputs are required to compute the monthly bill

For billing inputs, use ONLY these standardized type names — no others:
  monthly_kwh            — total monthly energy consumption (kWh)
  billing_demand_kw      — peak 15- or 30-minute demand in the billing period (kW)
  onpeak_demand_kw       — peak demand during defined on-peak hours (kW)
  offpeak_demand_kw      — peak demand during off-peak hours (kW)
  onpeak_kwh             — energy consumed during on-peak hours (kWh)
  offpeak_kwh            — energy consumed during off-peak hours (kWh)
  shoulder_kwh           — energy during shoulder/mid-peak hours if a 3rd TOU period exists (kWh)
  coincident_peak_kw     — customer's demand at the utility's system peak hour (kW)
  reactive_demand_kvar   — reactive power demand (kVAR)
  power_factor_pct       — average power factor as a percentage
  contract_demand_kw     — contractually reserved capacity (kW)
  ratchet_demand_kw      — highest demand across current and trailing N billing periods (kW)
  voltage_level          — which delivery voltage tier the customer takes service at
  load_factor_pct        — monthly load factor percentage
  billing_period_days    — number of days in the billing period (for daily charge proration)

IMPORTANT RULES:
- Include a schedule only if a Walmart-scale load (≥100 kW) is explicitly or implicitly eligible.
- Exclude residential, small commercial (typically <20 kW), agricultural, or lighting-only schedules.
- If a schedule has both a demand charge and TOU energy charges, list both the demand input AND the TOU kWh inputs.
- If a ratchet clause is present (billing demand = max of current and X% of prior months), include ratchet_demand_kw.
- If power factor or reactive demand is mentioned as a billing component (not just informational), include it.
- Set confidence between 0.0 and 1.0 based on how clearly the document defines the schedule and its inputs.
  Use < 0.7 if the document is unclear, truncated, or you had to infer rather than read directly.

Return ONLY valid JSON — no markdown, no explanation, no surrounding text:
{
  "utility_name": "...",
  "schedules": [
    {
      "schedule_code": "...",
      "schedule_name": "...",
      "applicability_min_kw": null,
      "applicability_max_kw": null,
      "applicability_notes": "...",
      "voltage_levels": ["secondary", "primary"],
      "inputs_required": ["monthly_kwh", "billing_demand_kw"],
      "confidence": 0.9,
      "notes": "..."
    }
  ]
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_html(url: str, client: httpx.Client) -> str:
    resp = client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    # Strip HTML tags for cleaner context
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text[:MAX_HTML_CHARS]


def _fetch_pdf_b64(url: str, client: httpx.Client) -> str:
    resp = client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    raw = resp.content
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError(f"PDF too large ({len(raw) / 1e6:.1f} MB > 32 MB limit): {url}")
    return base64.standard_b64encode(raw).decode()


def _is_pdf(url: str, content_type: str) -> bool:
    return "pdf" in content_type.lower() or url.lower().endswith(".pdf")


def _call_claude(
    client: Any,
    model: str,
    url: str,
    content_type: str,
    html_text: str | None,
    pdf_b64: str | None,
) -> str:
    if pdf_b64 is not None:
        content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {"type": "text", "text": EXTRACTION_PROMPT},
        ]
    else:
        content = [
            {"type": "text", "text": f"URL: {url}\n\nCONTENT:\n{html_text}"},
            {"type": "text", "text": EXTRACTION_PROMPT},
        ]

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


def _parse_extraction(raw: str) -> list[dict]:
    """Parse Claude's JSON response into a list of schedule dicts."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON substring if Claude wrapped it in text despite instructions
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON found in response: {raw[:200]}") from None
        data = json.loads(match.group())

    schedules = data.get("schedules", [])
    if not isinstance(schedules, list):
        raise ValueError(f"'schedules' is not a list: {type(schedules)}")
    return schedules


def _validate_inputs(inputs: list[str]) -> list[str]:
    """Return only recognized input type values, warn on unknowns."""
    valid = []
    for inp in inputs:
        if inp in VALID_INPUT_TYPES:
            valid.append(inp)
        else:
            print(f"  [warn] unknown input type '{inp}' — skipped")
    return valid


def _write_results(
    session: Session,
    url_row: UtilityTariffUrlTable,
    schedules: list[dict],
    raw: str,
) -> int:
    count = 0
    for s in schedules:
        inputs_validated = _validate_inputs(s.get("inputs_required", []))
        voltage_levels = s.get("voltage_levels", [])
        confidence = float(s.get("confidence", 1.0))

        row = UtilityTariffInputsTable(
            utility_eia_id=url_row.utility_eia_id,
            schedule_code=s.get("schedule_code", ""),
            schedule_name=s.get("schedule_name", "unknown"),
            applicability_min_kw=s.get("applicability_min_kw"),
            applicability_max_kw=s.get("applicability_max_kw"),
            applicability_notes=s.get("applicability_notes", ""),
            voltage_levels=json.dumps(voltage_levels),
            inputs_required=json.dumps(inputs_validated),
            source_url=url_row.url,
            extraction_status="extracted" if confidence >= 0.7 else "needs_review",
            confidence=confidence,
            raw_extraction=raw,
            last_updated=datetime.utcnow(),
        )
        session.add(row)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------


def process_urls(
    eia_ids: list[str] | None,
    dry_run: bool,
    limit: int | None,
    reprocess: bool,
    model: str,
) -> None:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not dry_run:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    claude = anthropic.Anthropic(api_key=api_key) if not dry_run else None
    create_db_and_tables()

    with Session(engine) as session:
        query = select(UtilityTariffUrlTable)

        statuses = ["pending"]
        if reprocess:
            statuses.append("extracted")
        query = query.where(UtilityTariffUrlTable.status.in_(statuses))  # type: ignore[attr-defined]

        if eia_ids:
            query = query.where(UtilityTariffUrlTable.utility_eia_id.in_(eia_ids))  # type: ignore[attr-defined]

        url_rows = session.exec(query).all()

        if limit:
            url_rows = url_rows[:limit]

        print(f"Processing {len(url_rows)} URL(s) …\n")

        with httpx.Client(headers={"User-Agent": "VoltRegistry/0.1 (tariff research)"}) as http:
            for i, row in enumerate(url_rows, 1):
                print(f"[{i}/{len(url_rows)}] eia_id={row.utility_eia_id}  {row.url}")

                if dry_run:
                    print("  [dry-run] skipping fetch + extraction\n")
                    continue

                # --- fetch ---------------------------------------------------
                try:
                    head = http.head(row.url, timeout=REQUEST_TIMEOUT, follow_redirects=True)
                    content_type = head.headers.get("content-type", "")

                    if _is_pdf(row.url, content_type):
                        print("  fetching PDF …")
                        pdf_b64 = _fetch_pdf_b64(row.url, http)
                        html_text = None
                    else:
                        print("  fetching HTML …")
                        html_text = _fetch_html(row.url, http)
                        pdf_b64 = None

                except Exception as exc:
                    print(f"  [fetch error] {exc}")
                    row.status = "failed"
                    row.notes = f"{row.notes} | fetch error: {exc}"[:2000]
                    row.last_updated = datetime.utcnow()
                    session.add(row)
                    session.commit()
                    continue

                # --- call Claude ---------------------------------------------
                try:
                    print("  calling Claude …")
                    raw = _call_claude(
                        claude, model, row.url, content_type, html_text, pdf_b64
                    )
                except Exception as exc:
                    if "429" in str(exc) or "rate" in str(exc).lower():
                        print(f"  [rate limit] sleeping {RETRY_DELAY}s then retrying …")
                        time.sleep(RETRY_DELAY)
                        try:
                            raw = _call_claude(
                                claude, model, row.url, content_type, html_text, pdf_b64
                            )
                        except Exception as exc2:
                            print(f"  [api error after retry] {exc2}")
                            row.status = "failed"
                            row.notes = f"{row.notes} | api error: {exc2}"[:2000]
                            row.last_updated = datetime.utcnow()
                            session.add(row)
                            session.commit()
                            continue
                    else:
                        print(f"  [api error] {exc}")
                        row.status = "failed"
                        row.notes = f"{row.notes} | api error: {exc}"[:2000]
                        row.last_updated = datetime.utcnow()
                        session.add(row)
                        session.commit()
                        continue

                # --- parse + write ------------------------------------------
                try:
                    schedules = _parse_extraction(raw)
                    count = _write_results(session, row, schedules, raw)
                    row.status = "extracted"
                    row.last_fetched = datetime.utcnow()
                    row.last_updated = datetime.utcnow()
                    session.add(row)
                    session.commit()
                    print(f"  extracted {count} schedule(s)\n")

                except Exception as exc:
                    print(f"  [parse error] {exc}")
                    row.status = "failed"
                    row.notes = f"{row.notes} | parse error: {exc}"[:2000]
                    row.last_updated = datetime.utcnow()
                    session.add(row)
                    session.commit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--eia-id",
        default=None,
        help="Comma-separated EIA IDs to process (default: all pending)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List URLs that would be processed without fetching or calling API",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of URLs to process in this run",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-process URLs already marked as extracted (adds new rows, does not delete old ones)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        help=f"Claude model to use (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    eia_ids = [x.strip() for x in args.eia_id.split(",")] if args.eia_id else None
    process_urls(
        eia_ids=eia_ids,
        dry_run=args.dry_run,
        limit=args.limit,
        reprocess=args.reprocess,
        model=args.model,
    )


if __name__ == "__main__":
    main()
