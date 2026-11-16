"""Persistence layer (AD-2, AD-3, AD-4, AD-17).

Import rule: ``nextron.domain``, SQLAlchemy, Alembic and python-ulid. Only ``nextron.db.engine``
creates an SQLAlchemy engine or calls ``sqlite3.connect``. Each owner writes its tables only
through ``nextron.db.repos.<owner>``. ``nextron.app`` reaches this package only through ``uow``,
``eventlog`` and its own repositories (contract ``c1-app-db-surface``).

- ``engine``: the one SQLite configuration (WAL, ``BEGIN IMMEDIATE``, no stray transaction SQL).
- ``uow``: the unit of work an entry point opens, ``@use_case``, ``@command``, notifications
  and the subscriber registry.
- ``eventlog``: ``append``, one ``events`` row per state transition.
- ``models``: ``Base``, the ``Ulid`` and ``UtcDateTime`` column types and every model.
- ``owners`` and ``pii_registry``: who writes each column, and what personal data it holds.
  ``owners.py`` is also the map import-linter contract group 7 is generated from.
- ``migrations`` and ``alembic/``: the startup migration and the one linear Alembic chain.
"""
