"""Story 11.5: the ``segment_versions`` table (FR-1, AD-4, AD-12), owned by ``setup``.

Creates ``segment_versions``, one immutable row per version of a Segment: a ULID ``id``, the
``segment_id`` of its Segment (``fk_segment_versions_segment_id_segments``, a foreign key with
no cascade), the ``version`` (``ck_segment_versions_version``: at least 1; unique per Segment,
``uq_segment_versions_segment_id_version``), the ``definition`` (the canonical JSON
``nextron.domain.segments.definition_text`` returned for that version), ``created_at`` and the
``actor`` who made it (``ck_segment_versions_actor``: ``seed`` or ``operator``). Every column
is NOT NULL.

Three triggers, as ``company_facts`` has (migration 0017): ``trg_segment_versions_no_delete``
aborts any ``DELETE``; ``trg_segment_versions_no_id_reuse`` aborts an ``INSERT`` whose ``id``,
or whose (``segment_id``, ``version``), already exists, which stops ``INSERT OR REPLACE``; and
``trg_segment_versions_no_update`` aborts every ``UPDATE``.

The backfill inserts one row per existing Segment at its current version, with its current
definition, ``created_at`` = the Segment's ``updated_at`` (when that version was written) and
actor ``seed`` (before this story only ``nextron seed sync`` wrote Segments). Earlier versions
were never kept, so they have no row.

Revision ID: 0055_11_5_segment_editing
Revises: 0054_2_16_signal_inbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from ulid import ULID

revision: str = "0055_11_5_segment_editing"
down_revision: str | None = "0054_2_16_signal_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE = "segment_versions rows are immutable"
BACKFILL_ACTOR = "seed"


def upgrade() -> None:
    op.create_table(
        "segment_versions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("actor", sa.String(8), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_segment_versions"),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_segment_versions_segment_id_segments"
        ),
        sa.UniqueConstraint(
            "segment_id", "version", name="uq_segment_versions_segment_id_version"
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_segment_versions_version")),
        sa.CheckConstraint(
            "actor IN ('seed', 'operator')", name=op.f("ck_segment_versions_actor")
        ),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, version, definition, updated_at FROM segments ORDER BY key")
    ).all()
    for segment_id, version, definition, updated_at in rows:
        bind.execute(
            sa.text(
                "INSERT INTO segment_versions "
                "(id, segment_id, version, definition, created_at, actor) "
                "VALUES (:id, :segment_id, :version, :definition, :created_at, :actor)"
            ),
            {
                "id": str(ULID()),
                "segment_id": segment_id,
                "version": version,
                "definition": definition,
                "created_at": updated_at,
                "actor": BACKFILL_ACTOR,
            },
        )
    op.execute(
        "CREATE TRIGGER trg_segment_versions_no_delete BEFORE DELETE ON segment_versions "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_segment_versions_no_id_reuse BEFORE INSERT ON segment_versions "
        "WHEN EXISTS (SELECT 1 FROM segment_versions WHERE id = NEW.id "
        "OR (segment_id = NEW.segment_id AND version = NEW.version)) "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_segment_versions_no_update BEFORE UPDATE ON segment_versions "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
