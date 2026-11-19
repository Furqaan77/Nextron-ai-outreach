"""Story 4.15: why a Step's drafting stored no revision (FR-20, AD-8, AD-18), part of the
``drafts`` content group owned by ``drafting``.

Rebuilds ``drafts`` (``batch_alter_table(recreate="always")`` from 0045's exact table, given as
``copy_from``, so nothing depends on reflection) with:

- ``draft_failed_at`` (UTC text, 27 characters, nullable; PII class ``none``): when the last
  drafting attempt failed, NULL when it didn't or a revision was stored;
- ``draft_failed_reason`` (at most 32 characters, nullable; PII class ``none``): its
  ``nextron.domain.step_drafting.DraftFailure`` code;
- ``ck_drafts_draft_failed``: both NULL or both set, and the reason one of the codes.

Every row, column, check, index and foreign key of ``drafts`` is kept. ``env.py`` runs with
``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0064_4_15_draft_failed
Revises: 0063_6_11_inbox_pacing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_4_15_draft_failed"
down_revision: str | None = "0063_6_11_inbox_pacing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("step", "recontact", "reply", "rebook", "channel")
STATUSES = ("ready", "approved", "sent", "rejected", "superseded", "cancelled")
STEP_KINDS = "kind IN ('step','recontact')"
REASONS = (
    "no_route",
    "refused",
    "truncated",
    "invalid",
    "failed",
    "invalid_proposal",
    "not_composable",
)


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


DRAFT_FAILED_CHECK = (
    "(draft_failed_at IS NULL) = (draft_failed_reason IS NULL) AND "
    f"(draft_failed_reason IS NULL OR {_one_of('draft_failed_reason', REASONS)})"
)


def _flag(name: str) -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text("0"), nullable=False)


def _drafts_0045() -> sa.Table:
    """``drafts`` exactly as ``0045_4_12_draft_revisions`` left it (0042's table and its
    revision foreign key)."""
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
        sa.ForeignKeyConstraint(
            ["id", "current_rev"],
            ["draft_revisions.draft_id", "draft_revisions.rev"],
            name="fk_drafts_id_current_rev_draft_revisions",
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
        batch.add_column(sa.Column("draft_failed_at", sa.String(27), nullable=True))
        batch.add_column(sa.Column("draft_failed_reason", sa.String(32), nullable=True))
        batch.create_check_constraint(op.f("ck_drafts_draft_failed"), DRAFT_FAILED_CHECK)


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
