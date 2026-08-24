from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pipeline import config
from pipeline.models import Base

engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create tables if they don't exist — for a throwaway database (tests,
    a scratch local instance) where starting from empty is the point.

    This is NOT how the schema evolves any more. Changing an existing table
    means writing an Alembic migration (`alembic revision --autogenerate`,
    then `alembic upgrade head`); create_all() only ever adds missing
    tables, so it would silently leave an existing database on the old
    shape. See migrations/ and the README's schema section.
    """
    Base.metadata.create_all(engine)


@contextmanager
def get_session():
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
