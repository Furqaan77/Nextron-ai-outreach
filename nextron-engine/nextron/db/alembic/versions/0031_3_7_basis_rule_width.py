"""Story 3.7: widen ``provenance_records.basis_rule`` to 128 characters (FR-51, AD-7, AD-17),
owned by ``research``.

A UK eligibility basis ("UK: corporate subscriber (<legal form>), legitimate interests") is 71
to 108 characters with a label, and 113 with a 64-character legal-form code; 0015 made the
column 64. Rebuilds ``provenance_records`` (``batch_alter_table(recreate="always")`` from 0015's
exact table, which 0016 to 0030 leave unchanged, given as ``copy_from``, so nothing depends on
reflection) with ``basis_rule`` as ``String(128)``. Every row and every other column is kept as
it is, and so are the four checks (``ck_provenance_records_subject``,
``ck_provenance_records_source_kind``, ``ck_provenance_records_address`` and
``ck_provenance_records_address_class``), ``fk_provenance_records_contact_id_contacts`` and
``ix_provenance_records_contact_id``. No column, table or check is added. ``env.py`` runs with
``foreign_keys`` off, and no table references ``provenance_records``.

Revision ID: 0031_3_7_basis_rule_width
Revises: 0030_4_5_ai_test_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_3_7_basis_rule_width"
down_revision: str | None = "0030_4_5_ai_test_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSES = "('published','guessed','verified','invalid')"


def _provenance_records_0015() -> sa.Table:
    """``provenance_records`` exactly as ``0015_2_3_contacts_provenance`` created it (0016 to
    0030 don't change it)."""
    return sa.Table(
        "provenance_records",
        sa.MetaData(),
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
            "subject IN ('contact','address')", name="ck_provenance_records_subject"
        ),
        sa.CheckConstraint(
            "source_kind IN ('csv','website','signal_inbox','referral','replier','operator',"
            "'pattern','finder','eligibility_basis')",
            name="ck_provenance_records_source_kind",
        ),
        sa.CheckConstraint(
            "(subject = 'address') = (address IS NOT NULL)",
            name="ck_provenance_records_address",
        ),
        sa.CheckConstraint(
            f"address_class IS NULL OR address_class IN {CLASSES}",
            name="ck_provenance_records_address_class",
        ),
        sa.Index("ix_provenance_records_contact_id", "contact_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "provenance_records", recreate="always", copy_from=_provenance_records_0015()
    ) as batch:
        batch.alter_column(
            "basis_rule",
            existing_type=sa.String(64),
            type_=sa.String(128),
            existing_nullable=True,
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
