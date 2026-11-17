"""The startup migration (AD-2): bring the database to the code's head revision, or refuse.

Alembic is configured here in code: there is no ``alembic.ini``, no Alembic CLI path and no
``script.py.mako``. The script directory is ``nextron/db/alembic`` (``env.py`` and
``versions/``), one linear chain of hand-written, forward-only revisions whose ids follow
``NNNN_<epic>_<story>_<short slug>`` (for example ``0001_1_3_persistence_foundation``):
underscores only, at most 31 characters.

``ensure_current(engine)`` reads the database's revision, holding the write lock:

- unknown to this code: ``DatabaseAheadError`` ("database revision <rev> is newer than this
  code"); nothing is changed;
- several revisions, or tables but no revision: ``DatabaseStateError``; nothing is changed;
- at head: nothing to do;
- behind, or a new file: ``alembic upgrade head`` in one transaction, on a connection with
  ``foreign_keys`` off (``nextron.db.engine.fk_off_connection``) so a batch rebuild of a parent
  table doesn't fail on its children. ``PRAGMA foreign_key_check`` must then find nothing
  (``ForeignKeyCheckError``) before the transaction commits. Any failure rolls the whole upgrade
  back and leaves the revision as it was.

``head_revision()`` is the revision this code migrates to, ``known_revisions()`` every revision id
in its chain, and ``current_revision(engine)`` the database's own (``None`` for an empty one;
several revisions, or tables but no revision, raise ``DatabaseStateError``). ``nextron doctor``
reads them on a read-only engine and never migrates.

The ``pre_migrate`` backup before an upgrade arrives in Story 1.16.
"""

from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

from nextron.db.engine import fk_off_connection

SCRIPT_LOCATION = Path(__file__).resolve().parent / "alembic"  # tests may point it elsewhere
VERSION_TABLE: Final = "alembic_version"


class DatabaseStateError(RuntimeError):
    """The database is not one this code may migrate; nothing was changed."""


class DatabaseAheadError(DatabaseStateError):
    """The database's revision is unknown to this code, so it was made by newer code."""


class ForeignKeyCheckError(RuntimeError):
    """The upgrade left foreign key violations; it was rolled back."""


def alembic_config(script_location: Path | None = None) -> Config:
    """An Alembic ``Config`` built in code for ``script_location`` (default ``SCRIPT_LOCATION``)."""
    location = script_location if script_location is not None else SCRIPT_LOCATION
    config = Config()
    config.set_main_option("script_location", str(location).replace("%", "%%"))
    config.set_main_option("path_separator", "os")
    return config


def _script(config: Config) -> tuple[ScriptDirectory, str]:
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()  # raises when the chain has several heads
    if head is None:
        raise RuntimeError(f"no revisions in {script.versions}")
    return script, head


def head_revision(script_location: Path | None = None) -> str:
    """The revision this code migrates to."""
    return _script(alembic_config(script_location))[1]


def known_revisions(script_location: Path | None = None) -> frozenset[str]:
    """Every revision id in the chain this code knows, head included."""
    script, _head = _script(alembic_config(script_location))
    return frozenset(revision.revision for revision in script.walk_revisions())


def _stored_revision(connection: Connection) -> str | None:
    """The database's one revision, or ``None`` for an empty database; refuses anything else."""
    tables = {
        name
        for (name,) in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND substr(name, 1, 7) != 'sqlite_'"
        )
    }
    revisions: list[str] = []
    if VERSION_TABLE in tables:
        revisions = sorted(
            str(version)
            for (version,) in connection.exec_driver_sql(f"SELECT version_num FROM {VERSION_TABLE}")
        )
    if len(revisions) > 1:
        raise DatabaseStateError(
            f"the database has {len(revisions)} Alembic revisions ({', '.join(revisions)}); "
            "expected one"
        )
    if revisions:
        return revisions[0]
    if tables - {VERSION_TABLE}:
        raise DatabaseStateError(
            "the database has tables but no Alembic revision; it was not made by this engine"
        )
    return None


def current_revision(engine: Engine) -> str | None:
    """The database's revision, ``None`` for an empty database."""
    with engine.connect() as connection:
        return _stored_revision(connection)


def ensure_current(
    engine: Engine, *, script_location: Path | None = None
) -> tuple[str | None, str]:
    """Upgrade the database to head; return the revisions ``(before, after)``.

    Raises ``DatabaseAheadError`` or ``DatabaseStateError`` (nothing changed), or whatever made
    the upgrade fail, ``ForeignKeyCheckError`` included (rolled back).
    """
    config = alembic_config(script_location)
    script, head = _script(config)
    known = {revision.revision for revision in script.walk_revisions()}
    with fk_off_connection(engine) as connection, connection.begin():
        before = _stored_revision(connection)
        if before is not None and before not in known:
            raise DatabaseAheadError(f"database revision {before} is newer than this code")
        if before == head:
            return before, head
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            tables = ", ".join(sorted({str(row[0]) for row in violations}))
            raise ForeignKeyCheckError(
                f"the upgrade left {len(violations)} foreign key violations in {tables}"
            )
        after = _stored_revision(connection)
        if after != head:
            raise RuntimeError(f"the upgrade stopped at {after}, not {head}")
    return before, head
