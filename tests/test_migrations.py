"""Guards on the Alembic migration history.

The failure these exist to catch: someone edits pipeline/models.py and
forgets the migration. init_db()'s create_all() would not notice (it only
adds *missing* tables), the tests would pass against a database that
happens to already be on the new shape, and the mismatch would only surface
on a machine that ran the migrations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from pipeline import db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def _db_reachable() -> bool:
    try:
        with db.engine.connect():
            pass
        return True
    except Exception:  # noqa: BLE001 - any failure here just means "skip"
        return False


def test_migrations_have_exactly_one_head():
    """Two heads mean two migrations claim the same parent — `alembic upgrade
    head` then fails outright, and the fix is a merge revision."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()

    assert len(heads) == 1, f"expected a single migration head, found {heads}"


def test_alembic_ini_does_not_contain_a_connection_string():
    """The URL carries the database password and alembic.ini is in version
    control; migrations/env.py injects it from pipeline.config instead."""
    lines = [
        line
        for line in ALEMBIC_INI.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("sqlalchemy.url")
    ]

    assert lines == [], f"alembic.ini must not set sqlalchemy.url, found: {lines}"


@pytest.mark.skipif(not _db_reachable(), reason="PostgreSQL not reachable at DATABASE_URL")
def test_models_match_the_migrations():
    """pipeline/models.py and the migration history must describe the same
    schema — a diff here means a migration is missing."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from pipeline.models import Base

    with db.engine.connect() as connection:
        diffs = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert diffs == [], (
        "pipeline/models.py has drifted from the database schema. "
        "Generate a migration: alembic revision --autogenerate -m '<what changed>' "
        f"then alembic upgrade head. Differences: {diffs}"
    )
