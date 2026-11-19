"""Story 5.2: each approved Draft held as exactly one queued OutboundMessage (FR-26, AD-4, AD-5,
AD-6, AD-17), owned by ``sending``.

Creates ``outbound_messages``: a ULID ``id`` (later the ``X-Nextron-Message-Id``); ``draft_id``
(``fk_outbound_messages_draft_id_drafts``, no cascade); ``approval_id`` (a ULID, no foreign key
until Story 5.4's ``approvals``); ``kind`` (``ck_outbound_messages_kind``: ``step``,
``recontact``, ``reply`` or ``rebook``); ``status`` (``ck_outbound_messages_status``: the five
OM statuses); the copy: ``to_address``, ``inbox_id``
(``fk_outbound_messages_inbox_id_sending_inboxes``), ``subject``, ``body``, ``approved_hash``
(``ck_outbound_messages_approved_hash``: 64 lowercase hex) and ``unsub_token``; the references
``contact_id`` (``fk_outbound_messages_contact_id_contacts``), ``lead_id``
(``fk_outbound_messages_lead_id_leads``) and the nullable ``enrollment_id``
(``fk_outbound_messages_enrollment_id_enrollments``); the AD-4 snapshot ``tier``
(``ck_outbound_messages_tier``: NULL, ``A``, ``B`` or ``C``), ``lead_score``, ``variant_id``,
``segment_id``, ``market_id`` (no foreign keys: values as they were at enqueue) and ``step_no``
(``ck_outbound_messages_step_no``: NULL or at least 1; ``ck_outbound_messages_step_slot``: a
``step`` or ``recontact`` OM has an Enrollment and a Step number); and ``created_at``, the
nullable ``cancelled_at`` (``ck_outbound_messages_cancelled``: set exactly when ``status`` is
``cancelled``) and ``sent_at``.

Indexes: the partial unique ``uq_outbound_messages_live_draft`` on ``draft_id`` where ``status
<> 'cancelled'`` (one live OM per Draft), and ``ix_outbound_messages_contact_id`` (erasure and
the export find a Contact's OMs). Four triggers keep the copy fixed and the end final:

- ``trg_outbound_messages_no_delete`` aborts any ``DELETE``;
- ``trg_outbound_messages_insert_new`` aborts an ``INSERT`` whose ``id`` exists, or a live one
  for a Draft that already has a live OM (so ``INSERT OR REPLACE`` can't replace an OM);
- ``trg_outbound_messages_immutable`` aborts an ``UPDATE`` that changes any column but
  ``status``, ``cancelled_at``, ``sent_at``, ``lead_id`` (a Lead merge moves the OM to the
  survivor, AD-3), ``to_address``, ``subject``, ``body`` and ``unsub_token``, or changes one of
  those last four other than from non-NULL to ``[erased]`` (the erasure tombstone, AD-17);
- ``trg_outbound_messages_final`` aborts an ``UPDATE`` that changes the ``status`` of a ``sent``
  or ``cancelled`` OM (both are terminal: a cancelled OM is never revived).

Revision ID: 0048_5_2_outbound_messages
Revises: 0047_4_14_ai_grounding
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_5_2_outbound_messages"
down_revision: str | None = "0047_4_14_ai_grounding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REDACTED = "[erased]"  # nextron.domain.erasure.REDACTED
FIXED_COPY = "the copy of an OutboundMessage is fixed"
FINAL_STATUS = "a sent or cancelled OutboundMessage is final"
KINDS = ("step", "recontact", "reply", "rebook")
STATUSES = ("queued", "sending", "sent", "unknown", "cancelled")
TERMINAL = ("sent", "cancelled")
TIERS = ("A", "B", "C")
FIXED = (
    "id",
    "draft_id",
    "approval_id",
    "kind",
    "inbox_id",
    "contact_id",
    "enrollment_id",
    "approved_hash",
    "tier",
    "lead_score",
    "variant_id",
    "segment_id",
    "market_id",
    "step_no",
    "created_at",
)
REDACTABLE = ("to_address", "subject", "body", "unsub_token")


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def _changed(columns: Sequence[str]) -> str:
    return " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)


def _redaction_only(column: str) -> str:
    """The SQL of a change to ``column`` other than the erasure tombstone."""
    return (
        f"(NEW.{column} IS NOT OLD.{column} AND NOT (OLD.{column} IS NOT NULL "
        f"AND NEW.{column} IS '{REDACTED}'))"
    )


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("draft_id", sa.String(26), nullable=False),
        sa.Column("approval_id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("to_address", sa.String(254), nullable=False),
        sa.Column("inbox_id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("enrollment_id", sa.String(26), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("approved_hash", sa.String(64), nullable=False),
        sa.Column("unsub_token", sa.Text(), nullable=False),
        sa.Column("tier", sa.String(1), nullable=True),
        sa.Column("lead_score", sa.Integer(), nullable=True),
        sa.Column("variant_id", sa.String(26), nullable=True),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("market_id", sa.String(26), nullable=True),
        sa.Column("step_no", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("cancelled_at", sa.String(27), nullable=True),
        sa.Column("sent_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbound_messages"),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name="fk_outbound_messages_draft_id_drafts"
        ),
        sa.ForeignKeyConstraint(
            ["inbox_id"],
            ["sending_inboxes.id"],
            name="fk_outbound_messages_inbox_id_sending_inboxes",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_outbound_messages_contact_id_contacts"
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_outbound_messages_lead_id_leads"
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name="fk_outbound_messages_enrollment_id_enrollments",
        ),
        sa.CheckConstraint(_one_of("kind", KINDS), name=op.f("ck_outbound_messages_kind")),
        sa.CheckConstraint(
            _one_of("status", STATUSES), name=op.f("ck_outbound_messages_status")
        ),
        sa.CheckConstraint(
            "(cancelled_at IS NOT NULL) = (status = 'cancelled')",
            name=op.f("ck_outbound_messages_cancelled"),
        ),
        sa.CheckConstraint(
            "length(approved_hash) = 64 AND approved_hash NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_outbound_messages_approved_hash"),
        ),
        sa.CheckConstraint(
            f"tier IS NULL OR {_one_of('tier', TIERS)}", name=op.f("ck_outbound_messages_tier")
        ),
        sa.CheckConstraint(
            "step_no IS NULL OR step_no >= 1", name=op.f("ck_outbound_messages_step_no")
        ),
        sa.CheckConstraint(
            "kind NOT IN ('step','recontact') OR (enrollment_id IS NOT NULL AND step_no IS NOT "
            "NULL)",
            name=op.f("ck_outbound_messages_step_slot"),
        ),
    )
    op.create_index(
        "uq_outbound_messages_live_draft",
        "outbound_messages",
        ["draft_id"],
        unique=True,
        sqlite_where=sa.text("status <> 'cancelled'"),
    )
    op.create_index("ix_outbound_messages_contact_id", "outbound_messages", ["contact_id"])
    op.execute(
        "CREATE TRIGGER trg_outbound_messages_no_delete BEFORE DELETE ON outbound_messages "
        f"BEGIN SELECT RAISE(ABORT, '{FIXED_COPY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_outbound_messages_insert_new BEFORE INSERT ON outbound_messages "
        "WHEN EXISTS (SELECT 1 FROM outbound_messages WHERE id = NEW.id "
        "OR (draft_id = NEW.draft_id AND status <> 'cancelled' AND NEW.status <> 'cancelled')) "
        f"BEGIN SELECT RAISE(ABORT, '{FIXED_COPY}'); END"
    )
    redactions = " OR ".join(_redaction_only(column) for column in REDACTABLE)
    op.execute(
        "CREATE TRIGGER trg_outbound_messages_immutable BEFORE UPDATE ON outbound_messages "
        f"WHEN {_changed(FIXED)} OR {redactions} "
        f"BEGIN SELECT RAISE(ABORT, '{FIXED_COPY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_outbound_messages_final BEFORE UPDATE ON outbound_messages "
        f"WHEN {_one_of('OLD.status', TERMINAL)} AND NEW.status IS NOT OLD.status "
        f"BEGIN SELECT RAISE(ABORT, '{FINAL_STATUS}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
