"""Story 1.17: the off-PC columns of ``backups`` (AD-9, AD-17, AD-20, OP-6), owned by ``ops``.

Adds six nullable columns to ``backups`` with SQLite's ``ALTER TABLE ... ADD COLUMN``, so the
table and its rows stay as they are: ``offpc_status`` (``copied``, ``failed`` or
``not_configured``), ``offpc_name`` (the encrypted copy's file name), ``offpc_ledger``
(``copied`` or ``absent``), ``offpc_at``, ``offpc_deleted_at`` and ``offpc_error``. Existing
rows get ``NULL`` in each: no off-PC step ran for them.

The last column carries the three checks, as named column constraints (SQLite enforces a column
CHECK that names other columns, and tests every existing row against it when the column is
added): ``ck_backups_offpc_status`` and ``ck_backups_offpc_ledger`` (``NULL`` or one of their
values) and ``ck_backups_offpc`` (the outcome is whole: ``NULL`` with every off-PC column
``NULL``; ``copied`` on a verified row with a name, a ledger status and a time, and no error;
``failed`` on a verified row with an error and a time, and no name, ledger status or
``offpc_deleted_at``; ``not_configured`` on a verified row with a time only). Each check starts
on a line of its own in the stored ``CREATE TABLE`` text, where the schema test reads it.

Revision ID: 0008_1_17_offpc
Revises: 0007_1_16_backups
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_1_17_offpc"
down_revision: str | None = "0007_1_16_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OFFPC_STATUS_CHECK = (
    "offpc_status IS NULL OR offpc_status IN ('copied','failed','not_configured')"
)
OFFPC_LEDGER_CHECK = "offpc_ledger IS NULL OR offpc_ledger IN ('copied','absent')"
OFFPC_CHECK = (
    "(offpc_status IS NULL AND offpc_name IS NULL AND offpc_ledger IS NULL "
    "AND offpc_at IS NULL AND offpc_deleted_at IS NULL AND offpc_error IS NULL) "
    "OR (offpc_status IS 'copied' AND status = 'verified' AND offpc_name IS NOT NULL "
    "AND offpc_ledger IS NOT NULL AND offpc_at IS NOT NULL AND offpc_error IS NULL) "
    "OR (offpc_status IS 'failed' AND status = 'verified' AND offpc_error IS NOT NULL "
    "AND offpc_at IS NOT NULL AND offpc_name IS NULL AND offpc_ledger IS NULL "
    "AND offpc_deleted_at IS NULL) "
    "OR (offpc_status IS 'not_configured' AND status = 'verified' AND offpc_at IS NOT NULL "
    "AND offpc_name IS NULL AND offpc_ledger IS NULL AND offpc_deleted_at IS NULL "
    "AND offpc_error IS NULL)"
)


def upgrade() -> None:
    op.execute("ALTER TABLE backups ADD COLUMN offpc_status VARCHAR(16)")
    op.execute("ALTER TABLE backups ADD COLUMN offpc_name TEXT")
    op.execute("ALTER TABLE backups ADD COLUMN offpc_ledger VARCHAR(16)")
    op.execute("ALTER TABLE backups ADD COLUMN offpc_at VARCHAR(27)")
    op.execute("ALTER TABLE backups ADD COLUMN offpc_deleted_at VARCHAR(27)")
    op.execute(
        "ALTER TABLE backups ADD COLUMN offpc_error VARCHAR(500)\n"
        f"CONSTRAINT ck_backups_offpc_status CHECK ({OFFPC_STATUS_CHECK})\n"
        f"CONSTRAINT ck_backups_offpc_ledger CHECK ({OFFPC_LEDGER_CHECK})\n"
        f"CONSTRAINT ck_backups_offpc CHECK ({OFFPC_CHECK})"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
