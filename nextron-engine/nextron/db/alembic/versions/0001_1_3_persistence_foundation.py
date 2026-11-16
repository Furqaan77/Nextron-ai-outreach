"""Story 1.3: the append-only ``events`` table (AD-4).

Creates ``events``, its indexes on ``(entity, entity_id)`` and ``at``, and three triggers that
abort an ``UPDATE``, a ``DELETE`` and an ``INSERT`` reusing an existing ``id`` (which stops
``INSERT OR REPLACE``).

Revision ID: 0001_1_3_persistence_foundation
Revises: none (the first revision)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_1_3_persistence_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = "events is append-only"


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(26), nullable=False),
        sa.Column("from_status", sa.String(64), nullable=True),
        sa.Column("to_status", sa.String(64), nullable=False),
        sa.Column("cause", sa.String(64), nullable=False),
        sa.Column("cause_ref", sa.String(26), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )
    op.create_index("ix_events_entity_entity_id", "events", ["entity", "entity_id"])
    op.create_index("ix_events_at", "events", ["at"])
    op.execute(
        "CREATE TRIGGER trg_events_no_update BEFORE UPDATE ON events "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_events_no_delete BEFORE DELETE ON events "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_events_no_id_reuse BEFORE INSERT ON events "
        "WHEN EXISTS (SELECT 1 FROM events WHERE id = NEW.id) "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
