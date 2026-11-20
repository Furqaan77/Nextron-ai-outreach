"""Story 6.2: each Sending Inbox's Gmail connection times (FR-32, AD-4, AD-12), in
``sending_inboxes``, owned by ``sending``.

Adds two columns, both PII class ``none``:

- ``status_changed_at`` (27 characters, UTC text, NOT NULL): when the inbox's status last
  changed; every existing row is backfilled from its ``created_at``;
- ``connected_at`` (27 characters, UTC text, nullable): when the inbox was last connected (or
  reconnected) through Gmail OAuth; NULL until then.

The steps: both columns are added in place (``add_column``, no rebuild), every row's
``status_changed_at`` is set to its ``created_at``, then ``sending_inboxes`` is rebuilt
(``batch_alter_table(recreate="always")`` from 0046's exact table plus the two new columns,
given as ``copy_from`` with its unique index, so nothing depends on reflection) to make
``status_changed_at`` NOT NULL. Every check, the foreign key to ``sending_domains``,
``uq_sending_inboxes_address`` and every row are kept. ``env.py`` runs with ``foreign_keys``
off, so the rebuild is safe. No token, address or label is added: the refresh token lives in
keyring as ``gmail/<address>``.

Revision ID: 0048_6_2_inbox_connect
Revises: 0047_6_6_reputation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_6_2_inbox_connect"
down_revision: str | None = "0047_6_6_reputation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WARMUP_RECORD_CHECK = (
    "(warmup_started_on IS NULL AND warmup_method IS NULL AND warm_level IS NULL) OR "
    "(warmup_started_on IS NOT NULL AND warmup_method IS NOT NULL AND warm_level IS NOT NULL)"
)
WARMUP_RANGES_CHECK = (
    "(warmup_method IS NULL OR length(warmup_method) BETWEEN 1 AND 40) AND "
    "(warm_level IS NULL OR warm_level BETWEEN 0 AND 100) AND "
    "warmup_tool_daily BETWEEN 0 AND 100 AND "
    "ramp_step BETWEEN 1 AND 10 AND "
    "ramp_target BETWEEN 1 AND 50"
)


def _inboxes_0046_with_times() -> sa.Table:
    """``sending_inboxes`` as 0046 left it (0047 leaves it unchanged), plus this migration's two
    columns while ``status_changed_at`` is still nullable; its unique index included."""
    return sa.Table(
        "sending_inboxes",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("address", sa.String(254), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("sending_domain_id", sa.String(26), nullable=False),
        sa.Column("from_name", sa.String(100), nullable=False),
        sa.Column(
            "status", sa.String(16), server_default=sa.text("'connecting'"), nullable=False
        ),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("warmup_started_on", sa.Date(), nullable=True),
        sa.Column("warmup_method", sa.String(40), nullable=True),
        sa.Column("warm_level", sa.Integer(), nullable=True),
        sa.Column("warmup_tool_daily", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ramp_step", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("ramp_target", sa.Integer(), server_default=sa.text("30"), nullable=False),
        sa.Column("next_send_at", sa.String(27), nullable=True),
        sa.Column("last_sent_at", sa.String(27), nullable=True),
        sa.Column("status_changed_at", sa.String(27), nullable=True),
        sa.Column("connected_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sending_inboxes"),
        sa.ForeignKeyConstraint(
            ["sending_domain_id"],
            ["sending_domains.id"],
            name="fk_sending_inboxes_sending_domain_id_sending_domains",
        ),
        sa.CheckConstraint(
            "status IN ('connecting','active','paused','auth_failed','removed')",
            name="ck_sending_inboxes_status",
        ),
        sa.CheckConstraint(
            "address NOT LIKE '%@nextronai.com' AND address NOT LIKE '%.nextronai.com'",
            name="ck_sending_inboxes_not_brand",
        ),
        sa.CheckConstraint(WARMUP_RECORD_CHECK, name="ck_sending_inboxes_warmup_record"),
        sa.CheckConstraint(WARMUP_RANGES_CHECK, name="ck_sending_inboxes_warmup_ranges"),
        sa.Index("uq_sending_inboxes_address", "address", unique=True),
    )


def upgrade() -> None:
    with op.batch_alter_table("sending_inboxes") as batch:
        batch.add_column(sa.Column("status_changed_at", sa.String(27), nullable=True))
        batch.add_column(sa.Column("connected_at", sa.String(27), nullable=True))
    op.execute("UPDATE sending_inboxes SET status_changed_at = created_at")
    with op.batch_alter_table(
        "sending_inboxes", recreate="always", copy_from=_inboxes_0046_with_times()
    ) as batch:
        batch.alter_column(
            "status_changed_at",
            existing_type=sa.String(27),
            existing_nullable=True,
            nullable=False,
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
