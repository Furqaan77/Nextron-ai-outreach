"""Story 1.11: the ``runner_heartbeat`` table (AD-20), owned by ``ops``.

Creates ``runner_heartbeat``: one row with the fixed all-zero ULID ``id``
(``ck_runner_heartbeat_singleton``), the engine's ``pid`` (``ck_runner_heartbeat_pid``: above
0), ``started_at`` and ``beat_at``.

Revision ID: 0004_1_11_heartbeat
Revises: 0003_1_10_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_1_11_heartbeat"
down_revision: str | None = "0003_1_10_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_heartbeat",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("beat_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_runner_heartbeat"),
        sa.CheckConstraint(
            "id = '00000000000000000000000000'", name=op.f("ck_runner_heartbeat_singleton")
        ),
        sa.CheckConstraint("pid > 0", name=op.f("ck_runner_heartbeat_pid")),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
