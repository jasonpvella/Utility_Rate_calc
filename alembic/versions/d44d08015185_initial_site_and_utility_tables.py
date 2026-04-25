"""initial site and utility tables

Revision ID: d44d08015185
Revises:
Create Date: 2026-04-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d44d08015185"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "utility",
        sa.Column("eia_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("regulatory_jurisdiction", sa.String(), nullable=False, server_default=""),
        sa.Column("market_structure", sa.String(), nullable=False, server_default=""),
        sa.Column("input_tier", sa.String(), nullable=False, server_default="TIER_2_PEAK_KW"),
        sa.Column("service_territory_wkt", sa.String(), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("eia_id"),
    )

    op.create_table(
        "site",
        sa.Column("site_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("brand", sa.String(), nullable=False),
        sa.Column("store_number", sa.String(), nullable=False, server_default=""),
        sa.Column("address", sa.String(), nullable=False, server_default=""),
        sa.Column("city", sa.String(), nullable=False, server_default=""),
        sa.Column("state", sa.String(), nullable=False, server_default=""),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("voltage_level", sa.String(), nullable=False, server_default="secondary"),
        sa.Column("estimated_peak_kw", sa.Float(), nullable=True),
        sa.Column(
            "utility_eia_id",
            sa.String(),
            sa.ForeignKey("utility.eia_id"),
            nullable=True,
        ),
        sa.Column("current_tariff_id", sa.String(), nullable=True),
        sa.Column("data_source", sa.String(), nullable=False, server_default="scraped"),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("site_id"),
    )


def downgrade() -> None:
    op.drop_table("site")
    op.drop_table("utility")
