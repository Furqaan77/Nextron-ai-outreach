"""Story 9.4: prompt for and record each Call Outcome (FR-69, FR-45, AD-4), in the ``pipeline``
service.

Adds to ``call_outcomes`` eleven nullable columns, the call's outcome:

- ``outcome`` (``held`` or ``no_show``), ``qualified`` (Boolean), ``reason`` (Text: why the Lead
  isn't qualified), ``audit_proposed`` (Boolean), ``audit_price`` (BigInteger micro-dollars),
  ``objections`` (Sales Playbook §5 codes, comma-joined and sorted; ``""`` for none),
  ``price_reaction`` (``accepted``, ``pushed_back`` or ``not_discussed``), ``next_step`` (Text)
  and ``next_step_on`` (Date);
- ``recorded_at`` and ``recorded_by``: when, and by whom, the outcome was first recorded (NULL
  while the booking awaits its outcome).

``reason`` and ``next_step`` are plain text of at most 500 characters (PII class ``redact``:
erasing a Contact sets them to ``[erased]``). All are added with ``batch_alter_table`` and
``add_column`` only: SQLite adds them in place, without a rebuild, and no CHECK constrains them
(``nextron.domain.call_outcomes`` keeps the rules).

Revision ID: 0038_9_4_call_outcomes
Revises: 0037_9_1_manual_stages
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_9_4_call_outcomes"
down_revision: str | None = "0037_9_1_manual_stages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("call_outcomes") as batch:
        batch.add_column(sa.Column("outcome", sa.String(16), nullable=True))
        batch.add_column(sa.Column("qualified", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("next_step", sa.Text(), nullable=True))
        batch.add_column(sa.Column("next_step_on", sa.Date(), nullable=True))
        batch.add_column(sa.Column("audit_proposed", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("audit_price", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("objections", sa.String(200), nullable=True))
        batch.add_column(sa.Column("price_reaction", sa.String(16), nullable=True))
        batch.add_column(sa.Column("recorded_at", sa.String(27), nullable=True))
        batch.add_column(sa.Column("recorded_by", sa.String(64), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
