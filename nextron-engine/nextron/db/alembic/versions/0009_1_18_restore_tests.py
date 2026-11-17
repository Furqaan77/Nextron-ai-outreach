"""Story 1.18: the ``restore_tests`` table (AD-20, FR-58, OP-7, NFR-16), owned by ``ops``.

Creates ``restore_tests``: one row per restore test of an off-PC copy (``kind``, ``result``,
``started_at``, ``finished_at``, the tested ``backup_id``, each check's outcome in
``check_fetch``, ``check_integrity``, ``check_revision``, ``check_suppression`` and
``check_provenance``, the ``reason``, the copy's ``copy_revision``, ``suppression_count`` and
``provenance_count``, and the ``detail``), with ``ck_restore_tests_kind``,
``ck_restore_tests_result``, one ``ck_restore_tests_check_<name>`` per check (``pass``, ``fail``
or ``skipped``), ``ck_restore_tests_outcome`` (a pass has no reason and no detail, a
``backup_id``, ``pass`` for fetch, integrity and revision and ``pass`` or ``skipped`` for both
counts; a fail has a reason and at least one failing check), ``ck_restore_tests_counts`` (no
negative count) and ``ix_restore_tests_started_at``.

Revision ID: 0009_1_18_restore_tests
Revises: 0008_1_17_offpc
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_1_18_restore_tests"
down_revision: str | None = "0008_1_17_offpc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK_VALUES = "IN ('pass','fail','skipped')"
CHECKS = ("fetch", "integrity", "revision", "suppression", "provenance")
OUTCOME_CHECK = (
    "(result = 'pass' AND reason IS NULL AND detail IS NULL AND backup_id IS NOT NULL "
    "AND check_fetch = 'pass' AND check_integrity = 'pass' AND check_revision = 'pass' "
    "AND check_suppression IN ('pass','skipped') AND check_provenance IN ('pass','skipped')) "
    "OR (result = 'fail' AND reason IS NOT NULL AND 'fail' IN (check_fetch, check_integrity, "
    "check_revision, check_suppression, check_provenance))"
)


def upgrade() -> None:
    op.create_table(
        "restore_tests",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=False),
        sa.Column("backup_id", sa.String(26), nullable=True),
        *(sa.Column(f"check_{name}", sa.String(16), nullable=False) for name in CHECKS),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("copy_revision", sa.String(64), nullable=True),
        sa.Column("suppression_count", sa.Integer(), nullable=True),
        sa.Column("provenance_count", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_restore_tests"),
        sa.CheckConstraint("kind IN ('monthly','manual')", name=op.f("ck_restore_tests_kind")),
        sa.CheckConstraint("result IN ('pass','fail')", name=op.f("ck_restore_tests_result")),
        *(
            sa.CheckConstraint(
                f"check_{name} {CHECK_VALUES}", name=op.f(f"ck_restore_tests_check_{name}")
            )
            for name in CHECKS
        ),
        sa.CheckConstraint(OUTCOME_CHECK, name=op.f("ck_restore_tests_outcome")),
        sa.CheckConstraint(
            "suppression_count >= 0 AND provenance_count >= 0",
            name=op.f("ck_restore_tests_counts"),
        ),
    )
    op.create_index("ix_restore_tests_started_at", "restore_tests", ["started_at"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
