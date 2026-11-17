"""Story 1.19: the ``recovery_state`` table (AD-2, AD-5, AD-17, AD-20, OP-8), owned by ``ops``.

Creates ``recovery_state``: one row per restore of a backup (``mode``, ``restored_at``,
``backup_at``, the restored file's ``source_name``, the safety copy's ``pre_restore_name``,
``ended_at`` and one column per recovery step: ``reapply_erasure_ledger``,
``mark_oms_unknown``, ``reingest_mailboxes`` and ``repoll_unsubscribes``), with
``ck_recovery_state_mode`` (``on``, ``off`` or ``superseded``), one
``ck_recovery_state_<step>`` per step (``pending``, ``done`` or ``not_applicable``),
``ck_recovery_state_outcome`` (``on`` has no ``ended_at``; ``superseded`` has one; ``off`` has
one and every step ``done`` or ``not_applicable``), the partial unique index
``uq_recovery_state_on`` (at most one ``on`` row) and ``ix_recovery_state_restored_at``.

Revision ID: 0010_1_19_recovery
Revises: 0009_1_18_restore_tests
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_1_19_recovery"
down_revision: str | None = "0009_1_18_restore_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STEPS = (
    "reapply_erasure_ledger",
    "mark_oms_unknown",
    "reingest_mailboxes",
    "repoll_unsubscribes",
)
STEP_VALUES = "IN ('pending','done','not_applicable')"
OUTCOME_CHECK = (
    "(mode = 'on' AND ended_at IS NULL) "
    "OR (mode = 'superseded' AND ended_at IS NOT NULL) "
    "OR (mode = 'off' AND ended_at IS NOT NULL "
    "AND reapply_erasure_ledger IN ('done','not_applicable') "
    "AND mark_oms_unknown IN ('done','not_applicable') "
    "AND reingest_mailboxes IN ('done','not_applicable') "
    "AND repoll_unsubscribes IN ('done','not_applicable'))"
)


def upgrade() -> None:
    op.create_table(
        "recovery_state",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("restored_at", sa.String(27), nullable=False),
        sa.Column("backup_at", sa.String(27), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("pre_restore_name", sa.String(255), nullable=True),
        sa.Column("ended_at", sa.String(27), nullable=True),
        *(sa.Column(step, sa.String(16), nullable=False) for step in STEPS),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_state"),
        sa.CheckConstraint(
            "mode IN ('on','off','superseded')", name=op.f("ck_recovery_state_mode")
        ),
        *(
            sa.CheckConstraint(f"{step} {STEP_VALUES}", name=op.f(f"ck_recovery_state_{step}"))
            for step in STEPS
        ),
        sa.CheckConstraint(OUTCOME_CHECK, name=op.f("ck_recovery_state_outcome")),
    )
    op.create_index(
        "uq_recovery_state_on",
        "recovery_state",
        ["mode"],
        unique=True,
        sqlite_where=sa.text("mode = 'on'"),
    )
    op.create_index("ix_recovery_state_restored_at", "recovery_state", ["restored_at"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
