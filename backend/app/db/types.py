"""Reusable SQLAlchemy column types."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


def enum_column(
    py_enum: type[enum.Enum],
    name: str,
    *,
    length: int = 32,
) -> SAEnum:
    """Return a ``VARCHAR``-backed enum column with a named ``CHECK`` constraint.

    ``native_enum=False`` stores the value as ``VARCHAR`` plus a ``CHECK``
    constraint instead of creating a PostgreSQL ``ENUM`` type. This is a
    deliberate trade-off for this project:

    * adding a new platform or status in a later phase is a plain constraint
      swap, not an ``ALTER TYPE`` that cannot be mixed with other DDL inside a
      transaction;
    * the allowed values are visible directly in the table definition, which
      makes the schema self-documenting;
    * ``values_callable`` stores enum *values* (``"instagram"``) rather than the
      Python member *names* (``"INSTAGRAM"``).

    The constraint name comes from ``name`` via the ``ck`` naming convention, so
    it becomes ``ck_<table>_<name>``.
    """
    return SAEnum(
        py_enum,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )
