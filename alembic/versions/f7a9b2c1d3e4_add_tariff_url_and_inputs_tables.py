"""add utility_tariff_url and utility_tariff_inputs tables

Revision ID: f7a9b2c1d3e4
Revises: a1b2c3d4e5f6
Create Date: 2026-04-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7a9b2c1d3e4"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "utility_tariff_url",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "utility_eia_id",
            sa.String(),
            sa.ForeignKey("utility.eia_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("url_type", sa.String(), nullable=False, server_default="tariff_page"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("notes", sa.String(), nullable=False, server_default=""),
        sa.Column("last_fetched", sa.DateTime(), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "utility_tariff_inputs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "utility_eia_id",
            sa.String(),
            sa.ForeignKey("utility.eia_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("schedule_code", sa.String(), nullable=False, server_default=""),
        sa.Column("schedule_name", sa.String(), nullable=False),
        sa.Column("applicability_min_kw", sa.Float(), nullable=True),
        sa.Column("applicability_max_kw", sa.Float(), nullable=True),
        sa.Column("applicability_notes", sa.String(), nullable=False, server_default=""),
        sa.Column("voltage_levels", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("inputs_required", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_url", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "extraction_status", sa.String(), nullable=False, server_default="extracted"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("raw_extraction", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("utility_tariff_inputs")
    op.drop_table("utility_tariff_url")
