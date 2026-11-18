"""Story 9.1: move Leads through the later Pipeline Stages by hand, with a deal value and notes
(FR-45, FR-37, FR-63, AD-4), in the ``pipeline`` service.

Adds to ``leads`` (the pipeline group) two nullable columns:

- ``deal_value`` (BigInteger): the deal's value in integer micro-dollars, a whole number of US
  dollars from 0 to 1,000,000,000;
- ``notes`` (Text): the Operator's plain-text notes, at most 5,000 characters (PII class
  ``redact``: erasing a Contact sets its Lead's notes to ``[erased]``).

Both are added with ``batch_alter_table`` and ``add_column`` only: SQLite adds them in place,
without a rebuild, and no CHECK constrains them (``nextron.domain.manual_stages`` keeps the
limits).

Creates ``call_outcomes``: one row per booked discovery call (``id``, ``lead_id``,
``contact_id``, ``scheduled_at``, ``prospect_tz``, ``booked_at``, ``booked_by``,
``superseded_at``), indexed on ``lead_id``. Story 9.4 extends it with the call's outcome.

Revision ID: 0049_9_1_manual_stages
Revises: 0048_10_2_assist_accounts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_9_1_manual_stages"
down_revision: str | None = "0048_10_2_assist_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("deal_value", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
    op.create_table(
        "call_outcomes",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("scheduled_at", sa.String(27), nullable=False),
        sa.Column("prospect_tz", sa.String(64), nullable=False),
        sa.Column("booked_at", sa.String(27), nullable=False),
        sa.Column("booked_by", sa.String(64), nullable=False),
        sa.Column("superseded_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_call_outcomes"),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_call_outcomes_lead_id_leads"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_call_outcomes_contact_id_contacts"
        ),
    )
    op.create_index("ix_call_outcomes_lead_id", "call_outcomes", ["lead_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
