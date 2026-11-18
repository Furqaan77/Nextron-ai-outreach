"""Story 8.4: Companies House discovery (FR-6, FR-9, FR-12, FR-13, AD-7, AD-17), owned by
``discovery`` (``discovery_runs``, ``leads.website_lookup_at``) and ``research``
(``provenance_records``).

**``discovery_runs``** is rebuilt (``batch_alter_table(recreate="always")`` from 0038's exact
table, given as ``copy_from`` with its index, so nothing depends on reflection):

- ``ck_discovery_runs_source`` allows ``places`` and ``companies_house``;
- ``location`` becomes nullable, and ``ck_discovery_runs_location`` holds a location exactly on
  a ``places`` run (``(source = 'places') = (location IS NOT NULL)``): a Companies House run has
  none;
- a new count ``inactive`` (NOT NULL, server default 0, so every existing run counts 0), and
  ``ck_discovery_runs_counts`` covers it too.

Every row, every other check, both foreign keys and ``ix_discovery_runs_started_at`` are kept.

**``leads.website_lookup_at``** (27, UTC text, nullable, PII class ``none``, in the identity
group): when Companies House discovery last decided a registry Lead's website lookup. Added in
place (``add_column``, no rebuild, no CHECK), so every Lead gets NULL.

**``provenance_records``** is rebuilt from 0031's exact table (``basis_rule`` 128; 0032 to 0038
change no column, given as ``copy_from``) with ``ck_provenance_records_source_kind`` also
allowing ``registry`` (a Companies House officer). Every row, the other three checks, the
foreign key and ``ix_provenance_records_contact_id`` are kept. A rebuild drops the table's
triggers, so ``trg_provenance_records_erased_update`` and ``trg_provenance_records_erased_insert``
are created again exactly as 0032 created them.

``env.py`` runs with ``foreign_keys`` off, so the rebuilds are safe; no table references either
rebuilt table.

Revision ID: 0039_8_4_registry_discovery
Revises: 0038_8_3_discovery_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_8_4_registry_discovery"
down_revision: str | None = "0038_8_3_discovery_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNTS_0038: tuple[str, ...] = (
    "seen",
    "created",
    "merged",
    "suppressed",
    "no_website",
    "market_blocked",
)
COUNTS: tuple[str, ...] = (*COUNTS_0038, "inactive")
CLASSES = "('published','guessed','verified','invalid')"
SOURCE_KINDS_0031 = (
    "'csv','website','signal_inbox','referral','replier','operator','pattern','finder',"
    "'eligibility_basis'"
)
ERASED_PROVENANCE = "the Provenance of an erased Contact is a tombstone"  # 0032's message


def _discovery_runs_0038() -> sa.Table:
    """``discovery_runs`` exactly as ``0038_8_3_discovery_runs`` created it, its index
    included."""
    return sa.Table(
        "discovery_runs",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("campaign_id", sa.String(26), nullable=False),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("location", sa.String(100), nullable=False),
        sa.Column("query_hash", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=True),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in COUNTS_0038),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_runs"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_discovery_runs_campaign_id_campaigns"
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_discovery_runs_segment_id_segments"
        ),
        sa.CheckConstraint("source IN ('places')", name="ck_discovery_runs_source"),
        sa.CheckConstraint(
            "status IN ('running','finished','deferred','failed','stopped')",
            name="ck_discovery_runs_status",
        ),
        sa.CheckConstraint(
            "country_code GLOB '[A-Z][A-Z]'", name="ck_discovery_runs_country_code"
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_discovery_runs_finished_at",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in COUNTS_0038),
            name="ck_discovery_runs_counts",
        ),
        sa.Index("ix_discovery_runs_started_at", "started_at"),
    )


def _provenance_records_0031() -> sa.Table:
    """``provenance_records`` exactly as ``0031_3_7_basis_rule_width`` left it (0032 to 0038
    change no column), its index included."""
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
        sa.Column("basis_rule", sa.String(128), nullable=True),
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
            f"source_kind IN ({SOURCE_KINDS_0031})", name="ck_provenance_records_source_kind"
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
        "discovery_runs", recreate="always", copy_from=_discovery_runs_0038()
    ) as batch:
        batch.add_column(
            sa.Column("inactive", sa.Integer(), server_default=sa.text("0"), nullable=False)
        )
        batch.alter_column(
            "location", existing_type=sa.String(100), existing_nullable=False, nullable=True
        )
        batch.drop_constraint(op.f("ck_discovery_runs_source"), type_="check")
        batch.drop_constraint(op.f("ck_discovery_runs_counts"), type_="check")
        batch.create_check_constraint(
            op.f("ck_discovery_runs_source"), "source IN ('places','companies_house')"
        )
        batch.create_check_constraint(
            op.f("ck_discovery_runs_location"), "(source = 'places') = (location IS NOT NULL)"
        )
        batch.create_check_constraint(
            op.f("ck_discovery_runs_counts"), " AND ".join(f"{name} >= 0" for name in COUNTS)
        )
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("website_lookup_at", sa.String(27), nullable=True))
    with op.batch_alter_table(
        "provenance_records", recreate="always", copy_from=_provenance_records_0031()
    ) as batch:
        batch.drop_constraint(op.f("ck_provenance_records_source_kind"), type_="check")
        batch.create_check_constraint(
            op.f("ck_provenance_records_source_kind"),
            f"source_kind IN ({SOURCE_KINDS_0031},'registry')",
        )
    op.execute(
        "CREATE TRIGGER trg_provenance_records_erased_update BEFORE UPDATE ON "
        "provenance_records WHEN EXISTS (SELECT 1 FROM contacts "
        "WHERE id IN (OLD.contact_id, NEW.contact_id) AND erased_at IS NOT NULL) "
        f"BEGIN SELECT RAISE(ABORT, '{ERASED_PROVENANCE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_provenance_records_erased_insert BEFORE INSERT ON "
        "provenance_records WHEN EXISTS (SELECT 1 FROM contacts "
        "WHERE id = NEW.contact_id AND erased_at IS NOT NULL) "
        f"BEGIN SELECT RAISE(ABORT, '{ERASED_PROVENANCE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
