"""Story 2.3: the ``contacts`` and ``provenance_records`` tables (FR-14, FR-15, FR-51, AD-3,
AD-7, AD-17), owned by ``research``.

Creates ``contacts``: a ULID ``id``; ``lead_id`` (``fk_contacts_lead_id_leads``, index
``ix_contacts_lead_id``); nullable ``name`` and ``role`` (200), ``email`` (254),
``address_class`` (16) and ``linkedin_url`` (2048); ``is_primary``, server default false; a
nullable ``tz`` (64) and ``market_id`` (``fk_contacts_market_id_markets``); ``created_at`` and
``updated_at``. Its checks are ``ck_contacts_address_class`` (NULL or one of the four Address
Classes), ``ck_contacts_email_class`` (an address has a class and a class has an address) and
``ck_contacts_identity`` (a name or an email).

Creates ``provenance_records``: a ULID ``id``; ``contact_id``
(``fk_provenance_records_contact_id_contacts``, index ``ix_provenance_records_contact_id``);
``subject`` (16), ``source_kind`` (32) and ``source_detail`` (2048); a nullable ``address``
(254); ``retrieved_at``; a nullable ``address_class`` (16) and ``notes`` (text); and the
nullable eligibility-basis fields ``basis_rule`` (64), ``basis_campaign_id`` (a ULID, no
foreign key yet), ``lia_ref`` (200), ``lia_date`` (a date) and ``basis_recorded_at``. Its checks
are ``ck_provenance_records_subject`` (``contact`` or ``address``),
``ck_provenance_records_source_kind`` (the nine Provenance sources),
``ck_provenance_records_address`` (an address exactly on ``address`` records) and
``ck_provenance_records_address_class`` (NULL or one of the four classes).

Every other column is NOT NULL, and no foreign key cascades.

Revision ID: 0012_2_3_contacts_provenance
Revises: 0011_2_2_leads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_2_3_contacts_provenance"
down_revision: str | None = "0011_2_2_leads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSES = "('published','guessed','verified','invalid')"


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("role", sa.String(200), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("address_class", sa.String(16), nullable=True),
        sa.Column("linkedin_url", sa.String(2048), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("tz", sa.String(64), nullable=True),
        sa.Column("market_id", sa.String(26), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_contacts"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_contacts_lead_id_leads"),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name="fk_contacts_market_id_markets"
        ),
        sa.CheckConstraint(
            f"address_class IS NULL OR address_class IN {CLASSES}",
            name=op.f("ck_contacts_address_class"),
        ),
        sa.CheckConstraint(
            "(email IS NULL) = (address_class IS NULL)", name=op.f("ck_contacts_email_class")
        ),
        sa.CheckConstraint(
            "name IS NOT NULL OR email IS NOT NULL", name=op.f("ck_contacts_identity")
        ),
    )
    op.create_index("ix_contacts_lead_id", "contacts", ["lead_id"])
    op.create_table(
        "provenance_records",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("subject", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_detail", sa.String(2048), nullable=False),
        sa.Column("address", sa.String(254), nullable=True),
        sa.Column("retrieved_at", sa.String(27), nullable=False),
        sa.Column("address_class", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("basis_rule", sa.String(64), nullable=True),
        sa.Column("basis_campaign_id", sa.String(26), nullable=True),
        sa.Column("lia_ref", sa.String(200), nullable=True),
        sa.Column("lia_date", sa.Date(), nullable=True),
        sa.Column("basis_recorded_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_provenance_records"),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_provenance_records_contact_id_contacts"
        ),
        sa.CheckConstraint(
            "subject IN ('contact','address')", name=op.f("ck_provenance_records_subject")
        ),
        sa.CheckConstraint(
            "source_kind IN ('csv','website','signal_inbox','referral','replier','operator',"
            "'pattern','finder','eligibility_basis')",
            name=op.f("ck_provenance_records_source_kind"),
        ),
        sa.CheckConstraint(
            "(subject = 'address') = (address IS NOT NULL)",
            name=op.f("ck_provenance_records_address"),
        ),
        sa.CheckConstraint(
            f"address_class IS NULL OR address_class IN {CLASSES}",
            name=op.f("ck_provenance_records_address_class"),
        ),
    )
    op.create_index(
        "ix_provenance_records_contact_id", "provenance_records", ["contact_id"]
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
