"""Story 6.11: each Sending Inbox's cold-send pacing (FR-31, AD-11, NFR-22), in
``sending_inboxes``, owned by ``sending``.

Adds to ``sending_inboxes`` two nullable columns, both PII class ``none``:

- ``next_send_at`` (27 characters, UTC text): the earliest time the inbox's next cold send may
  go (``sending_policy.next_send_after`` / ``repaced_next_send_at``);
- ``last_sent_at`` (27 characters, UTC text): when the inbox last sent a cold message.

Both start NULL: an inbox without a ``next_send_at`` sends no cold message until the startup
re-pace sets one. No planned send slot is stored (AD-11). Both are added with
``batch_alter_table`` and ``add_column`` only: SQLite adds them in place, without a rebuild, and
every row, check and index is kept.

Revision ID: 0063_6_11_inbox_pacing
Revises: 0062_8_7_crawl_run_mode
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_6_11_inbox_pacing"
down_revision: str | None = "0062_8_7_crawl_run_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sending_inboxes") as batch:
        batch.add_column(sa.Column("next_send_at", sa.String(27), nullable=True))
        batch.add_column(sa.Column("last_sent_at", sa.String(27), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
