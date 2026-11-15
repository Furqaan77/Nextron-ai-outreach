"""Persistence layer (AD-2, AD-3, AD-17).

Import rule: ``nextron.domain``, SQLAlchemy and python-ulid. Only ``nextron.db.engine``
configures SQLite connections, and each owner writes its tables only through
``nextron.db.repos.<owner>``. ``owners.py`` is the executable owner map that import-linter
contract group 7 is generated from.

Story 1.3 adds the engine, the unit of work and the event log.
"""
