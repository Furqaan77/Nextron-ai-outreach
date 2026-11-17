"""Story 4.7: the ``sending_domains`` and ``sending_inboxes`` tables (FR-32, AD-3, AD-4,
AD-21), owned by ``sending``.

Creates ``sending_domains``: a ULID ``id``, the registrable ``domain`` (unique index
``uq_sending_domains_domain``; ``ck_sending_domains_not_brand``: never ``nextronai.com``) and
``created_at``.

Creates ``sending_inboxes``: a ULID ``id``, the normalised ``address`` (unique index
``uq_sending_inboxes_address``), the ``label``, the ``sending_domain_id`` of its Sending Domain
(``fk_sending_inboxes_sending_domain_id_sending_domains``, no cascade), the ``from_name``, the
``status`` (server default ``'connecting'``; ``ck_sending_inboxes_status``: the five
SendingInbox statuses) and ``created_at``. ``ck_sending_inboxes_not_brand``: the address is on
neither nextronai.com nor a subdomain of it. Every column is NOT NULL.

Revision ID: 0014_4_7_sending_inboxes
Revises: 0013_4_1_sequences
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_4_7_sending_inboxes"
down_revision: str | None = "0013_4_1_sequences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sending_domains",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sending_domains"),
        sa.CheckConstraint(
            "domain <> 'nextronai.com'", name=op.f("ck_sending_domains_not_brand")
        ),
    )
    op.create_index("uq_sending_domains_domain", "sending_domains", ["domain"], unique=True)
    op.create_table(
        "sending_inboxes",
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
            name=op.f("ck_sending_inboxes_status"),
        ),
        sa.CheckConstraint(
            "address NOT LIKE '%@nextronai.com' AND address NOT LIKE '%.nextronai.com'",
            name=op.f("ck_sending_inboxes_not_brand"),
        ),
    )
    op.create_index("uq_sending_inboxes_address", "sending_inboxes", ["address"], unique=True)


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
