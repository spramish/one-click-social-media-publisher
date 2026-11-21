"""Database engine, session factory and transaction management."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ``pool_pre_ping`` recycles dead connections (important behind Docker/NAT).
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    echo=settings.debug and settings.environment == "development",
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Run a unit of work inside one transaction.

    Commits when the block exits normally, rolls back on any exception and
    always closes the session. This is the entry point for Celery tasks,
    management scripts and tests — anywhere there is no FastAPI request to
    manage the session lifetime.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session per request.

    The session is rolled back if the handler raises. The route or service
    decides when to commit, so one request can span several transactions.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
