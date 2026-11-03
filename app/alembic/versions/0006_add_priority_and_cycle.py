"""0006_add_priority_and_cycle

Revision ID: 0006_add_priority_and_cycle
Revises: 0005_add_dedupe_key
Create Date: 2026-11-03 14:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006_add_priority_and_cycle"
down_revision: Union[str, None] = "0005_add_dedupe_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="2"),
    )
    op.create_index(
        "ix_sources_priority_status",
        "sources",
        ["priority", "status"],
        unique=False,
    )
    op.add_column(
        "jobs",
        sa.Column("inserted_cycle_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "inserted_cycle_id")
    op.drop_index("ix_sources_priority_status", table_name="sources")
    op.drop_column("sources", "priority")

