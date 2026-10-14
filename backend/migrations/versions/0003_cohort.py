"""Counterfactual cohort: forward price checks of every evaluated token."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cohort",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("token_key", sa.String(), nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_cohort_token_key", "cohort", ["token_key"])
    op.create_index("ix_cohort_done", "cohort", ["done"])


def downgrade():
    op.drop_index("ix_cohort_done", "cohort")
    op.drop_index("ix_cohort_token_key", "cohort")
    op.drop_table("cohort")
