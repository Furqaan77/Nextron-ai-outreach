"""Story 5.6: edit a Draft, then approve it once the recheck passes (FR-27, AD-4, AD-6), the
``drafts`` status group (owned by ``approval``).

Rebuilds ``drafts`` (``batch_alter_table(recreate="always")`` from its exact table as of
``0045_4_12_draft_revisions``, given as ``copy_from``, so nothing depends on reflection) to add
three nullable columns, the pending approval of an Operator's edit:

- ``pending_approval_rev`` (``ck_drafts_pending_approval_rev``: NULL or at least 1), the
  revision the edit made;
- ``pending_approval_hash`` (``ck_drafts_pending_approval_hash``: NULL or 64 lowercase hex), the
  ``shown_hash`` of that revision's composed message at the edit;
- ``pending_review_ms`` (``ck_drafts_pending_review_ms``: NULL or at least 0), the review time
  measured at the edit.

Every row, column, check, index and foreign key of ``drafts`` is kept. ``env.py`` runs with
``foreign_keys`` off, so the tables that reference ``drafts`` keep their rows.

Revision ID: 0050_5_6_pending_approval
Revises: 0049_5_4_approvals
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_5_6_pending_approval"
down_revision: str | None = "0049_5_4_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("step", "recontact", "reply", "rebook", "channel")
STATUSES = ("ready", "approved", "sent", "rejected", "superseded", "cancelled")
STEP_KINDS = "kind IN ('step','recontact')"


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def _flag(name: str) -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text("0"), nullable=False)


def _drafts_0045() -> sa.Table:
    """``drafts`` exactly as ``0045_4_12_draft_revisions`` left it."""
    return sa.Table(
        "drafts",
        sa.MetaData(),
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
        sa.ForeignKeyConstraint(
            ["id", "current_rev"],
            ["draft_revisions.draft_id", "draft_revisions.rev"],
            name="fk_drafts_id_current_rev_draft_revisions",
        ),
        sa.CheckConstraint(_one_of("kind", KINDS), name="ck_drafts_kind"),
        sa.CheckConstraint(_one_of("status", STATUSES), name="ck_drafts_status"),
        sa.CheckConstraint(
            f"NOT ({STEP_KINDS}) OR (enrollment_id IS NOT NULL AND step_no IS NOT NULL "
            "AND day_offset IS NOT NULL)",
            name="ck_drafts_step_slot",
        ),
        sa.CheckConstraint("step_no IS NULL OR step_no >= 1", name="ck_drafts_step_no"),
        sa.CheckConstraint("day_offset IS NULL OR day_offset >= 0", name="ck_drafts_day_offset"),
        sa.CheckConstraint(
            "current_rev IS NULL OR current_rev >= 1", name="ck_drafts_current_rev"
        ),
        sa.Index(
            "uq_drafts_enrollment_step",
            "enrollment_id",
            "step_no",
            unique=True,
            sqlite_where=sa.text(STEP_KINDS),
        ),
        sa.Index("ix_drafts_due_at", "due_at"),
        sa.Index("ix_drafts_enrollment_id", "enrollment_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table("drafts", recreate="always", copy_from=_drafts_0045()) as batch:
        batch.add_column(sa.Column("pending_approval_rev", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pending_approval_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pending_review_ms", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_drafts_pending_approval_rev"),
            "pending_approval_rev IS NULL OR pending_approval_rev >= 1",
        )
        batch.create_check_constraint(
            op.f("ck_drafts_pending_approval_hash"),
            "pending_approval_hash IS NULL OR (length(pending_approval_hash) = 64 AND "
            "pending_approval_hash NOT GLOB '*[^0-9a-f]*')",
        )
        batch.create_check_constraint(
            op.f("ck_drafts_pending_review_ms"),
            "pending_review_ms IS NULL OR pending_review_ms >= 0",
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
