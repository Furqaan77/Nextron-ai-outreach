"""Story 3.1: the ``markets`` table (FR-2, AD-12), owned by ``setup``.

Creates ``markets``: a ULID ``id``, a unique ``code`` (``uq_markets_code``), the ``name``, the
``enabled``, ``cold_email_allowed``, ``free_webmail_blocked`` and ``privacy_line_required``
flags, a unique ``country_code`` (``uq_markets_country_code``), ``cctlds`` and
``footer_template`` as canonical JSON lists, ``eligible_legal_forms`` (a JSON list, NULL when
legal forms are not checked), the ``default_timezone`` and ``unknown_timezone``, the
``unknown_send_window`` (NULL when it isn't narrowed), the ``english_variant``, the ``version``
(``ck_markets_version``: at least 1), ``created_at`` and ``updated_at``. Every column but the
two nullable ones is NOT NULL.

Revision ID: 0009_3_1_markets
Revises: 0008_2_1_segments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_3_1_markets"
down_revision: str | None = "0008_2_1_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("cctlds", sa.Text(), nullable=False),
        sa.Column("cold_email_allowed", sa.Boolean(), nullable=False),
        sa.Column("eligible_legal_forms", sa.Text(), nullable=True),
        sa.Column("free_webmail_blocked", sa.Boolean(), nullable=False),
        sa.Column("default_timezone", sa.String(64), nullable=False),
        sa.Column("unknown_timezone", sa.String(64), nullable=False),
        sa.Column("unknown_send_window", sa.String(11), nullable=True),
        sa.Column("english_variant", sa.String(5), nullable=False),
        sa.Column("footer_template", sa.Text(), nullable=False),
        sa.Column("privacy_line_required", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_markets"),
        sa.UniqueConstraint("code", name="uq_markets_code"),
        sa.UniqueConstraint("country_code", name="uq_markets_country_code"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_markets_version")),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
