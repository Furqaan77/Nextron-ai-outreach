"""Repositories of the ``ops`` service: the only writers of its tables (AD-3).

Only ``nextron.app.ops`` and ``nextron.app.queries.ops`` import this package (contract
group 7), and the composition root, which binds ``snapshot.snapshot`` to the database file for
the ``ops`` service's ``LocalBackups``.

- ``heartbeat`` (Story 1.11): the repository of the ``runner_heartbeat`` table;
- ``backups`` (Story 1.16): the repository of the ``backups`` table;
- ``snapshot`` (Story 1.16) writes no table: it copies the database with SQLite's backup API
  into a new file, verifies the copy and describes it, and ``discard`` deletes a copy.
"""
