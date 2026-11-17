"""Story 2.6: the Lead's ``contact_methods`` profile value and the ``page_contact_methods``
table (FR-10, AD-7, AD-17), owned by ``research``.

Adds ``leads.contact_methods``, a nullable ``Text`` (the canonical JSON of
``nextron.domain.widgets.ContactMethods``), in place: ``ALTER TABLE ... ADD COLUMN``, no rebuild,
so every existing Lead keeps its row and gets NULL.

Creates ``page_contact_methods``: a ULID ``id``, ``page_id``
(``fk_page_contact_methods_page_id_crawled_pages``, no cascade, indexed by
``ix_page_contact_methods_page_id``), ``method`` (``ck_page_contact_methods_method``: ``form``,
``chat``, ``booking``, ``phone`` or ``after_hours``), ``signature`` (32), ``source``
(``ck_page_contact_methods_source``: ``script``, ``iframe``, ``link``, ``html`` or ``text``),
``matched`` (200) and ``detected_at``; unique on ``(page_id, method, signature, source)``
(``uq_page_contact_methods_page_id_method_signature_source``). Every column is NOT NULL.

Revision ID: 0022_2_6_contact_methods
Revises: 0021_4_2_campaigns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_2_6_contact_methods"
down_revision: str | None = "0021_4_2_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("contact_methods", sa.Text(), nullable=True))
    op.create_table(
        "page_contact_methods",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("page_id", sa.String(26), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("signature", sa.String(32), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("matched", sa.String(200), nullable=False),
        sa.Column("detected_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_page_contact_methods"),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["crawled_pages.id"],
            name="fk_page_contact_methods_page_id_crawled_pages",
        ),
        sa.UniqueConstraint(
            "page_id",
            "method",
            "signature",
            "source",
            name="uq_page_contact_methods_page_id_method_signature_source",
        ),
        sa.CheckConstraint(
            "method IN ('form','chat','booking','phone','after_hours')",
            name=op.f("ck_page_contact_methods_method"),
        ),
        sa.CheckConstraint(
            "source IN ('script','iframe','link','html','text')",
            name=op.f("ck_page_contact_methods_source"),
        ),
    )
    op.create_index("ix_page_contact_methods_page_id", "page_contact_methods", ["page_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
