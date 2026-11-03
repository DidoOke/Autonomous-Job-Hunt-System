"""0007_add_baseline_marker

Revision ID: 0007_add_baseline_marker
Revises: 0006_add_priority_and_cycle
Create Date: 2026-11-03 15:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0007_add_baseline_marker"
down_revision: Union[str, None] = "0006_add_priority_and_cycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # inserted_cycle_id column already exists from 0006.
    # Add index on inserted_cycle_id for fast cycle queries.
    op.create_index(
        "ix_jobs_inserted_cycle",
        "jobs",
        ["inserted_cycle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_inserted_cycle", table_name="jobs")

