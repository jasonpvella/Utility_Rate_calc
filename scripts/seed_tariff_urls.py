"""Seed utility_tariff_url table with known tariff page URLs for the top 30 gap utilities.

These are the utilities that serve the most Walmart/Sam's Club stores but have
zero URDB coverage.  Each entry points to the utility's tariff schedule page
(or the applicable PUC portal) so the Phase 2 extraction pipeline knows where
to start.

Run from repo root:
    python scripts/seed_tariff_urls.py [--dry-run]

The script is idempotent: it skips any (utility_eia_id, url) pair already in
the table, so it's safe to re-run after adding new entries.

To add a utility not in this list, append to GAP_UTILITY_URLS and re-run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlmodel import Session, select

from voltregistry.db import create_db_and_tables, engine
from voltregistry.models import UtilityTariffUrlTable

# ---------------------------------------------------------------------------
# Seed data
#
# url_type values:
#   "tariff_page"  — utility's own tariff schedule page (best starting point)
#   "portal"       — state PUC tariff filing portal (use when utility page unknown)
#   "document"     — direct PDF or HTML tariff document (ready for extraction)
#
# status is always "pending" on initial seed.
# confidence_note is for human reference only, not stored in the DB.
# ---------------------------------------------------------------------------

GAP_UTILITY_URLS: list[dict] = [
    # ── TVA (38) — 308 stores, FERC-regulated federal utility ───────────────
    {
        "eia_id": "38",
        "url": "https://www.tva.com/energy/our-power-system/generation/rate-schedules",
        "url_type": "tariff_page",
        "notes": "TVA Large Power (LP) rate is the applicable C&I schedule for Walmart-scale loads. Federal utility — tariffs published directly on tva.com, not via state PUC.",
    },
    # ── Kansas City Power & Light / Evergy (45) — 227 stores ────────────────
    {
        "eia_id": "45",
        "url": "https://www.evergy.com/rates-and-regulations/rates-and-tariffs/kansas-city-metro-tariffs",
        "url_type": "tariff_page",
        "notes": "Evergy Kansas City Metro (formerly KCPL). Large General Service (LGS) and Interruptible schedules are the likely applicable rates.",
    },
    {
        "eia_id": "45",
        "url": "https://www.efis.psc.mo.gov/mpsc/tariff.asp",
        "url_type": "portal",
        "notes": "Missouri PSC tariff portal — backup source if utility page doesn't have direct PDFs.",
    },
    # ── Northern States Power MN / Xcel Energy (4) — 173 stores ────────────
    {
        "eia_id": "4",
        "url": "https://www.xcelenergy.com/rates_and_regulations/minnesota_rates_and_regulations",
        "url_type": "tariff_page",
        "notes": "Xcel Energy Minnesota. Large Commercial (Cg) and Large Power (Cp) schedules. Also check Time-of-Day Large Commercial (TOU-D).",
    },
    # ── Appalachian Power / AEP (35) — 161 stores, VA + WV ─────────────────
    {
        "eia_id": "35",
        "url": "https://www.appalachianpower.com/about/rates/",
        "url_type": "tariff_page",
        "notes": "Appalachian Power serves VA and WV. Rate schedules 1C (Large General Service) and LP (Large Power). Also filed with VA SCC and WV PSC.",
    },
    {
        "eia_id": "35",
        "url": "https://www.scc.virginia.gov/pages/Electricity-Companies",
        "url_type": "portal",
        "notes": "Virginia SCC — official tariff filings for Appalachian Power Virginia jurisdiction.",
    },
    # ── Nevada Power / NV Energy (25) — 160 stores ──────────────────────────
    {
        "eia_id": "25",
        "url": "https://www.nvenergy.com/account-services/rates-tariffs",
        "url_type": "tariff_page",
        "notes": "NV Energy Nevada Power (southern NV). Large General Service (LGS) and Large Power schedules. TOU variants likely applicable at Walmart demand levels.",
    },
    {
        "eia_id": "25",
        "url": "https://pucweb1.state.nv.us/TariffSearch/",
        "url_type": "portal",
        "notes": "Nevada PUC tariff search — official filed versions of NV Energy rate schedules.",
    },
    # ── Rocky Mountain Power / PacifiCorp (28) — 151 stores, UT/WY/ID ───────
    {
        "eia_id": "28",
        "url": "https://www.rockymountainpower.net/about/rates-regulations.html",
        "url_type": "tariff_page",
        "notes": "Rocky Mountain Power (PacifiCorp). Schedule 6 (Large General Service) and Schedule 9 (Large Power) are the primary C&I rates. Covers UT, WY, ID service areas.",
    },
    {
        "eia_id": "28",
        "url": "https://psc.utah.gov/utilities/electric/tariffs/",
        "url_type": "portal",
        "notes": "Utah PSC — official Rocky Mountain Power tariff filings for UT jurisdiction.",
    },
    # ── PNM / Public Service Co of NM (27) — 145 stores ────────────────────
    {
        "eia_id": "27",
        "url": "https://www.pnm.com/tariffs",
        "url_type": "tariff_page",
        "notes": "PNM Resources. General Service Large Power (3A) and Large Power (3B) schedules. NM PRC-regulated.",
    },
    # ── Entergy Texas (6454) — 143 stores ───────────────────────────────────
    {
        "eia_id": "6454",
        "url": "https://www.entergytexas.com/regulatory/tariffs.aspx",
        "url_type": "tariff_page",
        "notes": "Entergy Texas — PUCT-regulated IOU in deregulated TX market. Tariffs here are the distribution/wires charges plus bundled generation for Entergy territory (not ERCOT).",
    },
    # ── Oklahoma Gas & Electric (47) — 143 stores ───────────────────────────
    {
        "eia_id": "47",
        "url": "https://www.oge.com/wps/portal/ord/about/tariffs-filings",
        "url_type": "tariff_page",
        "notes": "Oklahoma Gas & Electric. Large Power Service (LPS) schedule. OCC and FERC regulated.",
    },
    # ── Pacific Gas & Electric (29) — 142 stores ────────────────────────────
    {
        "eia_id": "29",
        "url": "https://www.pge.com/tariffs/",
        "url_type": "tariff_page",
        "notes": "PG&E E-Tariff portal. Applicable schedules for Walmart scale: A-10 (Large Commercial), E-19 (Medium/Large Commercial TOU), E-20 (Large Industrial TOU). Must also check CPUC-approved rate riders.",
    },
    {
        "eia_id": "29",
        "url": "https://www.cpuc.ca.gov/tariffs/",
        "url_type": "portal",
        "notes": "CPUC tariff portal — backup for PG&E official filed versions.",
    },
    # ── Duke Energy Ohio (5420) — 133 stores ────────────────────────────────
    {
        "eia_id": "5420",
        "url": "https://www.duke-energy.com/home/products/rates-tariffs/ohio",
        "url_type": "tariff_page",
        "notes": "Duke Energy Ohio. GS-4 (Large General Service) and GS-5 (Large Industrial) schedules.",
    },
    {
        "eia_id": "5420",
        "url": "https://puco.ohio.gov/utilities/electricity/electric-utilities/",
        "url_type": "portal",
        "notes": "PUCO electric utilities page — official filed Duke Energy Ohio tariffs.",
    },
    # ── MidAmerican Energy (44) — 122 stores ────────────────────────────────
    {
        "eia_id": "44",
        "url": "https://www.midamericanenergy.com/rates-tariffs",
        "url_type": "tariff_page",
        "notes": "MidAmerican Energy (Berkshire Hathaway). Large General Service (LGS) and Large Industrial schedules. Iowa IUB and Illinois ICC regulated.",
    },
    # ── Wisconsin Electric / WE Energies (43) — 120 stores ──────────────────
    {
        "eia_id": "43",
        "url": "https://www.we-energies.com/business/rates-and-tariffs.htm",
        "url_type": "tariff_page",
        "notes": "WE Energies (Wisconsin Electric Power). Large Commercial/Industrial C&I schedules. PSCW-regulated.",
    },
    {
        "eia_id": "43",
        "url": "https://psc.wi.gov/Pages/AppsAndTools/tariffs.aspx",
        "url_type": "portal",
        "notes": "Wisconsin PSC tariff portal — official WE Energies filed schedules.",
    },
    # ── Baltimore Gas & Electric (15) — 117 stores ──────────────────────────
    {
        "eia_id": "15",
        "url": "https://www.bge.com/Business/ProductsAndServices/Rates-Tariffs/Pages/Current-Tariffs.aspx",
        "url_type": "tariff_page",
        "notes": "BGE (Exelon/Constellation). Maryland is deregulated — BGE provides delivery-only. Schedule GL (General Large Service) and Schedule HT (High Tension) are the applicable delivery schedules.",
    },
    {
        "eia_id": "15",
        "url": "https://webapp.psc.state.md.us/newSearch/tariff/index.cfm",
        "url_type": "portal",
        "notes": "Maryland PSC tariff search — official BGE filed delivery tariff schedules.",
    },
    # ── New England Electric System / National Grid MA (21) — 114 stores ────
    {
        "eia_id": "21",
        "url": "https://www.nationalgridus.com/MA-Business/Bills-and-Payments/Rates",
        "url_type": "tariff_page",
        "notes": "National Grid Massachusetts (successor to New England Electric System / NEES). Large C&I schedules include G-3 and T-2. MA is deregulated — delivery-only tariffs.",
    },
    {
        "eia_id": "21",
        "url": "https://www.mass.gov/info-details/electric-utility-tariffs",
        "url_type": "portal",
        "notes": "MA DPU electric utility tariffs page — official National Grid MA filed delivery schedules.",
    },
    # ── Public Service Co of Oklahoma / PSO / AEP (48) — 106 stores ─────────
    {
        "eia_id": "48",
        "url": "https://www.psoklahoma.com/company/regulatory/tariffs/",
        "url_type": "tariff_page",
        "notes": "PSO (AEP subsidiary). Large Power Service (LPS) schedule. OCC-regulated.",
    },
    # ── Kentucky Utilities (36) — 96 stores ─────────────────────────────────
    {
        "eia_id": "36",
        "url": "https://lge-ku.com/regulatory/tariffs/kentucky-utilities",
        "url_type": "tariff_page",
        "notes": "Kentucky Utilities (PPL). Large Power Service (LPS) schedule. KY PSC-regulated.",
    },
    # ── Louisville Gas & Electric (37) — 92 stores ──────────────────────────
    {
        "eia_id": "37",
        "url": "https://lge-ku.com/regulatory/tariffs/louisville-gas-electric",
        "url_type": "tariff_page",
        "notes": "LG&E (PPL). Large Power Service (LPS) schedule. KY PSC-regulated. Same portal as KU but separate tariff book.",
    },
    # ── Indianapolis Power & Light / AES Indiana (39) — 87 stores ───────────
    {
        "eia_id": "39",
        "url": "https://www.iplpower.com/business/rates-and-tariffs/",
        "url_type": "tariff_page",
        "notes": "IPL (AES Indiana). Large Power Service (LPS) and Commercial/Industrial TOU schedules. IURC-regulated.",
    },
    # ── CenterPoint Energy Houston Electric (3672) — 85 stores ──────────────
    {
        "eia_id": "3672",
        "url": "https://www.centerpointenergyservices.com/regulatory/tariffs",
        "url_type": "tariff_page",
        "notes": "CenterPoint Energy Houston Electric is delivery-only (ERCOT deregulated market). Applicable schedule: HT (High Tension/Large Commercial wires rate). Retail energy is through a separate REP.",
    },
    {
        "eia_id": "3672",
        "url": "https://interchange.puc.texas.gov/",
        "url_type": "portal",
        "notes": "PUCT interchange — official CenterPoint Houston delivery tariff filings.",
    },
    # ── Arizona Public Service / APS (23) — 79 stores ───────────────────────
    {
        "eia_id": "23",
        "url": "https://www.aps.com/en/Utility/Regulatory-and-Legal/Rates/Rate-Rider-Tariffs",
        "url_type": "tariff_page",
        "notes": "APS (Pinnacle West). Large Commercial/Industrial schedules include LGS (Large General Service) and LGS-TOU. AZ ACC-regulated.",
    },
    # ── AEP Texas Central Co (14469) — 79 stores ────────────────────────────
    {
        "eia_id": "14469",
        "url": "https://www.aeptexas.com/regulatory/tariffs/",
        "url_type": "tariff_page",
        "notes": "AEP Texas Central — delivery-only wires company in ERCOT deregulated market. HV (High Voltage) and MV (Medium Voltage) distribution rate schedules apply at Walmart demand levels.",
    },
    {
        "eia_id": "14469",
        "url": "https://interchange.puc.texas.gov/",
        "url_type": "portal",
        "notes": "PUCT interchange — official AEP Texas Central tariff filings.",
    },
    # ── PPL Electric Utilities (14) — 79 stores ─────────────────────────────
    {
        "eia_id": "14",
        "url": "https://www.pplweb.com/ppl-electric/rates-and-regulations/",
        "url_type": "tariff_page",
        "notes": "PPL Electric (PA). PA is deregulated — delivery-only. Schedule LP (Large Power) is the applicable delivery tariff for Walmart-scale loads.",
    },
    {
        "eia_id": "14",
        "url": "https://www.puc.pa.gov/electric/tariffs/",
        "url_type": "portal",
        "notes": "PA PUC tariff portal — official PPL Electric filed delivery schedules.",
    },
    # ── Puget Sound Energy (33) — 75 stores ─────────────────────────────────
    {
        "eia_id": "33",
        "url": "https://www.pse.com/rates/tariffs",
        "url_type": "tariff_page",
        "notes": "Puget Sound Energy (WA). Schedule 31 (Large General Service) and Schedule 48 (Large General Service TOU) are likely applicable at Walmart demand levels. WUTC-regulated.",
    },
    # ── AmerenMissouri (2) — 75 stores ──────────────────────────────────────
    {
        "eia_id": "2",
        "url": "https://www.ameren.com/missouri/business/my-account/ameren-rates",
        "url_type": "tariff_page",
        "notes": "AmerenMissouri. Large Power (LP) and Large Power Time-of-Day (LPTOD) schedules. MO PSC-regulated.",
    },
    {
        "eia_id": "2",
        "url": "https://www.efis.psc.mo.gov/mpsc/tariff.asp",
        "url_type": "portal",
        "notes": "Missouri PSC tariff portal — official AmerenMissouri filed schedules.",
    },
    # ── Xcel Energy PSCo / Colorado (5) — 73 stores ─────────────────────────
    {
        "eia_id": "5",
        "url": "https://www.xcelenergy.com/rates_and_regulations/colorado_rates_and_regulations",
        "url_type": "tariff_page",
        "notes": "Xcel Energy Public Service Co of Colorado (PSCo). Schedule C (Large Commercial) and Schedule P (Large Power Industrial) are the primary C&I tariffs. CO PUC-regulated.",
    },
    # ── Eversource CT / CL&P (22) — 73 stores ───────────────────────────────
    {
        "eia_id": "22",
        "url": "https://www.eversource.com/content/ct/business/my-account/billing-payments/rates-tariffs",
        "url_type": "tariff_page",
        "notes": "Eversource CT (Connecticut Light & Power). CT is deregulated — delivery-only. Rate 35 (Large Commercial & Industrial) is the applicable delivery tariff. CT PURA-regulated.",
    },
    {
        "eia_id": "22",
        "url": "https://portal.ct.gov/PURA/Utility-Regulation/Electric/Tariffs",
        "url_type": "portal",
        "notes": "CT PURA tariff portal — official Eversource CT filed delivery schedules.",
    },
    # ── AEP Texas North Co (14470) — 73 stores ──────────────────────────────
    {
        "eia_id": "14470",
        "url": "https://www.aeptexas.com/regulatory/tariffs/",
        "url_type": "tariff_page",
        "notes": "AEP Texas North — delivery-only wires in ERCOT. Same tariff page as AEP Texas Central; check for North-specific rate schedules if available.",
    },
    # ── Sierra Pacific Power / NV Energy (26) — 68 stores ───────────────────
    {
        "eia_id": "26",
        "url": "https://www.nvenergy.com/account-services/rates-tariffs",
        "url_type": "tariff_page",
        "notes": "NV Energy Sierra Pacific (northern NV). Same utility website as Nevada Power but separate rate jurisdiction. NV PUC-regulated.",
    },
    {
        "eia_id": "26",
        "url": "https://pucweb1.state.nv.us/TariffSearch/",
        "url_type": "portal",
        "notes": "Nevada PUC tariff search — filter for Sierra Pacific Power (northern NV) schedules.",
    },
    # ── Duke Energy Florida (5418) — 67 stores ──────────────────────────────
    {
        "eia_id": "5418",
        "url": "https://www.duke-energy.com/home/products/rates-tariffs/florida",
        "url_type": "tariff_page",
        "notes": "Duke Energy Florida. GSD-1 (General Service Demand) and GST-1 (Large Power TOU) are the applicable C&I schedules. FL PSC-regulated.",
    },
    {
        "eia_id": "5418",
        "url": "https://www.floridapsc.com/utilities/electricgas/tariffs",
        "url_type": "portal",
        "notes": "Florida PSC tariff filings — official Duke Energy Florida filed schedules.",
    },
]


def seed(dry_run: bool = False) -> None:
    create_db_and_tables()

    with Session(engine) as session:
        inserted = 0
        skipped = 0

        for entry in GAP_UTILITY_URLS:
            existing = session.exec(
                select(UtilityTariffUrlTable).where(
                    UtilityTariffUrlTable.utility_eia_id == entry["eia_id"],
                    UtilityTariffUrlTable.url == entry["url"],
                )
            ).first()

            if existing:
                skipped += 1
                continue

            row = UtilityTariffUrlTable(
                utility_eia_id=entry["eia_id"],
                url=entry["url"],
                url_type=entry["url_type"],
                status="pending",
                notes=entry.get("notes", ""),
                last_updated=datetime.utcnow(),
            )

            if dry_run:
                print(f"[dry-run] would insert: eia_id={entry['eia_id']} url={entry['url']}")
            else:
                session.add(row)
            inserted += 1

        if not dry_run:
            session.commit()

    action = "would insert" if dry_run else "inserted"
    print(f"Done — {action} {inserted} rows, skipped {skipped} duplicates.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print rows without writing to DB")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)
