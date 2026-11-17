"""Story 4.11: the ``footer_snapshots`` table (FR-23, AD-3), owned by ``drafting``.

Creates ``footer_snapshots``, each distinct footer text a Market's emails were composed with: a
ULID ``id``, the ``market_id`` (a foreign key to ``markets``, no cascade), the ``sha256`` of the
text (``ck_footer_snapshots_sha256``: 64 characters, each ``0-9`` or ``a-f``), the ``text``
(``ck_footer_snapshots_text``: not empty) and ``created_at``. Every column is NOT NULL, and a
(``market_id``, ``sha256``) pair is stored once (``uq_footer_snapshots_market_id_sha256``).

Revision ID: 0013_4_11_footer_snapshots
Revises: 0012_4_2_campaigns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_4_11_footer_snapshots"
down_revision: str | None = "0012_4_2_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "footer_snapshots",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("market_id", sa.String(26), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_footer_snapshots"),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name="fk_footer_snapshots_market_id_markets"
        ),
        sa.UniqueConstraint("market_id", "sha256", name="uq_footer_snapshots_market_id_sha256"),
        sa.CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_footer_snapshots_sha256"),
        ),
        sa.CheckConstraint("length(text) > 0", name=op.f("ck_footer_snapshots_text")),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
