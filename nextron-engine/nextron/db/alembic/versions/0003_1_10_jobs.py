"""Story 1.10: the ``jobs`` table (AD-10), owned by ``jobqueue``.

Creates ``jobs`` with its checks (statuses, lanes, ``attempts >= 0``, the priority range, and a
lease exactly while ``running``), the partial unique index ``uq_jobs_live_key`` over live keys,
the claim, lease and type indexes, and the partial index ``ix_jobs_running_concurrency``.

Revision ID: 0003_1_10_jobs
Revises: 0002_1_6_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_1_10_jobs"
down_revision: str | None = "0002_1_6_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("lane", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("concurrency_key", sa.String(260), nullable=True),
        sa.Column("run_after", sa.String(27), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.String(27), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.CheckConstraint(
            "status IN ('queued','running','done','dead')", name=op.f("ck_jobs_status")
        ),
        sa.CheckConstraint(
            "lane IN ('realtime','interactive','bulk')", name=op.f("ck_jobs_lane")
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_jobs_attempts")),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name=op.f("ck_jobs_priority")),
        sa.CheckConstraint(
            "(status = 'running') = (lease_until IS NOT NULL)", name=op.f("ck_jobs_lease")
        ),
    )
    op.create_index(
        "uq_jobs_live_key",
        "jobs",
        ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "lane", "priority", "run_after"])
    op.create_index("ix_jobs_lease", "jobs", ["status", "lease_until"])
    op.create_index("ix_jobs_type_status", "jobs", ["type", "status", "updated_at"])
    op.create_index(
        "ix_jobs_running_concurrency",
        "jobs",
        ["concurrency_key"],
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
