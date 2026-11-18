"""Story 6.5: the ``domain_checks`` table (FR-64, NFR-4, NFR-20), owned by ``sending``.

Creates ``domain_checks``, append-only: a ULID ``id``, the ``sending_domain_id`` of the Sending
Domain checked (``fk_domain_checks_sending_domain_id_sending_domains``, no cascade),
``checked_at``, the Boolean outcomes ``spf_ok``, ``dkim_ok``, ``dmarc_ok``, ``aligned_ok`` and
``passed`` (``ck_domain_checks_passed``: ``passed = (spf_ok AND dkim_ok AND dmarc_ok AND
aligned_ok)``) and ``details`` (256: each item's reason as compact JSON). Index
``ix_domain_checks_sending_domain_id_checked_at``. Every column is NOT NULL.

Revision ID: 0033_6_5_domain_checks
Revises: 0032_3_13_erase_contacts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_6_5_domain_checks"
down_revision: str | None = "0032_3_13_erase_contacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_checks",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("sending_domain_id", sa.String(26), nullable=False),
        sa.Column("checked_at", sa.String(27), nullable=False),
        sa.Column("spf_ok", sa.Boolean(), nullable=False),
        sa.Column("dkim_ok", sa.Boolean(), nullable=False),
        sa.Column("dmarc_ok", sa.Boolean(), nullable=False),
        sa.Column("aligned_ok", sa.Boolean(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("details", sa.String(256), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_domain_checks"),
        sa.ForeignKeyConstraint(
            ["sending_domain_id"],
            ["sending_domains.id"],
            name="fk_domain_checks_sending_domain_id_sending_domains",
        ),
        sa.CheckConstraint(
            "passed = (spf_ok AND dkim_ok AND dmarc_ok AND aligned_ok)",
            name=op.f("ck_domain_checks_passed"),
        ),
    )
    op.create_index(
        "ix_domain_checks_sending_domain_id_checked_at",
        "domain_checks",
        ["sending_domain_id", "checked_at"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
