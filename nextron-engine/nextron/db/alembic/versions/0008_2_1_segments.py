"""Story 2.1: the ``segments`` table (FR-1, AD-12), owned by ``setup``.

Creates ``segments``: a ULID ``id``, a unique ``key`` (``uq_segments_key``), the canonical JSON
``definition``, the ``version`` (``ck_segments_version``: at least 1), ``created_at`` and
``updated_at``; every column NOT NULL.

Revision ID: 0008_2_1_segments
Revises: 0007_1_16_backups
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_2_1_segments"
down_revision: str | None = "0007_1_16_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segments",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_segments"),
        sa.UniqueConstraint("key", name="uq_segments_key"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_segments_version")),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
