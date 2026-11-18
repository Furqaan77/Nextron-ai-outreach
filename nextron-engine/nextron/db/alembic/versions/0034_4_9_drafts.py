"""Story 4.9: the ``drafts`` table (AD-3, AD-4, AD-6, AD-21), its content group owned by
``drafting`` and its status group by ``approval``.

Creates ``drafts``: a ULID ``id``; the content group ``kind`` (``step``, ``recontact``,
``reply``, ``rebook`` or ``channel``), ``enrollment_id`` (a foreign key to ``enrollments.id``,
no cascade, nullable), ``step_no`` (at least 1), ``day_offset`` (the Step's Sequence day,
snapshotted when the slot is requested; at least 0), ``current_rev`` (at least 1, NULL until a
revision exists), ``due_at``, ``generic`` and ``stale`` (server default 0), ``created_at`` and
``updated_at``; the status group ``status`` (the Draft machine's values, server default
``ready``), ``reapproval_required`` and ``event_expired`` (server default 0), ``skipped_at``
and ``status_at``. A ``step`` or ``recontact`` Draft has its ``enrollment_id``, ``step_no`` and
``day_offset`` (``ck_drafts_step_slot``). ``uq_drafts_enrollment_step`` (partial, ``kind IN
('step','recontact')``): one slot per Step of an Enrollment. Indexes on ``due_at`` and
``enrollment_id``.

Revision ID: 0034_4_9_drafts
Revises: 0033_4_8_enrollments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_4_9_drafts"
down_revision: str | None = "0033_4_8_enrollments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("step", "recontact", "reply", "rebook", "channel")
STATUSES = ("ready", "approved", "sent", "rejected", "superseded", "cancelled")
STEP_KINDS = "kind IN ('step','recontact')"


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def _flag(name: str) -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text("0"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enrollment_id", sa.String(26), nullable=True),
        sa.Column("step_no", sa.Integer(), nullable=True),
        sa.Column("day_offset", sa.Integer(), nullable=True),
        sa.Column("current_rev", sa.Integer(), nullable=True),
        sa.Column("due_at", sa.String(27), nullable=True),
        _flag("generic"),
        _flag("stale"),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'ready'"), nullable=False),
        _flag("reapproval_required"),
        _flag("event_expired"),
        sa.Column("skipped_at", sa.String(27), nullable=True),
        sa.Column("status_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_drafts"),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"], name="fk_drafts_enrollment_id_enrollments"
        ),
        sa.CheckConstraint(_one_of("kind", KINDS), name=op.f("ck_drafts_kind")),
        sa.CheckConstraint(_one_of("status", STATUSES), name=op.f("ck_drafts_status")),
        sa.CheckConstraint(
            f"NOT ({STEP_KINDS}) OR (enrollment_id IS NOT NULL AND step_no IS NOT NULL "
            "AND day_offset IS NOT NULL)",
            name=op.f("ck_drafts_step_slot"),
        ),
        sa.CheckConstraint("step_no IS NULL OR step_no >= 1", name=op.f("ck_drafts_step_no")),
        sa.CheckConstraint(
            "day_offset IS NULL OR day_offset >= 0", name=op.f("ck_drafts_day_offset")
        ),
        sa.CheckConstraint(
            "current_rev IS NULL OR current_rev >= 1", name=op.f("ck_drafts_current_rev")
        ),
    )
    op.create_index(
        "uq_drafts_enrollment_step",
        "drafts",
        ["enrollment_id", "step_no"],
        unique=True,
        sqlite_where=sa.text(STEP_KINDS),
    )
    op.create_index("ix_drafts_due_at", "drafts", ["due_at"])
    op.create_index("ix_drafts_enrollment_id", "drafts", ["enrollment_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
