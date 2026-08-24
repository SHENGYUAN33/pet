"""The database engine must carry a connect timeout.

Without one libpq waits indefinitely, so a stopped PostgreSQL turns into a
stall rather than an error: a measured `pytest` run with Docker down took
21m46s to report its skips, printing nothing throughout, because every
database-backed test module probes the connection during collection.
"""

from __future__ import annotations

import importlib

from pipeline import config, db


def test_connect_args_carry_the_configured_timeout():
    assert db.engine_connect_args() == {"connect_timeout": config.DB_CONNECT_TIMEOUT}


def test_timeout_is_short_enough_to_fail_fast():
    """The database is local, so connecting is either immediate or broken.
    libpq applies this per host address and "localhost" resolves to two, so
    the wait a developer actually sees is twice this value."""
    assert 0 < config.DB_CONNECT_TIMEOUT <= 10


def test_timeout_cannot_be_configured_to_wait_forever(monkeypatch):
    """libpq reads connect_timeout=0 as no timeout at all — the stall this
    setting exists to prevent. Someone setting 0 to 'turn it off' must not
    get the old behaviour back."""
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "0")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.DB_CONNECT_TIMEOUT >= 1
    finally:
        # Other modules hold a reference to this module object; leave it
        # holding the real environment's value rather than the patched one.
        monkeypatch.delenv("DB_CONNECT_TIMEOUT", raising=False)
        importlib.reload(config)
