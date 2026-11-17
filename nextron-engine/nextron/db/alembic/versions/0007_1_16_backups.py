"""Story 1.16: the ``backups`` table (AD-2, AD-20, FR-58, OP-5), owned by ``ops``.

Creates ``backups``: one row per backup (``kind``, ``status``, ``started_at``, ``finished_at``,
the copy's ``path``, ``size_bytes``, ``sha256``, ``db_revision``, ``suppression_count``,
``provenance_count``, ``error`` and ``local_deleted_at``), with ``ck_backups_kind``,
``ck_backups_status``, ``ck_backups_outcome`` (a verified row has a size, a SHA-256 and a
revision and no error; a failed row has an error, no size, no SHA-256 and no
``local_deleted_at``), ``ck_backups_counts`` (no negative size or count),
``ck_backups_sha256`` (64 characters) and ``ix_backups_started_at``.

Revision ID: 0007_1_16_backups
Revises: 0006_1_13_metering
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_1_16_backups"
down_revision: str | None = "0006_1_13_metering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backups",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("db_revision", sa.String(64), nullable=True),
        sa.Column("suppression_count", sa.Integer(), nullable=True),
        sa.Column("provenance_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("local_deleted_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_backups"),
        sa.CheckConstraint(
            "kind IN ('daily','manual','pre_migrate')", name=op.f("ck_backups_kind")
        ),
        sa.CheckConstraint("status IN ('verified','failed')", name=op.f("ck_backups_status")),
        sa.CheckConstraint(
            "(status = 'verified' AND size_bytes IS NOT NULL AND sha256 IS NOT NULL "
            "AND db_revision IS NOT NULL AND error IS NULL) "
            "OR (status = 'failed' AND error IS NOT NULL AND size_bytes IS NULL "
            "AND sha256 IS NULL AND local_deleted_at IS NULL)",
            name=op.f("ck_backups_outcome"),
        ),
        sa.CheckConstraint(
            "size_bytes >= 0 AND suppression_count >= 0 AND provenance_count >= 0",
            name=op.f("ck_backups_counts"),
        ),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_backups_sha256")),
    )
    op.create_index("ix_backups_started_at", "backups", ["started_at"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
