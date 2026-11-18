"""Story 3.11: the ``unsub_requests`` table (FR-70, FR-41, AD-15, AD-16), owned by
``compliance``.

Creates ``unsub_requests``, one row per unsubscribe request applied from the Worker's pending
list: a ULID ``id`` (the ``cause_ref`` of the suppression entries the request wrote);
``token_hash``, the lowercase hex SHA-256 of the token (``ck_unsub_requests_token_hash``: 64
lowercase hex characters; unique, ``uq_unsub_requests_token_hash``, so a request lands once);
``received_at``, when the Worker received it; and ``processed_at``, when the engine applied it.
Every column is NOT NULL. The table never holds a token, an address or an address hash.

Three triggers, as ``unsub_tokens`` (migration 0024) has, make the records permanent, so a
request can't be applied twice by deleting its row:

- ``trg_unsub_requests_no_delete`` aborts any ``DELETE``;
- ``trg_unsub_requests_immutable`` aborts any ``UPDATE``;
- ``trg_unsub_requests_no_reuse`` aborts an ``INSERT`` whose ``id`` or ``token_hash`` already
  exists, which stops ``INSERT OR REPLACE``.

Each aborts with "unsubscribe request records are permanent".

Revision ID: 0037_3_11_unsub_requests
Revises: 0036_2_12_legal_form
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_3_11_unsub_requests"
down_revision: str | None = "0036_2_12_legal_form"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMANENT = "unsubscribe request records are permanent"


def upgrade() -> None:
    op.create_table(
        "unsub_requests",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("received_at", sa.String(27), nullable=False),
        sa.Column("processed_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_unsub_requests"),
        sa.CheckConstraint(
            "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_unsub_requests_token_hash"),
        ),
    )
    op.create_index("uq_unsub_requests_token_hash", "unsub_requests", ["token_hash"], unique=True)
    op.execute(
        "CREATE TRIGGER trg_unsub_requests_no_delete BEFORE DELETE ON unsub_requests "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_unsub_requests_immutable BEFORE UPDATE ON unsub_requests "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_unsub_requests_no_reuse BEFORE INSERT ON unsub_requests "
        "WHEN EXISTS (SELECT 1 FROM unsub_requests WHERE id = NEW.id "
        "OR token_hash = NEW.token_hash) "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
