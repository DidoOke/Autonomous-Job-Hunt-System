"""0003_add_is_test

Revision ID: 0003_add_is_test
Revises: 0002_lever_and_dedupe
Create Date: 2026-10-31 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_is_test"
down_revision: Union[str, None] = "0002_lever_and_dedupe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("is_test", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index("ix_jobs_is_test", "jobs", ["is_test"])

    # Backfill is_test for existing test jobs
    op.execute(
        r"""
        UPDATE jobs
        SET is_test = true
        WHERE title ~* '(\y(?!(latest|contest|protest|greatest|fastest|smartest)\y)\w*test[a-z0-9_-]*(?![\s_-]+(\w+[\s_-]+){0,2}(engineer|analyst|developer|manager|lead|architect|specialist|consultant|coordinator|automation|qa|ops|scientist|designer|writer|strategist|planner|director|senior|principal|graduate|junior|intern|associate|technician|design))|\y(canary|placeholder|dummy)\y|\w*sample[\s_-]*job\y|\y(job|vacancy|posting)\s+test\y)'
        """
    )

    # Backfill is_uk for existing jobs with gb/uk location
    op.execute(
        """
        UPDATE jobs
        SET is_uk = true
        WHERE location_raw ~* '(\\mgb\\M|\\muk\\M|united kingdom|great britain|england|scotland|wales|northern ireland)'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_is_test", table_name="jobs")
    op.drop_column("jobs", "is_test")

