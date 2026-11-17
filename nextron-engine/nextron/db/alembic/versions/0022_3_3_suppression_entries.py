"""Story 3.3: the ``suppression_entries`` table (FR-50, FR-63, AD-4, AD-16, AD-17), owned by
``compliance``.

Creates ``suppression_entries``, the Suppression List: a ULID ``id``; the key's ``kind``
(``ck_suppression_entries_kind``: ``address``, ``domain``, ``person`` or ``profile``) and
``value_hash`` (``ck_suppression_entries_value_hash``: 64 lowercase hex characters);
``domain_plain`` (nullable; set exactly on the domain kind,
``ck_suppression_entries_domain_plain``); the ``status`` (``ck_suppression_entries_status``:
``active`` or ``voided``) and ``source`` (``ck_suppression_entries_source``: ``reply_rule``,
``reply_class``, ``one_click``, ``mailto``, ``operator``, ``import`` or ``dsr``); ``cause_ref``
(a ULID, nullable, no foreign key);
``created_at``; and ``voided_at`` and ``void_reason`` (nullable; both set exactly when the
entry is ``voided``, ``ck_suppression_entries_voided``). Every other column is NOT NULL. The
partial unique index ``uq_suppression_entries_active_key`` allows one ``active`` row per
(``kind``, ``value_hash``).

Four triggers, as ``events`` (migration 0001) and ``company_facts`` (migration 0017) have, make
the list permanent:

- ``trg_suppression_entries_no_delete`` aborts any ``DELETE``;
- ``trg_suppression_entries_no_id_reuse`` aborts an ``INSERT`` whose ``id`` already exists, or an
  ``active`` ``INSERT`` whose (``kind``, ``value_hash``) already has an ``active`` row, which
  stops ``INSERT OR REPLACE``;
- ``trg_suppression_entries_insert_active`` aborts an ``INSERT`` that isn't ``active``;
- ``trg_suppression_entries_immutable`` aborts an ``UPDATE`` that changes ``id``, ``kind``,
  ``value_hash``, ``domain_plain``, ``source``, ``cause_ref`` or ``created_at``, moves an entry
  out of ``voided``, voids an entry whose ``source`` isn't ``reply_rule`` (AD-4:
  ``VOIDABLE_SOURCES``), or changes a ``voided_at`` that is set. ``void_reason`` may change
  while the entry stays ``voided`` (Story 3.13 redacts it).

Revision ID: 0022_3_3_suppression_entries
Revises: 0021_4_2_campaigns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_3_3_suppression_entries"
down_revision: str | None = "0021_4_2_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMANENT = "suppression entries are permanent"
# Every column but status, voided_at and void_reason, which a void sets once.
FIXED_COLUMNS = ("id", "kind", "value_hash", "domain_plain", "source", "cause_ref", "created_at")


def upgrade() -> None:
    op.create_table(
        "suppression_entries",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("domain_plain", sa.String(253), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("cause_ref", sa.String(26), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("voided_at", sa.String(27), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_suppression_entries"),
        sa.CheckConstraint(
            "kind IN ('address','domain','person','profile')",
            name=op.f("ck_suppression_entries_kind"),
        ),
        sa.CheckConstraint(
            "length(value_hash) = 64 AND value_hash NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_suppression_entries_value_hash"),
        ),
        sa.CheckConstraint(
            "(kind = 'domain') = (domain_plain IS NOT NULL)",
            name=op.f("ck_suppression_entries_domain_plain"),
        ),
        sa.CheckConstraint(
            "status IN ('active','voided')", name=op.f("ck_suppression_entries_status")
        ),
        sa.CheckConstraint(
            "source IN ('reply_rule','reply_class','one_click','mailto','operator','import','dsr')",
            name=op.f("ck_suppression_entries_source"),
        ),
        sa.CheckConstraint(
            "(status = 'voided') = (voided_at IS NOT NULL) "
            "AND (status = 'voided') = (void_reason IS NOT NULL)",
            name=op.f("ck_suppression_entries_voided"),
        ),
    )
    op.create_index(
        "uq_suppression_entries_active_key",
        "suppression_entries",
        ["kind", "value_hash"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.execute(
        "CREATE TRIGGER trg_suppression_entries_no_delete BEFORE DELETE ON suppression_entries "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_suppression_entries_no_id_reuse BEFORE INSERT ON suppression_entries "
        "WHEN EXISTS (SELECT 1 FROM suppression_entries WHERE id = NEW.id "
        "OR (NEW.status = 'active' AND status = 'active' "
        "AND kind = NEW.kind AND value_hash = NEW.value_hash)) "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_suppression_entries_insert_active BEFORE INSERT ON suppression_entries "
        "WHEN NEW.status IS NOT 'active' "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in FIXED_COLUMNS)
    op.execute(
        "CREATE TRIGGER trg_suppression_entries_immutable BEFORE UPDATE ON suppression_entries "
        f"WHEN {changed} "
        "OR (OLD.status = 'voided' AND NEW.status IS NOT 'voided') "
        "OR (OLD.status IS NOT 'voided' AND NEW.status = 'voided' "
        "AND OLD.source IS NOT 'reply_rule') "
        "OR (OLD.voided_at IS NOT NULL AND NEW.voided_at IS NOT OLD.voided_at) "
        f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
