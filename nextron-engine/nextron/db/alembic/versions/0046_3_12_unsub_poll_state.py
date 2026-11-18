"""Story 3.12: the ``unsub_poll_state`` table (FR-70, FR-58, AD-15, AD-20), owned by
``compliance``.

Creates ``unsub_poll_state``, the one row recording how the unsubscribe poll went:

- ``id``, always ``00000000000000000000000001`` (the ULID whose value is 1;
  ``ck_unsub_poll_state_singleton``), so the table holds at most one row;
- ``last_success_at``, ``failing_since`` and ``last_failure_at``, nullable times;
- ``last_failure_code``, nullable, one of ``nextron.domain.unsub_requests.PollFailure``
  (``ck_unsub_poll_state_last_failure_code``).

``ck_unsub_poll_state_failure`` keeps the three failure columns set or NULL together. No column
holds a token, an address, the Worker host or the bearer.

Revision ID: 0046_3_12_unsub_poll_state
Revises: 0045_4_12_draft_revisions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_3_12_unsub_poll_state"
down_revision: str | None = "0045_4_12_draft_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FAILURE_CODES = (
    "unsub_refused",
    "unsub_bearer_invalid",
    "unsub_unauthorized",
    "unsub_unavailable",
    "unsub_bad_response",
    "unsub_unreachable",
    "internal_error",
)


def upgrade() -> None:
    codes = ",".join(f"'{code}'" for code in FAILURE_CODES)
    op.create_table(
        "unsub_poll_state",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("last_success_at", sa.String(27), nullable=True),
        sa.Column("failing_since", sa.String(27), nullable=True),
        sa.Column("last_failure_at", sa.String(27), nullable=True),
        sa.Column("last_failure_code", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_unsub_poll_state"),
        sa.CheckConstraint(
            "id = '00000000000000000000000001'", name=op.f("ck_unsub_poll_state_singleton")
        ),
        sa.CheckConstraint(
            f"last_failure_code IS NULL OR last_failure_code IN ({codes})",
            name=op.f("ck_unsub_poll_state_last_failure_code"),
        ),
        sa.CheckConstraint(
            "(failing_since IS NULL) = (last_failure_at IS NULL) "
            "AND (failing_since IS NULL) = (last_failure_code IS NULL)",
            name=op.f("ck_unsub_poll_state_failure"),
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
