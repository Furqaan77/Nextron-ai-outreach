"""Story 10.7: the Channel Permissions a prospect gave on WhatsApp or Instagram (FR-24, FR-47,
AD-3, AD-7), owned by ``pipeline``.

Creates ``channel_permissions``: a ULID ``id``; ``contact_id`` (FK ``contacts.id``, indexed:
``ix_channel_permissions_contact_id``); ``channel`` (``ck_channel_permissions_channel``:
``whatsapp`` or ``instagram``); ``prospect_action`` (``ck_channel_permissions_action``: one
valid for the channel); ``occurred_at``; ``evidence_id`` (FK ``evidence.id``: the operator
Quote Evidence ``research`` stored); ``whatsapp_number`` (16) and ``instagram_handle`` (30),
nullable (``ck_channel_permissions_identifier``: only the channel's own one, required while
``active``; erasure NULLs it); ``last_inbound_at`` (``ck_channel_permissions_last_inbound``: set
exactly for Instagram, never before ``occurred_at``); ``status``
(``ck_channel_permissions_status``: ``active`` or ``revoked``) with ``revoked_at``
(``ck_channel_permissions_revoked_at``: set exactly when ``revoked``); ``recorded_by``
(``ck_channel_permissions_recorded_by``: ``operator``); ``created_at`` and ``updated_at``. The
partial unique index ``uq_channel_permissions_active`` (``WHERE status = 'active'``) lets a
Contact hold at most one ``active`` permission per channel.

Revision ID: 0038_10_7_channel_permissions
Revises: 0037_10_2_assist_accounts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_10_7_channel_permissions"
down_revision: str | None = "0037_10_2_assist_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_permissions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("prospect_action", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.String(27), nullable=False),
        sa.Column("evidence_id", sa.String(26), nullable=False),
        sa.Column("whatsapp_number", sa.String(16), nullable=True),
        sa.Column("instagram_handle", sa.String(30), nullable=True),
        sa.Column("last_inbound_at", sa.String(27), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("revoked_at", sa.String(27), nullable=True),
        sa.Column("recorded_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_channel_permissions"),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_channel_permissions_contact_id_contacts"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="fk_channel_permissions_evidence_id_evidence"
        ),
        sa.CheckConstraint(
            "channel IN ('whatsapp','instagram')", name=op.f("ck_channel_permissions_channel")
        ),
        sa.CheckConstraint(
            "(channel = 'whatsapp' AND prospect_action IN "
            "('started_whatsapp_conversation','gave_number_for_whatsapp'))"
            " OR (channel = 'instagram' AND prospect_action IN "
            "('started_instagram_conversation'))",
            name=op.f("ck_channel_permissions_action"),
        ),
        sa.CheckConstraint(
            "(channel = 'whatsapp' AND instagram_handle IS NULL"
            " AND (status = 'revoked' OR whatsapp_number IS NOT NULL))"
            " OR (channel = 'instagram' AND whatsapp_number IS NULL"
            " AND (status = 'revoked' OR instagram_handle IS NOT NULL))",
            name=op.f("ck_channel_permissions_identifier"),
        ),
        sa.CheckConstraint(
            "(channel = 'instagram') = (last_inbound_at IS NOT NULL)"
            " AND (last_inbound_at IS NULL OR last_inbound_at >= occurred_at)",
            name=op.f("ck_channel_permissions_last_inbound"),
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')", name=op.f("ck_channel_permissions_status")
        ),
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name=op.f("ck_channel_permissions_revoked_at"),
        ),
        sa.CheckConstraint(
            "recorded_by = 'operator'", name=op.f("ck_channel_permissions_recorded_by")
        ),
    )
    op.create_index("ix_channel_permissions_contact_id", "channel_permissions", ["contact_id"])
    op.create_index(
        "uq_channel_permissions_active",
        "channel_permissions",
        ["contact_id", "channel"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
