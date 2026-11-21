"""SQLAlchemy declarative base.

Every ORM model (Phase 2 onward) inherits from :class:`Base` so that Alembic
can autogenerate migrations from a single metadata object.

A deterministic ``naming_convention`` is attached to the metadata so every
index, unique, check, foreign-key and primary-key constraint receives a stable,
predictable name. Alembic needs this to reliably autogenerate later
``ALTER``/``DROP`` operations instead of guessing at anonymous constraints.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
