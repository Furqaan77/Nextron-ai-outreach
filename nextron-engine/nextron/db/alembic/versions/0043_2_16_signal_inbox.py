"""Story 2.16: the Signal Inbox's items (FR-8, FR-11, FR-49, AD-4), owned by ``discovery``.

Creates ``signal_inbox_items``, one item pasted into the Signal Inbox: a ULID ``id``; ``url``
(2,048; stored as a source only, never fetched), ``text`` (10,000) and the nullable ``author`` and
``company`` (200); ``status`` (``ck_signal_inbox_items_status``: ``pending``, ``processed`` or
``flagged``) and the nullable ``flag_reason``, set exactly when the item is flagged
(``ck_signal_inbox_items_flag_reason``: ``organization_unknown``, ``segment_unknown``,
``parse_failed`` or ``organization_suppressed``); ``submitted_at``; the nullable
``processed_at`` and ``lead_id`` (``fk_signal_inbox_items_lead_id_leads``, indexed by
``ix_signal_inbox_items_lead_id``), set exactly when the item is processed
(``ck_signal_inbox_items_processed``); the accepted parse, each nullable: ``parsed_segment``
(64), ``parsed_signal_type`` (64), ``parsed_website`` (253), ``parsed_author_name`` (200),
``parsed_author_role`` (200), ``parsed_event_date`` (a date), ``parsed_quote`` (300) and
``extracted_by`` (128). Indexed by ``(status, submitted_at)``
(``ix_signal_inbox_items_status_submitted_at``). No foreign key cascades.

Revision ID: 0043_2_16_signal_inbox
Revises: 0042_4_9_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_2_16_signal_inbox"
down_revision: str | None = "0042_4_9_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_inbox_items",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("text", sa.String(10000), nullable=False),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("flag_reason", sa.String(32), nullable=True),
        sa.Column("submitted_at", sa.String(27), nullable=False),
        sa.Column("processed_at", sa.String(27), nullable=True),
        sa.Column("lead_id", sa.String(26), nullable=True),
        sa.Column("parsed_segment", sa.String(64), nullable=True),
        sa.Column("parsed_signal_type", sa.String(64), nullable=True),
        sa.Column("parsed_website", sa.String(253), nullable=True),
        sa.Column("parsed_author_name", sa.String(200), nullable=True),
        sa.Column("parsed_author_role", sa.String(200), nullable=True),
        sa.Column("parsed_event_date", sa.Date(), nullable=True),
        sa.Column("parsed_quote", sa.String(300), nullable=True),
        sa.Column("extracted_by", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_signal_inbox_items"),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_signal_inbox_items_lead_id_leads"
        ),
        sa.CheckConstraint(
            "status IN ('pending','processed','flagged')",
            name=op.f("ck_signal_inbox_items_status"),
        ),
        sa.CheckConstraint(
            "(status = 'flagged' AND flag_reason IN ('organization_unknown','segment_unknown',"
            "'parse_failed','organization_suppressed')) OR (status <> 'flagged' AND flag_reason "
            "IS NULL)",
            name=op.f("ck_signal_inbox_items_flag_reason"),
        ),
        sa.CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL AND lead_id IS NOT NULL) OR "
            "(status <> 'processed' AND processed_at IS NULL AND lead_id IS NULL)",
            name=op.f("ck_signal_inbox_items_processed"),
        ),
    )
    op.create_index(
        "ix_signal_inbox_items_status_submitted_at",
        "signal_inbox_items",
        ["status", "submitted_at"],
    )
    op.create_index("ix_signal_inbox_items_lead_id", "signal_inbox_items", ["lead_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
