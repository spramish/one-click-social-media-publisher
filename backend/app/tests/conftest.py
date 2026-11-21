"""Shared pytest fixtures for the backend test suite.

Database tests run against a dedicated ``publisher_test`` database that is
rebuilt once per session (drop schema → ``alembic upgrade head``). Building it
through Alembic rather than ``metadata.create_all`` means the migrations
themselves are exercised, and the seeded roles/plans are available.

Each test then runs inside a transaction that is rolled back afterwards, so
tests never observe each other's rows and the seeded reference data is always
present in a pristine state.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# --- Point the application at the test database BEFORE importing app code ---
# ``app.core.config`` caches its Settings on first import, so this must happen
# as early as possible. pydantic-settings gives real environment variables
# priority over any ``.env`` file, so ``DATABASE_URL`` wins.
_TEST_DB = os.getenv("TEST_POSTGRES_DB", "publisher_test")
_BASE_URL = (
    f"postgresql+psycopg://{os.getenv('POSTGRES_USER', 'publisher')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'publisher_dev_password')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5432')}"
)
TEST_DATABASE_URL = f"{_BASE_URL}/{_TEST_DB}"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# The weakest bcrypt work factor bcrypt accepts. Hashing is deliberately slow;
# with the production factor of 12 the auth suite would spend minutes inside
# bcrypt instead of exercising application logic.
os.environ.setdefault("PASSWORD_HASH_ROUNDS", "4")

# Rate limiting is off for the suite: a test that signs in repeatedly would
# otherwise trip a limit meant for an attacker. The limiter's own tests turn it
# back on explicitly (see ``test_security_hardening.py``).
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

# Sandbox mode is a demonstration switch, not a test switch. A developer's
# ``.env`` may enable it, but the suite must always exercise the real
# providers — its mocks replace their HTTP transport. Real environment
# variables win over ``.env``, so this pins it off for every test; the sandbox
# tests opt back in with ``monkeypatch`` (see ``test_sandbox.py``).
os.environ.setdefault("DEMO_MODE", "false")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


@pytest.fixture(scope="session")
def engine():
    """Session-wide engine bound to ``publisher_test`` (created if missing)."""
    admin = create_engine(
        f"{_BASE_URL}/postgres",
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": _TEST_DB},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{_TEST_DB}"'))
    finally:
        admin.dispose()

    eng = create_engine(TEST_DATABASE_URL, poolclass=NullPool, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def migrated_database(engine):
    """Rebuild the schema from migrations exactly once per session.

    Not ``autouse``: only the ``db`` fixture requests it, so a run limited to
    unit tests (``pytest app/tests/test_models.py``) never touches PostgreSQL.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
    return engine


@pytest.fixture
def db(engine, migrated_database) -> Session:
    """A session whose changes are rolled back when the test ends."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def api_client(db: Session) -> Iterator[TestClient]:
    """A ``TestClient`` whose requests share the test's rolled-back session.

    ``get_db`` is overridden with the ``db`` session, so rows created inside a
    request are visible to the test (and vice versa) while everything is still
    discarded at the end of the test. ``raise_server_exceptions`` stays at its
    default so an unexpected 500 fails the test instead of being reported as a
    passing error response.
    """
    from app.db.session import get_db
    from app.main import app

    def _override_get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Media (Phase 5)
# ---------------------------------------------------------------------------
@pytest.fixture
def media_storage(tmp_path):
    """A storage backend rooted in the test's temporary directory.

    Uploads in a test therefore never touch the developer's real
    ``backend/var/media`` tree.
    """
    from app.services.storage import LocalStorage

    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    return LocalStorage(root)


@pytest.fixture
def media_client(api_client, media_storage):
    """An ``api_client`` whose media endpoints write to :func:`media_storage`."""
    from app.main import app
    from app.services.storage import get_storage

    app.dependency_overrides[get_storage] = lambda: media_storage
    try:
        yield api_client
    finally:
        app.dependency_overrides.pop(get_storage, None)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """A tiny real MP4, produced by FFmpeg, for inspection and thumbnail tests.

    Generated rather than committed so the repository carries no binary blob,
    and skipped — not failed — when FFmpeg is unavailable.
    """
    import subprocess

    from app.services import video as video_service

    ffmpeg = video_service.ffmpeg_path()
    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed; video generation tests need it")

    destination = tmp_path_factory.mktemp("sample") / "sample.mp4"
    result = subprocess.run(  # noqa: S603 - fixed argv, resolved binary, no shell
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-t",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"FFmpeg could not build the sample video: {result.stderr.strip()[-300:]}")
    return destination

