"""rename the outpaint job fields to background and record the treatment

Autogenerate proposed dropping the outpaint_* columns and adding the
background_* ones, which would have thrown away the settings of every run
made before the rename — and those rows are the provenance record the
project is required to keep. They are renames instead, so the existing runs
keep their settings and stay resumable.

background_mode is genuinely new. Every run that already has a generated
background was made before "replace" existed, so those rows are backfilled
with "extend": that is not a guess, it is what they actually did.

Revision ID: 45f1b54e4190
Revises: 2f878966c440
Create Date: 2026-08-25 16:04:42.769641

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "45f1b54e4190"
down_revision: str | Sequence[str] | None = "2f878966c440"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("generation_jobs", "outpaint_scenes", new_column_name="background_scenes")
    op.alter_column("generation_jobs", "outpaint_prompt", new_column_name="background_prompt")
    op.add_column("generation_jobs", sa.Column("background_mode", sa.String(), nullable=True))

    op.alter_column("scene_jobs", "outpaint_prompt", new_column_name="background_prompt")
    op.add_column("scene_jobs", sa.Column("background_mode", sa.String(), nullable=True))

    op.execute(
        "UPDATE generation_jobs SET background_mode = 'extend' WHERE background_scenes IS NOT NULL"
    )
    op.execute("UPDATE scene_jobs SET background_mode = 'extend' WHERE image_provider IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("scene_jobs", "background_mode")
    op.alter_column("scene_jobs", "background_prompt", new_column_name="outpaint_prompt")

    op.drop_column("generation_jobs", "background_mode")
    op.alter_column("generation_jobs", "background_prompt", new_column_name="outpaint_prompt")
    op.alter_column("generation_jobs", "background_scenes", new_column_name="outpaint_scenes")
