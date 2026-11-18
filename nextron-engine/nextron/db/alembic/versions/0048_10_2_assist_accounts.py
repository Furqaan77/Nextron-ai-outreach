"""Story 10.2: the LinkedIn account a person sends Tasks from by hand, with its owner and caps
(FR-49, PRD OQ-5, AD-3), owned by ``pipeline``.

Creates ``linkedin_accounts``: a ULID ``id``; ``label`` (100, unique:
``uq_linkedin_accounts_label``), ``owner_name`` (100) and ``owner_role`` (100); ``status``
(``ck_linkedin_accounts_status``: ``active`` or ``inactive``); the Integer caps
``tasks_per_weekday``, ``daily_invite_cap`` and ``weekly_invite_cap`` with server defaults 10, 10
and 100 (``ck_linkedin_accounts_caps``: each 0 or more; ``ck_linkedin_accounts_weekly_cap``: weekly
at most 100; ``ck_linkedin_accounts_daily_cap``: daily at most weekly); ``created_at`` and
``updated_at``. The partial unique index ``uq_linkedin_accounts_active`` (``WHERE status =
'active'``) lets at most one account be active (v1 supports one, PRD OQ-5). Every column is NOT
NULL.

The file is named ``assist_accounts``, not after the table: Story 10.1's contract forbids the
platform's name in any module path outside ``nextron.domain``.

Revision ID: 0048_10_2_assist_accounts
Revises: 0047_8_3_discovery_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_10_2_assist_accounts"
down_revision: str | None = "0047_8_3_discovery_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linkedin_accounts",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("owner_name", sa.String(100), nullable=False),
        sa.Column("owner_role", sa.String(100), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column(
            "tasks_per_weekday", sa.Integer(), server_default=sa.text("10"), nullable=False
        ),
        sa.Column("daily_invite_cap", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("weekly_invite_cap", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_linkedin_accounts"),
        sa.CheckConstraint(
            "status IN ('active','inactive')", name=op.f("ck_linkedin_accounts_status")
        ),
        sa.CheckConstraint(
            "tasks_per_weekday >= 0 AND daily_invite_cap >= 0 AND weekly_invite_cap >= 0",
            name=op.f("ck_linkedin_accounts_caps"),
        ),
        sa.CheckConstraint(
            "weekly_invite_cap <= 100", name=op.f("ck_linkedin_accounts_weekly_cap")
        ),
        sa.CheckConstraint(
            "daily_invite_cap <= weekly_invite_cap", name=op.f("ck_linkedin_accounts_daily_cap")
        ),
    )
    op.create_index("uq_linkedin_accounts_label", "linkedin_accounts", ["label"], unique=True)
    op.create_index(
        "uq_linkedin_accounts_active",
        "linkedin_accounts",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
