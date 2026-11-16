"""Alembic environment (AD-2): migrations run only on the connection the engine hands over.

``nextron.db.migrations.ensure_current`` puts its connection, already inside a transaction with
``foreign_keys`` off, in ``config.attributes["connection"]`` and runs ``upgrade head``. This file
never creates an engine or a connection, and refuses to run without one: there is no
``alembic.ini`` and no Alembic CLI path, and offline (``--sql``) mode is refused too.

``render_as_batch=True`` makes ``op.alter_column`` and friends rebuild SQLite tables in batch
mode. A batch rebuild drops a table's triggers, so a migration that rebuilds ``events`` must
create its triggers again; the triggers-at-head contract test catches one that doesn't.
"""

from alembic import context

from nextron.db.models import Base

connection = context.config.attributes.get("connection")
if context.is_offline_mode() or connection is None:
    raise RuntimeError(
        "nextron migrations run only through nextron.db.migrations, which hands env.py a "
        "connection from nextron.db.engine"
    )

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,
)

with context.begin_transaction():
    context.run_migrations()
