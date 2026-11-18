"""Story 3.14: erase personal data outside the database and issue a receipt (FR-52, AD-10,
AD-15, AD-17).

Creates ``erasure_receipts`` (owner ``compliance``): one row per erasure, or per repeat erase
request on a Contact erased before. A ULID ``id``; ``contact_id``
(``fk_erasure_receipts_contact_id_contacts``, no cascade, indexed by
``ix_erasure_receipts_contact_id``); the nullable ``request_ref``
(``ck_erasure_receipts_request_ref``: 1 to 200 characters), set exactly on a ``dsr`` receipt
(``ck_erasure_receipts_dsr_ref``); ``cause`` (``ck_erasure_receipts_cause``: ``dsr`` or
``retention``); ``outcome`` (``ck_erasure_receipts_outcome``: ``erased`` or ``already_erased``);
``created_at``. Two triggers make a receipt final: ``trg_erasure_receipts_no_delete`` aborts any
``DELETE`` and ``trg_erasure_receipts_immutable`` every ``UPDATE``.

Creates ``erasure_receipt_stores`` (owner ``compliance``): one row per store of a receipt. A
ULID ``id``; ``receipt_id`` (``fk_erasure_receipt_stores_receipt_id_erasure_receipts``, no
cascade, indexed by ``ix_erasure_receipt_stores_receipt_id``); ``store``
(``ck_erasure_receipt_stores_store``: one of the 17 ``ErasureStore`` codes); ``action`` (at most
300 characters); ``status`` (``ck_erasure_receipt_stores_status``: ``pending``, ``done``,
``residual``, ``none_held`` or ``failed``); ``detail`` (``ck_erasure_receipt_stores_detail``: 1
to 1000 characters, ``ck_erasure_receipt_stores_detail_address``: no ``@``); ``at``. One row
per store of a receipt (``uq_erasure_receipt_stores_receipt_id_store``). Three triggers:

- ``trg_erasure_receipt_stores_no_delete`` aborts any ``DELETE``;
- ``trg_erasure_receipt_stores_insert`` aborts an ``INSERT`` whose ``id``, or whose
  (``receipt_id``, ``store``), exists (so ``INSERT OR REPLACE`` can't replace a row);
- ``trg_erasure_receipt_stores_settle`` aborts an ``UPDATE`` unless the row was ``pending`` and
  no longer is, with ``id``, ``receipt_id``, ``store`` and ``action`` unchanged: each store
  settles once.

``trg_data_subject_requests_receipt`` aborts an ``UPDATE`` that sets a request's ``receipt_id``
to a receipt that doesn't exist or belongs to another Contact, with "a request's receipt must
exist and be its own Contact's". It is a trigger rather than a
foreign key, so ``data_subject_requests`` isn't rebuilt.

Revision ID: 0037_3_14_erasure_receipts
Revises: 0036_2_12_legal_form
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_3_14_erasure_receipts"
down_revision: str | None = "0036_2_12_legal_form"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECEIPTS_PERMANENT = "erasure receipts are permanent"
STORES_SETTLE_ONCE = "each store of an erasure receipt settles once"
RECEIPT_MISMATCH = "a request's receipt must exist and be its own Contact's"
# nextron.domain.erasure_receipt.ErasureStore, in display order
STORES = (
    "database",
    "suppression",
    "evidence_quotes",
    "page_addresses",
    "other_contacts",
    "mailboxes",
    "crawled_pages",
    "telegram",
    "anthropic",
    "worker_kv",
    "backups",
    "ai_test_sets",
    "nextronai_inbox",
    "logs",
    "sink_files",
    "restore_work",
    "erasure_ledger",
)
STATUSES = ("pending", "done", "residual", "none_held", "failed")  # StoreStatus
STORE_FIXED = ("id", "receipt_id", "store", "action")


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def _quoted(text: str) -> str:
    """``text`` inside an SQL string literal: each ``'`` doubled."""
    return text.replace("'", "''")


def _changed(columns: Sequence[str]) -> str:
    return " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)


def upgrade() -> None:
    op.create_table(
        "erasure_receipts",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("request_ref", sa.String(200), nullable=True),
        sa.Column("cause", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_erasure_receipts"),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_erasure_receipts_contact_id_contacts"
        ),
        sa.CheckConstraint(
            "length(request_ref) BETWEEN 1 AND 200", name=op.f("ck_erasure_receipts_request_ref")
        ),
        sa.CheckConstraint(
            "(cause = 'dsr') = (request_ref IS NOT NULL)",
            name=op.f("ck_erasure_receipts_dsr_ref"),
        ),
        sa.CheckConstraint(
            "cause IN ('dsr','retention')", name=op.f("ck_erasure_receipts_cause")
        ),
        sa.CheckConstraint(
            "outcome IN ('erased','already_erased')", name=op.f("ck_erasure_receipts_outcome")
        ),
    )
    op.create_index("ix_erasure_receipts_contact_id", "erasure_receipts", ["contact_id"])
    op.create_table(
        "erasure_receipt_stores",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("receipt_id", sa.String(26), nullable=False),
        sa.Column("store", sa.String(16), nullable=False),
        sa.Column("action", sa.String(300), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_erasure_receipt_stores"),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["erasure_receipts.id"],
            name="fk_erasure_receipt_stores_receipt_id_erasure_receipts",
        ),
        sa.UniqueConstraint(
            "receipt_id", "store", name="uq_erasure_receipt_stores_receipt_id_store"
        ),
        sa.CheckConstraint(_one_of("store", STORES), name=op.f("ck_erasure_receipt_stores_store")),
        sa.CheckConstraint(
            _one_of("status", STATUSES), name=op.f("ck_erasure_receipt_stores_status")
        ),
        sa.CheckConstraint(
            "length(detail) BETWEEN 1 AND 1000", name=op.f("ck_erasure_receipt_stores_detail")
        ),
        sa.CheckConstraint(
            "instr(detail, '@') = 0", name=op.f("ck_erasure_receipt_stores_detail_address")
        ),
    )
    op.create_index(
        "ix_erasure_receipt_stores_receipt_id", "erasure_receipt_stores", ["receipt_id"]
    )
    op.execute(
        "CREATE TRIGGER trg_erasure_receipts_no_delete BEFORE DELETE ON erasure_receipts "
        f"BEGIN SELECT RAISE(ABORT, '{RECEIPTS_PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_erasure_receipts_immutable BEFORE UPDATE ON erasure_receipts "
        f"BEGIN SELECT RAISE(ABORT, '{RECEIPTS_PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_erasure_receipt_stores_no_delete BEFORE DELETE ON "
        f"erasure_receipt_stores BEGIN SELECT RAISE(ABORT, '{RECEIPTS_PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_erasure_receipt_stores_insert BEFORE INSERT ON "
        "erasure_receipt_stores WHEN EXISTS (SELECT 1 FROM erasure_receipt_stores "
        "WHERE id = NEW.id OR (receipt_id = NEW.receipt_id AND store = NEW.store)) "
        f"BEGIN SELECT RAISE(ABORT, '{RECEIPTS_PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_erasure_receipt_stores_settle BEFORE UPDATE ON "
        "erasure_receipt_stores WHEN NOT (OLD.status IS 'pending' AND NEW.status IS NOT "
        f"'pending') OR {_changed(STORE_FIXED)} "
        f"BEGIN SELECT RAISE(ABORT, '{STORES_SETTLE_ONCE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_data_subject_requests_receipt BEFORE UPDATE ON "
        "data_subject_requests WHEN NEW.receipt_id IS NOT NULL "
        "AND NEW.receipt_id IS NOT OLD.receipt_id AND NOT EXISTS (SELECT 1 FROM "
        "erasure_receipts WHERE id = NEW.receipt_id AND contact_id = NEW.contact_id) "
        f"BEGIN SELECT RAISE(ABORT, '{_quoted(RECEIPT_MISMATCH)}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
