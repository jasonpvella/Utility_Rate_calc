"""Database engine and session factory for VoltRegistry.

SQLite via SQLModel.  DB file lives at data/voltregistry.db relative to the
repo root.  Call ``create_db_and_tables()`` once at startup (bootstrap or API).
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine  # noqa: F401 — re-exported

# Resolve the repo root → data/voltregistry.db regardless of cwd
_REPO_ROOT = Path(__file__).resolve().parents[2]  # src/voltregistry → src → repo
_DB_PATH = os.environ.get("VOLTREGISTRY_DB", str(_REPO_ROOT / "data" / "voltregistry.db"))

_ENGINE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(
    _ENGINE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    """Create all tables defined via SQLModel metadata (idempotent)."""
    # Ensure data/ directory exists
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yields a database session."""
    with Session(engine) as session:
        yield session
