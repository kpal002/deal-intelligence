"""Database engine and session management.

Thin wrapper over SQLAlchemy 2.0: one lazily-constructed engine, a session
factory, and a context manager that commits on success / rolls back on error.
The rest of the codebase imports ``session_scope`` and never touches the engine
directly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from simpero.config import get_settings
from simpero.orm.tables import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, constructing it on first use.

    Returns:
        The shared :class:`~sqlalchemy.Engine`, configured from
        :func:`simpero.config.get_settings`.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
        logger.info("Created SQLAlchemy engine for %s", _engine.url.render_as_string())
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, constructing it on first use.

    Returns:
        A configured :class:`~sqlalchemy.orm.sessionmaker`.
    """
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


def init_db() -> None:
    """Create all tables defined on :class:`simpero.orm.tables.Base`.

    Idempotent — safe to call on every startup. For real schema evolution this
    would be replaced by Alembic migrations (scaffolded under ``migrations/``).
    """
    Base.metadata.create_all(bind=get_engine())
    logger.info("Initialized database schema (create_all)")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session scope.

    Commits on clean exit, rolls back on any exception, and always closes the
    session. Use as ``with session_scope() as session: ...``.

    Yields:
        An active :class:`~sqlalchemy.orm.Session`.

    Raises:
        Exception: Re-raises whatever the wrapped block raised, after rollback.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Session rolled back due to an error")
        raise
    finally:
        session.close()
