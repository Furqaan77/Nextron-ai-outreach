"""Story 4.17: the Message Batch tables (AD-8, INT-7, FR-57), shared by row owner (``drafting``
or ``research``) and written only by ``nextron.db.ai_batches``. Neither holds personal data.

Creates ``ai_batches``: a ULID ``id``; ``owner`` (``ck_ai_batches_owner``); the unique
``provider_batch_id`` (``uq_ai_batches_provider_batch_id``); ``route`` and ``route_tuple_key``;
``status`` (``ck_ai_batches_status``: ``submitted``, ``ended`` or ``deleted``);
``submitted_at``, ``ended_at`` and ``deleted_at`` (``ck_ai_batches_times``); and
``ix_ai_batches_owner_status``.

Creates ``ai_batch_items``: a ULID ``id``; ``owner`` (``ck_ai_batch_items_owner``); the
nullable ``batch_row_id`` (``fk_ai_batch_items_batch_row_id_ai_batches``); ``route``,
``custom_id``, ``target_id`` and ``rev`` (``ck_ai_batch_items_rev``: 0 or more); the nullable
``context_id`` and ``fingerprint``; ``status`` (``ck_ai_batch_items_status``: ``pending``,
``submitted``, ``applied``, ``stale``, ``rerun`` or ``failed``) with ``ck_ai_batch_items_batch``
(no batch while ``pending``; a batch and a fingerprint once ``submitted``); ``created_at`` and
``updated_at``; ``uq_ai_batch_items_owner_route_target_id_rev``,
``uq_ai_batch_items_batch_row_id_custom_id``, ``ix_ai_batch_items_owner_status`` and
``ix_ai_batch_items_batch_row_id``.

Revision ID: 0049_4_17_ai_batches
Revises: 0048_4_15_draft_failed
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_4_17_ai_batches"
down_revision: str | None = "0048_4_15_draft_failed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNERS = ("drafting", "research")
BATCH_STATUSES = ("submitted", "ended", "deleted")
ITEM_STATUSES = ("pending", "submitted", "applied", "stale", "rerun", "failed")
BATCH_TIMES_CHECK = (
    "(status = 'submitted' OR ended_at IS NOT NULL) "
    "AND ((status = 'deleted') = (deleted_at IS NOT NULL))"
)
ITEM_BATCH_CHECK = (
    "(status <> 'pending' OR batch_row_id IS NULL) "
    "AND (status <> 'submitted' OR (batch_row_id IS NOT NULL AND fingerprint IS NOT NULL))"
)


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.create_table(
        "ai_batches",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("owner", sa.String(16), nullable=False),
        sa.Column("provider_batch_id", sa.String(128), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("route_tuple_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("submitted_at", sa.String(27), nullable=False),
        sa.Column("ended_at", sa.String(27), nullable=True),
        sa.Column("deleted_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ai_batches"),
        sa.UniqueConstraint("provider_batch_id", name="uq_ai_batches_provider_batch_id"),
        sa.CheckConstraint(_one_of("owner", OWNERS), name=op.f("ck_ai_batches_owner")),
        sa.CheckConstraint(_one_of("status", BATCH_STATUSES), name=op.f("ck_ai_batches_status")),
        sa.CheckConstraint(BATCH_TIMES_CHECK, name=op.f("ck_ai_batches_times")),
    )
    op.create_index("ix_ai_batches_owner_status", "ai_batches", ["owner", "status"])
    op.create_table(
        "ai_batch_items",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("owner", sa.String(16), nullable=False),
        sa.Column("batch_row_id", sa.String(26), nullable=True),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("custom_id", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(26), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("context_id", sa.String(26), nullable=True),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_batch_items"),
        sa.ForeignKeyConstraint(
            ["batch_row_id"], ["ai_batches.id"], name="fk_ai_batch_items_batch_row_id_ai_batches"
        ),
        sa.UniqueConstraint(
            "owner", "route", "target_id", "rev", name="uq_ai_batch_items_owner_route_target_id_rev"
        ),
        sa.UniqueConstraint(
            "batch_row_id", "custom_id", name="uq_ai_batch_items_batch_row_id_custom_id"
        ),
        sa.CheckConstraint(_one_of("owner", OWNERS), name=op.f("ck_ai_batch_items_owner")),
        sa.CheckConstraint(
            _one_of("status", ITEM_STATUSES), name=op.f("ck_ai_batch_items_status")
        ),
        sa.CheckConstraint("rev >= 0", name=op.f("ck_ai_batch_items_rev")),
        sa.CheckConstraint(ITEM_BATCH_CHECK, name=op.f("ck_ai_batch_items_batch")),
    )
    op.create_index("ix_ai_batch_items_owner_status", "ai_batch_items", ["owner", "status"])
    op.create_index("ix_ai_batch_items_batch_row_id", "ai_batch_items", ["batch_row_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
