"""0002_lever_and_dedupe

Revision ID: 0002_lever_and_dedupe
Revises: 0001_initial
Create Date: 2026-10-26 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002_lever_and_dedupe"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_jobs_fingerprint", table_name="jobs")
    op.create_index("ix_jobs_fingerprint", "jobs", ["fingerprint"], unique=False)
    op.add_column("jobs", sa.Column("source", sa.Text(), nullable=True))
    op.create_index(
        "ix_sightings_source_external",
        "sightings",
        ["source_id", "external_id"],
        unique=True,
    )
    op.execute("UPDATE jobs SET source = 'greenhouse' WHERE source IS NULL")
    op.execute(
        """
        INSERT INTO sightings (job_id, source_id, external_id)
        SELECT j.id, s.id, regexp_replace(j.apply_url, '^.*/', '')
        FROM jobs j
        JOIN sources s ON s.company_id = j.company_id
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_sightings_source_external", table_name="sightings")
    op.drop_column("jobs", "source")
    op.drop_index("ix_jobs_fingerprint", table_name="jobs")
    op.create_index("ix_jobs_fingerprint", "jobs", ["fingerprint"], unique=True)
