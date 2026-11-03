"""0004_add_score_tier_and_company_rules

Revision ID: 0004_add_score_tier_and_company_rules
Revises: 0003_add_is_test
Create Date: 2026-11-02 11:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_score_tier_and_company_rules"
down_revision: Union[str, None] = "0003_add_is_test"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
    op.add_column(
        "jobs",
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "jobs",
        sa.Column("tier", sa.String(length=10), nullable=False, server_default="drop"),
    )
    op.add_column(
        "jobs",
        sa.Column("matched_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_jobs_tier_score",
        "jobs",
        ["tier", "score"],
        postgresql_ops={"score": "DESC"},
    )
    op.create_table(
        "company_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "force_tech",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "force_drop",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("company_rules")
    op.drop_index("ix_jobs_tier_score", table_name="jobs")
    op.drop_column("jobs", "matched_rules")
    op.drop_column("jobs", "tier")
    op.drop_column("jobs", "score")

