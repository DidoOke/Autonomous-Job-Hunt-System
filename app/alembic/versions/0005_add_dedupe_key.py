"""0005_add_dedupe_key

Revision ID: 0005_add_dedupe_key
Revises: 0004_add_score_tier_and_company_rules
Create Date: 2026-11-02 19:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005_add_dedupe_key"
down_revision: Union[str, None] = "0004_add_score_tier_and_company_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("dedupe_key", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "duplicate_of",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_jobs_dedupe_key", "jobs", ["dedupe_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_dedupe_key", table_name="jobs")
    op.drop_constraint("jobs_duplicate_of_fkey", "jobs", type_="foreignkey")
    op.drop_column("jobs", "duplicate_of")
    op.drop_column("jobs", "dedupe_key")

