"""Story 3.8: the ``unsub_tokens`` table (FR-70, AD-15, AD-17), owned by ``compliance``.

Creates ``unsub_tokens``, one row per issued unsubscribe token: a ULID ``id``; ``token_hash``,
the lowercase hex SHA-256 of the token (``ck_unsub_tokens_token_hash``: 64 lowercase hex
characters; unique, ``uq_unsub_tokens_token_hash``); ``om_id``, the ULID of the OutboundMessage
it was issued for, with no foreign key (unique, ``uq_unsub_tokens_om_id``); ``kid``, the key id
(``ck_unsub_tokens_kid``: 1 to 255); and ``issued_at``, in whole seconds
(``ck_unsub_tokens_issued_at``). Every column is NOT NULL. ``ix_unsub_tokens_kid_issued_at``
serves "the newest token of each kid". The table never holds a token, an address or an address
hash.

Three triggers, as ``suppression_entries`` (migration 0019) has, make the records permanent, so
the 2-year key check can't be defeated by deleting rows:

- ``trg_unsub_tokens_no_delete`` aborts any ``DELETE``;
- ``trg_unsub_tokens_immutable`` aborts any ``UPDATE``;
- ``trg_unsub_tokens_no_reuse`` aborts an ``INSERT`` whose ``id``, ``token_hash`` or ``om_id``
  already exists, which stops ``INSERT OR REPLACE``.

Each aborts with "unsubscribe token records are permanent".

Revision ID: 0020_3_8_unsub_tokens
Revises: 0019_3_3_suppression_entries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_3_8_unsub_tokens"
down_revision: str | None = "0019_3_3_suppression_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMANENT = "unsubscribe token records are permanent"


def upgrade() -> None:
    op.create_table(
        "unsub_tokens",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("om_id", sa.String(26), nullable=False),
        sa.Column("kid", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_unsub_tokens"),
        sa.CheckConstraint(
            "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_unsub_tokens_token_hash"),
        ),
        sa.CheckConstraint("kid BETWEEN 1 AND 255", name=op.f("ck_unsub_tokens_kid")),
        sa.CheckConstraint(
            "issued_at LIKE '%.000000Z'", name=op.f("ck_unsub_tokens_issued_at")
        ),
    )
    op.create_index("uq_unsub_tokens_token_hash", "unsub_tokens", ["token_hash"], unique=True)
    op.create_index("uq_unsub_tokens_om_id", "unsub_tokens", ["om_id"], unique=True)
    op.create_index("ix_unsub_tokens_kid_issued_at", "unsub_tokens", ["kid", "issued_at"])
    op.execute(
        "CREATE TRIGGER trg_unsub_tokens_no_delete BEFORE DELETE ON unsub_tokens "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_unsub_tokens_immutable BEFORE UPDATE ON unsub_tokens "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_unsub_tokens_no_reuse BEFORE INSERT ON unsub_tokens "
        "WHEN EXISTS (SELECT 1 FROM unsub_tokens WHERE id = NEW.id "
        "OR token_hash = NEW.token_hash OR om_id = NEW.om_id) "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
