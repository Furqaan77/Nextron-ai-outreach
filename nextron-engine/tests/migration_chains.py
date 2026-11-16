"""Test helpers for the startup migration: temporary Alembic chains and database files.

A temporary chain is a script directory holding the real ``env.py`` and a linear chain of
hand-written revisions, for the "behind" and "a migration fails" cases (the real chain has one
revision). Point ``nextron.db.migrations.SCRIPT_LOCATION`` at one, or pass it as
``script_location``.
"""

import hashlib
import shutil
import sqlite3
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine

from nextron.db.engine import create_engine
from nextron.db.migrations import SCRIPT_LOCATION

REVISION = '''"""A temporary revision for the migration tests."""

import sqlalchemy as sa
from alembic import op

revision = "{revision}"
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
{body}


def downgrade() -> None:
    raise NotImplementedError
'''


def create_table(name: str) -> str:
    """An upgrade body that creates table ``name`` with an integer ``id``."""
    return f"op.create_table({name!r}, sa.Column('id', sa.Integer, primary_key=True))"


def add_revision(location: Path, revision: str, body: str, down_revision: str | None) -> None:
    source = REVISION.format(
        revision=revision,
        down_revision=down_revision,
        body=textwrap.indent(textwrap.dedent(body).strip(), "    "),
    )
    (location / "versions" / f"{revision}.py").write_text(source, encoding="utf-8")


def chain(folder: Path, *revisions: tuple[str, str]) -> Path:
    """A script directory under ``folder`` with a linear chain of ``(id, upgrade body)``."""
    location = folder / "chain"
    (location / "versions").mkdir(parents=True)
    shutil.copy2(SCRIPT_LOCATION / "env.py", location / "env.py")
    extend(location, *revisions)
    return location


def extend(location: Path, *revisions: tuple[str, str]) -> None:
    """Append ``(id, upgrade body)`` revisions to the chain at ``location``."""
    existing = sorted(path.stem for path in (location / "versions").glob("*.py"))
    down = existing[-1] if existing else None
    for revision, body in revisions:
        add_revision(location, revision, body, down)
        down = revision


@contextmanager
def engine_on(path: Path) -> Iterator[Engine]:
    """An engine on ``path``, disposed on exit (which checkpoints the WAL into the file)."""
    engine = create_engine(path)
    try:
        yield engine
    finally:
        engine.dispose()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tables(path: Path) -> set[str]:
    reader = sqlite3.connect(path)
    try:
        return {
            name
            for (name,) in reader.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        reader.close()


def stored_revisions(path: Path) -> list[str]:
    """The ``alembic_version`` rows of ``path``, read outside the engine."""
    reader = sqlite3.connect(path)
    try:
        return [version for (version,) in reader.execute("SELECT version_num FROM alembic_version")]
    finally:
        reader.close()


def run_sql(path: Path, *statements: str) -> None:
    """Change the file outside the engine, as another tool would."""
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        for statement in statements:
            writer.execute(statement)
    finally:
        writer.close()
