"""Story 4.2: the ``offers``, ``campaigns`` and ``variants`` tables (FR-3, AD-4, NFR-3), owned by
``setup``.

Creates ``offers``: a ULID ``id``, a unique ``key`` (``uq_offers_key``), the ``name``, the
``ladder`` (canonical JSON of its rungs), the ``version`` (``ck_offers_version``: at least 1),
``created_at`` and ``updated_at``.

Creates ``campaigns``: a ULID ``id``, a unique ``key`` (``uq_campaigns_key``), the ``name``, the
``segment_id``, ``market_id``, ``offer_id`` and ``sequence_id`` (foreign keys to ``segments``,
``markets``, ``offers`` and ``sequences``, no cascade; unique per Segment and Market,
``uq_campaigns_segment_id_market_id``), the ``status`` (``ck_campaigns_status``: ``draft``,
``active`` or ``paused``), ``lia_ref`` and ``lia_date`` (nullable; ``ck_campaigns_lia``: both
NULL or both set), the ``version`` (``ck_campaigns_version``: at least 1), ``created_at`` and
``updated_at``.

Creates ``variants``: a ULID ``id``, the ``campaign_id`` (a foreign key to ``campaigns``, no
cascade), the ``name`` (unique per Campaign, ``uq_variants_campaign_id_name``), the ``status``
(``ck_variants_status``: ``active`` or ``inactive``), ``step_angles`` (canonical JSON, ``[]``
for none), ``created_at`` and ``updated_at``.

Every column but ``lia_ref`` and ``lia_date`` is NOT NULL. Three triggers are fail-closed
backstops: ``trg_campaigns_lia_insert`` and ``trg_campaigns_lia_update`` abort an ``INSERT`` or
``UPDATE`` of a Campaign whose ``status`` isn't ``draft``, whose Market's ``country_code`` is
``GB`` and whose ``lia_ref`` or ``lia_date`` is NULL (NFR-3: no UK Campaign runs without a
recorded legitimate-interests assessment); ``trg_variants_at_most_two`` aborts an ``INSERT`` of a
Variant when its Campaign already has 2.

Revision ID: 0021_4_2_campaigns
Revises: 0020_2_5_crawl
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_4_2_campaigns"
down_revision: str | None = "0020_2_5_crawl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIA_MISSING = "a UK Campaign needs its legitimate-interests assessment before it runs"
TOO_MANY_VARIANTS = "a Campaign can have at most 2 Variants"
# A non-draft row in a GB Market without both LIA columns: what the two LIA triggers refuse.
LIA_GAP = (
    "NEW.status <> 'draft' "
    "AND (SELECT country_code FROM markets WHERE id = NEW.market_id) = 'GB' "
    "AND (NEW.lia_ref IS NULL OR NEW.lia_date IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("ladder", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.UniqueConstraint("key", name="uq_offers_key"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_offers_version")),
    )
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("market_id", sa.String(26), nullable=False),
        sa.Column("offer_id", sa.String(26), nullable=False),
        sa.Column("sequence_id", sa.String(26), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("lia_ref", sa.String(200), nullable=True),
        sa.Column("lia_date", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_campaigns_segment_id_segments"
        ),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name="fk_campaigns_market_id_markets"
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], name="fk_campaigns_offer_id_offers"),
        sa.ForeignKeyConstraint(
            ["sequence_id"], ["sequences.id"], name="fk_campaigns_sequence_id_sequences"
        ),
        sa.UniqueConstraint("key", name="uq_campaigns_key"),
        sa.UniqueConstraint(
            "segment_id", "market_id", name="uq_campaigns_segment_id_market_id"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused')", name=op.f("ck_campaigns_status")
        ),
        sa.CheckConstraint(
            "(lia_ref IS NULL AND lia_date IS NULL) "
            "OR (lia_ref IS NOT NULL AND lia_date IS NOT NULL)",
            name=op.f("ck_campaigns_lia"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_campaigns_version")),
    )
    op.create_table(
        "variants",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("campaign_id", sa.String(26), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("step_angles", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_variants"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_variants_campaign_id_campaigns"
        ),
        sa.UniqueConstraint("campaign_id", "name", name="uq_variants_campaign_id_name"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name=op.f("ck_variants_status")),
    )
    op.execute(
        "CREATE TRIGGER trg_campaigns_lia_insert BEFORE INSERT ON campaigns "
        f"WHEN {LIA_GAP} "
        f"BEGIN SELECT RAISE(ABORT, '{LIA_MISSING}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_campaigns_lia_update BEFORE UPDATE ON campaigns "
        f"WHEN {LIA_GAP} "
        f"BEGIN SELECT RAISE(ABORT, '{LIA_MISSING}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_variants_at_most_two BEFORE INSERT ON variants "
        "WHEN (SELECT COUNT(*) FROM variants WHERE campaign_id = NEW.campaign_id) >= 2 "
        f"BEGIN SELECT RAISE(ABORT, '{TOO_MANY_VARIANTS}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
