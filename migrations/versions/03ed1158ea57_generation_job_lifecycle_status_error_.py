"""generation job lifecycle: status, error, finished_at

Revision ID: 03ed1158ea57
Revises: 26e4451f27e9
Create Date: 2026-08-24 21:09:31.601352

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03ed1158ea57"
down_revision: str | Sequence[str] | None = "26e4451f27e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # status is NOT NULL but the table already holds rows, so it cannot be
    # added in one statement: add it nullable, backfill, then tighten. The
    # existing rows all predate the lifecycle columns and were only ever
    # written after their run completed, so they are all "done".
    op.add_column("generation_jobs", sa.Column("status", sa.String(), nullable=True))
    op.add_column("generation_jobs", sa.Column("error", sa.String(), nullable=True))
    op.add_column(
        "generation_jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE generation_jobs SET status = 'done', finished_at = created_at WHERE status IS NULL"
    )
    op.alter_column("generation_jobs", "status", existing_type=sa.String(), nullable=False)

    # Only set once the render succeeds, so it has to admit NULL now.
    op.alter_column("generation_jobs", "output_path", existing_type=sa.VARCHAR(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Rows for runs that never produced a file have no output_path, and the
    # pre-lifecycle schema has nowhere to record that they failed — drop
    # them rather than invent a path for them.
    op.execute("DELETE FROM generation_jobs WHERE output_path IS NULL")
    op.alter_column("generation_jobs", "output_path", existing_type=sa.VARCHAR(), nullable=False)
    op.drop_column("generation_jobs", "finished_at")
    op.drop_column("generation_jobs", "error")
    op.drop_column("generation_jobs", "status")
