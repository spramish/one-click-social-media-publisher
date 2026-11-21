"""Business services (authentication, email, ...).

Services own the unit of work: they receive a :class:`~sqlalchemy.orm.Session`,
perform the domain work and leave committing to the caller — a FastAPI route via
``get_db``, a Celery task via ``session_scope``, or a test.

Import concrete modules directly (for example ``from app.services import auth``)
rather than re-exporting everything here, so this package stays dependency-free.
"""
