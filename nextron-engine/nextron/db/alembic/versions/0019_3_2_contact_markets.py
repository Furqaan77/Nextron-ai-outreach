"""Story 3.2: each Contact's Market and timezone bases and office location (FR-2, FR-51, AD-7,
AD-11, AD-17), owned by ``research``.

Rebuilds ``contacts`` (``batch_alter_table(recreate="always")`` from 0015's exact table, which
0016 to 0018 leave unchanged, given as ``copy_from``, so nothing depends on reflection) with four
new nullable columns after ``market_id``: ``market_basis`` (32) and ``tz_basis`` (32), why the
Contact's Market and timezone apply (``nextron.domain.contact_market``), and ``office_country``
(2) and ``office_region`` (2), the place its office is in. Every 0015 check, both foreign keys and ``ix_contacts_lead_id`` are
kept, and these checks are added:

- ``ck_contacts_market_basis`` and ``ck_contacts_tz_basis``: NULL or one of the bases;
- ``ck_contacts_resolved``: a Contact has both bases or neither;
- ``ck_contacts_market_tz``: a Contact has a Market and a timezone, or neither;
- ``ck_contacts_office_country``: NULL or two capitals;
- ``ck_contacts_office_region``: NULL, or two capitals with an office country.

Existing rows keep every value, with the four new columns NULL; the ``resolve_contact_markets``
job resolves them. ``env.py`` runs with ``foreign_keys`` off, so the rebuild under
``provenance_records``' foreign key to ``contacts`` is safe.

Revision ID: 0019_3_2_contact_markets
Revises: 0018_4_7_sending_inboxes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_3_2_contact_markets"
down_revision: str | None = "0018_4_7_sending_inboxes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSES = "('published','guessed','verified','invalid')"
MARKET_BASES = (
    "('office_location','lead_country','conflict_stricter','cctld_outside_markets',"
    "'location_outside_markets','location_unknown')"
)
TZ_BASES = "('office_location','lead_hq','market_default','us_unknown_fallback','no_market')"


def _contacts_0015() -> sa.Table:
    """``contacts`` exactly as ``0015_2_3_contacts_provenance`` created it (0016 to 0018 don't
    change it)."""
    return sa.Table(
        "contacts",
        sa.MetaData(),
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
            name="ck_contacts_address_class",
        ),
        sa.CheckConstraint(
            "(email IS NULL) = (address_class IS NULL)", name="ck_contacts_email_class"
        ),
        sa.CheckConstraint("name IS NOT NULL OR email IS NOT NULL", name="ck_contacts_identity"),
        sa.Index("ix_contacts_lead_id", "lead_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "contacts", recreate="always", copy_from=_contacts_0015()
    ) as batch:
        batch.add_column(
            sa.Column("market_basis", sa.String(32), nullable=True), insert_after="market_id"
        )
        batch.add_column(
            sa.Column("tz_basis", sa.String(32), nullable=True), insert_after="market_basis"
        )
        batch.add_column(
            sa.Column("office_country", sa.String(2), nullable=True), insert_after="tz_basis"
        )
        batch.add_column(
            sa.Column("office_region", sa.String(2), nullable=True),
            insert_after="office_country",
        )
        batch.create_check_constraint(
            op.f("ck_contacts_market_basis"),
            f"market_basis IS NULL OR market_basis IN {MARKET_BASES}",
        )
        batch.create_check_constraint(
            op.f("ck_contacts_tz_basis"), f"tz_basis IS NULL OR tz_basis IN {TZ_BASES}"
        )
        batch.create_check_constraint(
            op.f("ck_contacts_resolved"), "(market_basis IS NULL) = (tz_basis IS NULL)"
        )
        batch.create_check_constraint(
            op.f("ck_contacts_market_tz"), "(market_id IS NULL) = (tz IS NULL)"
        )
        batch.create_check_constraint(
            op.f("ck_contacts_office_country"),
            "office_country IS NULL OR office_country GLOB '[A-Z][A-Z]'",
        )
        batch.create_check_constraint(
            op.f("ck_contacts_office_region"),
            "office_region IS NULL OR "
            "(office_country IS NOT NULL AND office_region GLOB '[A-Z][A-Z]')",
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
