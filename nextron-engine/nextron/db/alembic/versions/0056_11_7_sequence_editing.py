"""Story 11.7: the ``sequence_versions`` table (FR-4, AD-4, AD-12), owned by ``setup``, and the
Enrollment's bound version (owned by ``sequencing``).

Creates ``sequence_versions``, one immutable row per version of a Sequence: a ULID ``id``, the
``sequence_id`` of its Sequence (``fk_sequence_versions_sequence_id_sequences``, a foreign key
with no cascade), the ``version`` (``ck_sequence_versions_version``: at least 1; unique per
Sequence, ``uq_sequence_versions_sequence_id_version``), the ``definition`` (the canonical JSON
of ``nextron.domain.sequences.sequence_form`` for that version: ``json.dumps`` with
``sort_keys``, ``ensure_ascii=False`` and no spaces), ``created_at`` and the ``actor`` who made
it (``ck_sequence_versions_actor``: ``seed`` or ``operator``). Every column is NOT NULL.

Three triggers, as ``segment_versions`` has (migration 0055):
``trg_sequence_versions_no_delete`` aborts any ``DELETE``; ``trg_sequence_versions_no_id_reuse``
aborts an ``INSERT`` whose ``id``, or whose (``sequence_id``, ``version``), already exists,
which stops ``INSERT OR REPLACE``; and ``trg_sequence_versions_no_update`` aborts every
``UPDATE``.

The backfill inserts one row per existing Sequence at its current version, its definition built
from its ``step_briefs`` rows in Step order, ``created_at`` = the Sequence's ``updated_at`` and
actor ``seed`` (before this story only ``nextron seed sync`` wrote Sequences). Earlier versions
were never kept, so they have no row.

Adds to ``enrollments`` the nullable ``sequence_version_id``
(``fk_enrollments_sequence_version_id_sequence_versions``, no cascade) with ``ALTER TABLE ...
ADD COLUMN`` (SQLite adds it in place, so the table's triggers and partial index stay), and
backfills it for each Enrollment whose (``sequence_id``, ``sequence_version``) has a row. An
Enrollment at a version that was never kept stays NULL. Then ``trg_enrollments_bound`` is
dropped and created again with ``sequence_version_id`` among the bound columns.

Revision ID: 0056_11_7_sequence_editing
Revises: 0055_11_5_segment_editing
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from ulid import ULID

revision: str = "0056_11_7_sequence_editing"
down_revision: str | None = "0055_11_5_segment_editing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE = "sequence_versions rows are immutable"
BACKFILL_ACTOR = "seed"
BOUND = (
    "id",
    "contact_id",
    "campaign_id",
    "variant_id",
    "sending_inbox_id",
    "sequence_id",
    "sequence_version",
    "offer_id",
    "origin",
    "created_at",
    "sequence_version_id",
)
BOUND_MESSAGE = "the bound columns of an Enrollment never change"
_SCALARS = ("delay_days", "purpose", "word_limit", "word_comparison", "ask", "link_rule")
_FLAGS = ("no_price", "no_audit")


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _definition(key: str, name: str, briefs: Sequence[sa.Row]) -> str:  # type: ignore[type-arg]
    """The canonical ``sequence_form`` JSON of one Sequence from its brief rows in Step order."""
    steps = []
    for brief in briefs:
        form: dict[str, object] = {column: getattr(brief, column) for column in _SCALARS}
        for column in _FLAGS:
            form[column] = bool(getattr(brief, column))
        form["allowed_content"] = json.loads(brief.allowed_content)
        if brief.subject_rule is not None:
            form["subject_rule"] = json.loads(brief.subject_rule)
        angles = json.loads(brief.segment_angles)
        if angles:
            form["segment_angles"] = angles
        steps.append(form)
    return _json_text({"key": key, "name": name, "steps": steps})


def upgrade() -> None:
    op.create_table(
        "sequence_versions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("sequence_id", sa.String(26), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("actor", sa.String(8), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sequence_versions"),
        sa.ForeignKeyConstraint(
            ["sequence_id"], ["sequences.id"], name="fk_sequence_versions_sequence_id_sequences"
        ),
        sa.UniqueConstraint(
            "sequence_id", "version", name="uq_sequence_versions_sequence_id_version"
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_sequence_versions_version")),
        sa.CheckConstraint(
            "actor IN ('seed', 'operator')", name=op.f("ck_sequence_versions_actor")
        ),
    )
    bind = op.get_bind()
    sequences = bind.execute(
        sa.text("SELECT id, key, name, version, updated_at FROM sequences ORDER BY key")
    ).all()
    for sequence_id, key, name, version, updated_at in sequences:
        briefs = bind.execute(
            sa.text(
                "SELECT delay_days, purpose, word_limit, word_comparison, allowed_content, ask, "
                "link_rule, no_price, no_audit, subject_rule, segment_angles FROM step_briefs "
                "WHERE sequence_id = :sequence_id ORDER BY step"
            ),
            {"sequence_id": sequence_id},
        ).all()
        bind.execute(
            sa.text(
                "INSERT INTO sequence_versions "
                "(id, sequence_id, version, definition, created_at, actor) "
                "VALUES (:id, :sequence_id, :version, :definition, :created_at, :actor)"
            ),
            {
                "id": str(ULID()),
                "sequence_id": sequence_id,
                "version": version,
                "definition": _definition(key, name, briefs),
                "created_at": updated_at,
                "actor": BACKFILL_ACTOR,
            },
        )
    op.execute(
        "CREATE TRIGGER trg_sequence_versions_no_delete BEFORE DELETE ON sequence_versions "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_sequence_versions_no_id_reuse BEFORE INSERT ON sequence_versions "
        "WHEN EXISTS (SELECT 1 FROM sequence_versions WHERE id = NEW.id "
        "OR (sequence_id = NEW.sequence_id AND version = NEW.version)) "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_sequence_versions_no_update BEFORE UPDATE ON sequence_versions "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.execute(
        "ALTER TABLE enrollments ADD COLUMN sequence_version_id VARCHAR(26) "
        "CONSTRAINT fk_enrollments_sequence_version_id_sequence_versions "
        "REFERENCES sequence_versions (id)"
    )
    op.execute(
        "UPDATE enrollments SET sequence_version_id = (SELECT v.id FROM sequence_versions v "
        "WHERE v.sequence_id = enrollments.sequence_id "
        "AND v.version = enrollments.sequence_version)"
    )
    op.execute("DROP TRIGGER trg_enrollments_bound")
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in BOUND)
    op.execute(
        f"CREATE TRIGGER trg_enrollments_bound BEFORE UPDATE ON enrollments WHEN {changed} "
        f"BEGIN SELECT RAISE(ABORT, '{BOUND_MESSAGE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
