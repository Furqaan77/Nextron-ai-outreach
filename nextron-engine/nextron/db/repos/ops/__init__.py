"""Repositories of the ``ops`` service: the only writers of its tables (AD-3).

Only ``nextron.app.ops`` and ``nextron.app.queries.ops`` import this package (contract
group 7), and the composition root, which binds ``snapshot.snapshot`` to the database file for
the ``ops`` service's ``LocalBackups``, passes ``snapshot.inspect_copy`` to its
``RestoreTests`` and runs ``restore``'s file work in ``nextron restore``.

- ``heartbeat`` (Story 1.11): the repository of the ``runner_heartbeat`` table;
- ``backups`` (Story 1.16): the repository of the ``backups`` table;
- ``snapshot`` (Story 1.16) writes no table: it copies the database with SQLite's backup API
  into a new file, verifies the copy and describes it, and ``discard`` deletes a copy;
  ``inspect_copy`` (Story 1.18) reads a restored copy read-only and describes it, for the
  restore test;
- ``restore_tests`` (Story 1.18): the repository of the ``restore_tests`` table;
- ``recovery`` (Story 1.19): the repository of the ``recovery_state`` table;
- ``restore`` (Story 1.19) writes no table: ``nextron restore``'s file work, all through
  ``nextron.db.engine`` (copy a backup file into a candidate, read its newest event, save the
  current database, swap the candidate in, check the result).
"""
