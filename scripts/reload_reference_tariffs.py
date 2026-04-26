"""Reload reference tariff JSON files into the VoltRegistry DB.

Runs only Step 8 of bootstrap.py — no network calls, no site/utility refresh.
Use this after editing a reference tariff JSON file to push the change into
the local SQLite DB immediately.

Usage:
    python scripts/reload_reference_tariffs.py
    VOLTREGISTRY_DB=/tmp/voltregistry.db python scripts/reload_reference_tariffs.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from sqlmodel import Session  # noqa: E402

from voltregistry.db import create_db_and_tables, engine  # noqa: E402
from voltregistry.models import TariffTable  # noqa: E402
from voltregistry.tariffs.models import TariffBundle  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("reload_reference_tariffs")

_REFERENCE_DIR = _REPO_ROOT / "src" / "voltregistry" / "tariffs" / "reference"


def reload() -> None:
    create_db_and_tables()

    with Session(engine) as session:
        loaded = 0
        for json_path in sorted(_REFERENCE_DIR.glob("*.json")):
            try:
                bundle = TariffBundle.model_validate_json(
                    json_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                logger.warning("Skipping %s — parse error: %s", json_path.name, exc)
                continue

            t = bundle.tariff
            existing = session.get(TariffTable, t.tariff_id)
            payload = bundle.model_dump_json()
            avail = t.availability.value if hasattr(t.availability, "value") else str(t.availability)

            if existing:
                existing.utility_eia_id = t.utility_eia_id
                existing.name = t.name
                existing.rate_code = t.rate_code
                existing.availability = avail
                existing.effective_date = t.effective_date
                existing.end_date = t.end_date
                existing.payload_json = payload
                existing.last_updated = datetime.utcnow()
                logger.info("Updated  %-30s → EIA %s", t.tariff_id, t.utility_eia_id)
            else:
                session.add(TariffTable(
                    tariff_id=t.tariff_id,
                    utility_eia_id=t.utility_eia_id,
                    name=t.name,
                    rate_code=t.rate_code,
                    availability=avail,
                    effective_date=t.effective_date,
                    end_date=t.end_date,
                    payload_json=payload,
                    last_updated=datetime.utcnow(),
                ))
                logger.info("Inserted %-30s → EIA %s", t.tariff_id, t.utility_eia_id)
            loaded += 1

        session.commit()
        logger.info("\nDone — %d reference tariffs reloaded.", loaded)


if __name__ == "__main__":
    reload()
