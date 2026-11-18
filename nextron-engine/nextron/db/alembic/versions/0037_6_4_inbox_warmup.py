"""Story 6.4: each Sending Inbox's warm-up record and Daily Cap ramp (FR-33, FR-34, FR-19,
NFR-22, AD-11), in ``sending_inboxes``, owned by ``sending``.

Rebuilds ``sending_inboxes`` (``batch_alter_table(recreate="always")`` from 0018's exact table,
which 0019 to 0036 leave unchanged, given as ``copy_from`` with its unique index, so nothing
depends on reflection) with six new columns, all PII class ``none``:

- ``warmup_started_on`` (a date), ``warmup_method`` (40: ``manual`` or a warm-up tool's name)
  and ``warm_level`` (an integer): the warm-up record, NULL until it is recorded;
- ``warmup_tool_daily`` (integer, NOT NULL, server default 0), ``ramp_step`` (NOT NULL, server
  default 5) and ``ramp_target`` (NOT NULL, server default 30): the ramp.

The checks added:

- ``ck_sending_inboxes_warmup_record``: the three warm-up record columns are all NULL or all
  set;
- ``ck_sending_inboxes_warmup_ranges``: ``warmup_method`` 1 to 40 characters, ``warm_level`` 0
  to 100, ``warmup_tool_daily`` 0 to 100, ``ramp_step`` 1 to 10 and ``ramp_target`` 1 to 50
  (``nextron.domain.sending_policy``).

Every 0018 check, the foreign key to ``sending_domains``, ``uq_sending_inboxes_address`` and
every row are kept; an existing inbox reads as warm-up unrecorded with the default ramp, so its
cold Daily Cap is 0. ``env.py`` runs with ``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0037_6_4_inbox_warmup
Revises: 0036_2_12_legal_form
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_6_4_inbox_warmup"
down_revision: str | None = "0036_2_12_legal_form"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WARMUP_RECORD_CHECK = (
    "(warmup_started_on IS NULL AND warmup_method IS NULL AND warm_level IS NULL) OR "
    "(warmup_started_on IS NOT NULL AND warmup_method IS NOT NULL AND warm_level IS NOT NULL)"
)
WARMUP_RANGES_CHECK = (
    "(warmup_method IS NULL OR length(warmup_method) BETWEEN 1 AND 40) AND "
    "(warm_level IS NULL OR warm_level BETWEEN 0 AND 100) AND "
    "warmup_tool_daily BETWEEN 0 AND 100 AND "
    "ramp_step BETWEEN 1 AND 10 AND "
    "ramp_target BETWEEN 1 AND 50"
)


def _inboxes_0018() -> sa.Table:
    """``sending_inboxes`` exactly as ``0018_4_7_sending_inboxes`` created it (0019 to 0036
    leave it unchanged), its unique index included."""
    return sa.Table(
        "sending_inboxes",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("address", sa.String(254), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("sending_domain_id", sa.String(26), nullable=False),
        sa.Column("from_name", sa.String(100), nullable=False),
        sa.Column(
            "status", sa.String(16), server_default=sa.text("'connecting'"), nullable=False
        ),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sending_inboxes"),
        sa.ForeignKeyConstraint(
            ["sending_domain_id"],
            ["sending_domains.id"],
            name="fk_sending_inboxes_sending_domain_id_sending_domains",
        ),
        sa.CheckConstraint(
            "status IN ('connecting','active','paused','auth_failed','removed')",
            name="ck_sending_inboxes_status",
        ),
        sa.CheckConstraint(
            "address NOT LIKE '%@nextronai.com' AND address NOT LIKE '%.nextronai.com'",
            name="ck_sending_inboxes_not_brand",
        ),
        sa.Index("uq_sending_inboxes_address", "address", unique=True),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "sending_inboxes", recreate="always", copy_from=_inboxes_0018()
    ) as batch:
        batch.add_column(sa.Column("warmup_started_on", sa.Date(), nullable=True))
        batch.add_column(sa.Column("warmup_method", sa.String(40), nullable=True))
        batch.add_column(sa.Column("warm_level", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "warmup_tool_daily", sa.Integer(), server_default=sa.text("0"), nullable=False
            )
        )
        batch.add_column(
            sa.Column("ramp_step", sa.Integer(), server_default=sa.text("5"), nullable=False)
        )
        batch.add_column(
            sa.Column("ramp_target", sa.Integer(), server_default=sa.text("30"), nullable=False)
        )
        batch.create_check_constraint(
            op.f("ck_sending_inboxes_warmup_record"), WARMUP_RECORD_CHECK
        )
        batch.create_check_constraint(
            op.f("ck_sending_inboxes_warmup_ranges"), WARMUP_RANGES_CHECK
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
