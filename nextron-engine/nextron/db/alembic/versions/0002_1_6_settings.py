"""Story 1.6: the ``settings`` table (AD-12), owned by ``setup``.

Creates ``settings``: a ULID ``id``, a unique ``key``, the canonical text ``value`` and
``updated_at``.

Revision ID: 0002_1_6_settings
Revises: 0001_1_3_persistence_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_1_6_settings"
down_revision: str | None = "0001_1_3_persistence_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_settings"),
        sa.UniqueConstraint("key", name="uq_settings_key"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
