"""Isolated supervised ledger; never reuse simulated capital."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "live_book",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_table(
        "live_orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("active_slot", sa.Integer(), nullable=True, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("live_orders")
    op.drop_table("live_book")
