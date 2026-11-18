"""Story 4.10: the ``daily_capacity`` table (FR-19 S1, FR-59, UX-DR20), owned by ``scoring``.

Creates ``daily_capacity``: a ULID ``id``; ``local_date`` (a date in the ``sender_timezone``,
``uq_daily_capacity_local_date``); ``total_cold_capacity``, ``follow_ups_reserved`` and
``new_sequence_capacity``; ``picked`` (server default 0); ``signal_shortfall`` (NULL until
finalised) and ``computed_at``. ``ck_daily_capacity_counts``: no count is negative.

Revision ID: 0052_4_10_daily_capacity
Revises: 0051_10_7_channel_permissions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_4_10_daily_capacity"
down_revision: str | None = "0051_10_7_channel_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_capacity",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("total_cold_capacity", sa.Integer(), nullable=False),
        sa.Column("follow_ups_reserved", sa.Integer(), nullable=False),
        sa.Column("new_sequence_capacity", sa.Integer(), nullable=False),
        sa.Column("picked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("signal_shortfall", sa.Integer(), nullable=True),
        sa.Column("computed_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_capacity"),
        sa.UniqueConstraint("local_date", name="uq_daily_capacity_local_date"),
        sa.CheckConstraint(
            "total_cold_capacity >= 0 AND follow_ups_reserved >= 0 "
            "AND new_sequence_capacity >= 0 AND picked >= 0 "
            "AND (signal_shortfall IS NULL OR signal_shortfall >= 0)",
            name=op.f("ck_daily_capacity_counts"),
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
