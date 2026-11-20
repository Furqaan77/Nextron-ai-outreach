"""Story 5.4: approve a Draft bound to the exact text shown (FR-26, FR-55, AD-4, AD-6), owned by
``approval``.

Creates ``approvals``, one append-only row per approval: a ULID ``id``; ``draft_id``
(``fk_approvals_draft_id_drafts``, no cascade); ``rev`` (``ck_approvals_rev``: at least 1);
``text_hash`` (``ck_approvals_text_hash``: 64 lowercase hex, the ``domain.render.text_hash`` the
approval binds to); ``approved_by`` (``ck_approvals_approved_by``: ``operator``);
``approved_at``; ``review_ms`` (``ck_approvals_review_ms``: NULL or at least 0) and
``edit_ratio`` (``ck_approvals_edit_ratio``: 0 to 1). Indexes ``ix_approvals_draft_id`` and
``ix_approvals_approved_at``. Three
triggers keep it append-only: ``trg_approvals_no_update`` and ``trg_approvals_no_delete`` abort
any ``UPDATE`` or ``DELETE``, and ``trg_approvals_insert_new`` aborts an ``INSERT`` whose ``id``
exists (so ``INSERT OR REPLACE`` can't replace one). ``outbound_messages.approval_id`` gets no
foreign key (Story 5.4 decision 2: it would mean rebuilding a triggered table).

Creates ``approval_sessions``, one row per Approval Queue session: a ULID ``id``, ``started_at``,
the nullable ``ended_at`` (NULL while open; ``ck_approval_sessions_ended``: not before
``started_at``), ``decisions`` (``ck_approval_sessions_decisions``: at least 0) and
``active_ms`` (``ck_approval_sessions_active_ms``: at least 0). ``uq_approval_sessions_open`` is
a partial unique index on the expression ``(ended_at IS NULL)`` where ``ended_at IS NULL``: at
most one open session (a plain index on the NULL column would not hold, since SQLite's unique
indexes treat NULLs as distinct). It is created in SQL exactly as the model
(``nextron.db.models.approval``) compiles it; SQLAlchemy doesn't reflect an expression index, so
Alembic's drift comparison skips it.

Revision ID: 0049_5_4_approvals
Revises: 0048_5_2_outbound_messages
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_5_4_approvals"
down_revision: str | None = "0048_5_2_outbound_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = "an approval is never changed or removed"
ONE_OPEN = "uq_approval_sessions_open"


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("draft_id", sa.String(26), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(16), nullable=False),
        sa.Column("approved_at", sa.String(27), nullable=False),
        sa.Column("review_ms", sa.Integer(), nullable=True),
        sa.Column("edit_ratio", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], name="fk_approvals_draft_id_drafts"),
        sa.CheckConstraint("rev >= 1", name=op.f("ck_approvals_rev")),
        sa.CheckConstraint(
            "length(text_hash) = 64 AND text_hash NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_approvals_text_hash"),
        ),
        sa.CheckConstraint("approved_by = 'operator'", name=op.f("ck_approvals_approved_by")),
        sa.CheckConstraint(
            "review_ms IS NULL OR review_ms >= 0", name=op.f("ck_approvals_review_ms")
        ),
        sa.CheckConstraint(
            "edit_ratio >= 0 AND edit_ratio <= 1", name=op.f("ck_approvals_edit_ratio")
        ),
    )
    op.create_index("ix_approvals_draft_id", "approvals", ["draft_id"])
    op.create_index("ix_approvals_approved_at", "approvals", ["approved_at"])
    op.execute(
        "CREATE TRIGGER trg_approvals_no_update BEFORE UPDATE ON approvals "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_approvals_no_delete BEFORE DELETE ON approvals "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_approvals_insert_new BEFORE INSERT ON approvals "
        "WHEN EXISTS (SELECT 1 FROM approvals WHERE id = NEW.id) "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    op.create_table(
        "approval_sessions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("ended_at", sa.String(27), nullable=True),
        sa.Column("decisions", sa.Integer(), nullable=False),
        sa.Column("active_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_approval_sessions"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name=op.f("ck_approval_sessions_ended")
        ),
        sa.CheckConstraint("decisions >= 0", name=op.f("ck_approval_sessions_decisions")),
        sa.CheckConstraint("active_ms >= 0", name=op.f("ck_approval_sessions_active_ms")),
    )
    op.execute(
        f"CREATE UNIQUE INDEX {ONE_OPEN} ON approval_sessions ((ended_at IS NULL)) "
        "WHERE ended_at IS NULL"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
