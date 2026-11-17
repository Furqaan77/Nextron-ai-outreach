"""Story 1.12: the ``alerts`` and ``notifications`` tables (AD-4, AD-15, UX-DR5), owned by
``notify``.

Creates ``alerts``: one row per Telegram alert, unique on kind, subject and channel
(``uq_alerts_kind_subject_channel``), with its status in the Alert machine
(``ck_alerts_status``), Telegram's message id, the last error code and the resend time. Creates
``notifications``: the dashboard's notices, unique on kind and subject
(``uq_notifications_kind_subject``), with ``ix_notifications_open`` on ``dismissed_at`` and
``created_at``.

Revision ID: 0005_1_12_notify
Revises: 0004_1_11_heartbeat
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_1_12_notify"
down_revision: str | None = "0004_1_11_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("resent_at", sa.String(27), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.UniqueConstraint(
            "kind", "subject", "channel", name="uq_alerts_kind_subject_channel"
        ),
        sa.CheckConstraint(
            "status IN ('pending','sent','unknown','failed')", name=op.f("ck_alerts_status")
        ),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("dismissed_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.UniqueConstraint("kind", "subject", name="uq_notifications_kind_subject"),
    )
    op.create_index(
        "ix_notifications_open", "notifications", ["dismissed_at", "created_at"]
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
