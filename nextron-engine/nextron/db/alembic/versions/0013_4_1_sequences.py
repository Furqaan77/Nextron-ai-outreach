"""Story 4.1: the ``sequences`` and ``step_briefs`` tables (FR-4, AD-12), owned by ``setup``.

Creates ``sequences``: a ULID ``id``, a unique ``key`` (``uq_sequences_key``), the ``name``, the
``version`` (``ck_sequences_version``: at least 1), ``created_at`` and ``updated_at``.

Creates ``step_briefs``: a ULID ``id``, the ``sequence_id`` of its Sequence (a foreign key to
``sequences.id``, ``fk_step_briefs_sequence_id_sequences``, no cascade), the 1-based ``step``
(``ck_step_briefs_step``: 1 to 6; unique per Sequence, ``uq_step_briefs_sequence_id_step``),
``delay_days`` (``ck_step_briefs_delay_days``: at least 0), ``purpose``, ``word_limit``
(``ck_step_briefs_word_limit``: 1 to 150), ``word_comparison``, ``allowed_content`` (a canonical
JSON list), ``ask``, ``link_rule``, the ``no_price`` and ``no_audit`` flags, ``subject_rule`` (a
canonical JSON object, NULL when the Step has none), ``segment_angles`` (a canonical JSON list,
``[]`` for none), ``created_at`` and ``updated_at``. Every column but ``subject_rule`` is NOT
NULL.

Revision ID: 0013_4_1_sequences
Revises: 0012_1_18_restore_tests
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_4_1_sequences"
down_revision: str | None = "0012_1_18_restore_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sequences",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sequences"),
        sa.UniqueConstraint("key", name="uq_sequences_key"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_sequences_version")),
    )
    op.create_table(
        "step_briefs",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("sequence_id", sa.String(26), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("delay_days", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(200), nullable=False),
        sa.Column("word_limit", sa.Integer(), nullable=False),
        sa.Column("word_comparison", sa.String(8), nullable=False),
        sa.Column("allowed_content", sa.Text(), nullable=False),
        sa.Column("ask", sa.String(200), nullable=False),
        sa.Column("link_rule", sa.String(16), nullable=False),
        sa.Column("no_price", sa.Boolean(), nullable=False),
        sa.Column("no_audit", sa.Boolean(), nullable=False),
        sa.Column("subject_rule", sa.Text(), nullable=True),
        sa.Column("segment_angles", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_step_briefs"),
        sa.ForeignKeyConstraint(
            ["sequence_id"], ["sequences.id"], name="fk_step_briefs_sequence_id_sequences"
        ),
        sa.UniqueConstraint("sequence_id", "step", name="uq_step_briefs_sequence_id_step"),
        sa.CheckConstraint("step BETWEEN 1 AND 6", name=op.f("ck_step_briefs_step")),
        sa.CheckConstraint("delay_days >= 0", name=op.f("ck_step_briefs_delay_days")),
        sa.CheckConstraint(
            "word_limit BETWEEN 1 AND 150", name=op.f("ck_step_briefs_word_limit")
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
