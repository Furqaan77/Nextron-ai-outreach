"""Story 4.16: draft a Signal Inbox Lead when it is ready (FR-8, FR-19, AD-8, AD-10, AD-21),
three columns of ``signal_inbox_items``, owned by ``discovery``.

Rebuilds ``signal_inbox_items`` (``batch_alter_table(recreate="always")`` from 0054's exact
table, given as ``copy_from``, so nothing depends on reflection) with:

- ``draft_when_ready`` (Boolean, NOT NULL, server default 0; PII class ``none``): the
  submission's "Draft Step 1 when ready" checkbox; every existing row backfills ``false``, so an
  item submitted before this story never triggers drafting;
- ``draft_outcome`` (at most 32 characters, nullable; PII class ``none``): what scoring decided
  for the item: ``enrolled``, ``disqualified``, a ``PickReason`` or a ``RefusalCode`` value
  (no value CHECK: the code keeps the list);
- ``draft_decided_at`` (UTC text, 27 characters, nullable; PII class ``none``): when;
- ``ck_signal_inbox_items_draft_decided``: both NULL or both set.

Every row, column, check, index and foreign key of ``signal_inbox_items`` is kept; the table has
no trigger. ``env.py`` runs with ``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0065_4_16_draft_when_ready
Revises: 0064_4_15_draft_failed
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_4_16_draft_when_ready"
down_revision: str | None = "0064_4_15_draft_failed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAFT_DECIDED_CHECK = "(draft_outcome IS NULL) = (draft_decided_at IS NULL)"


def _items_0054() -> sa.Table:
    """``signal_inbox_items`` exactly as ``0054_2_16_signal_inbox`` created it."""
    return sa.Table(
        "signal_inbox_items",
        sa.MetaData(),
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
            name="ck_signal_inbox_items_status",
        ),
        sa.CheckConstraint(
            "(status = 'flagged' AND flag_reason IN ('organization_unknown','segment_unknown',"
            "'parse_failed','organization_suppressed')) OR (status <> 'flagged' AND flag_reason "
            "IS NULL)",
            name="ck_signal_inbox_items_flag_reason",
        ),
        sa.CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL AND lead_id IS NOT NULL) OR "
            "(status <> 'processed' AND processed_at IS NULL AND lead_id IS NULL)",
            name="ck_signal_inbox_items_processed",
        ),
        sa.Index("ix_signal_inbox_items_status_submitted_at", "status", "submitted_at"),
        sa.Index("ix_signal_inbox_items_lead_id", "lead_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "signal_inbox_items", recreate="always", copy_from=_items_0054()
    ) as batch:
        batch.add_column(
            sa.Column(
                "draft_when_ready", sa.Boolean(), server_default=sa.text("0"), nullable=False
            )
        )
        batch.add_column(sa.Column("draft_outcome", sa.String(32), nullable=True))
        batch.add_column(sa.Column("draft_decided_at", sa.String(27), nullable=True))
        batch.create_check_constraint(
            op.f("ck_signal_inbox_items_draft_decided"), DRAFT_DECIDED_CHECK
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
