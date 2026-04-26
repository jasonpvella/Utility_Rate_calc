"""add tariff table

Revision ID: a1b2c3d4e5f6
Revises: d44d08015185
Create Date: 2026-04-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d44d08015185"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tariff",
        sa.Column("tariff_id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "utility_eia_id",
            sa.String(),
            sa.ForeignKey("utility.eia_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rate_code", sa.String(), nullable=False, server_default=""),
        sa.Column("availability", sa.String(), nullable=False, server_default="optional"),
        sa.Column("effective_date", sa.String(), nullable=False, server_default=""),
        sa.Column("end_date", sa.String(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tariff_id"),
    )


def downgrade() -> None:
    op.drop_table("tariff")
