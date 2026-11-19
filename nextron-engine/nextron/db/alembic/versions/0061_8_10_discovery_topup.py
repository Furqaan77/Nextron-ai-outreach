"""Story 8.10: the weekday discovery top-up (FR-5, FR-6, FR-9, AD-7, AD-10), owned by
``discovery`` (``discovery_runs``).

**``discovery_runs``** is rebuilt (``batch_alter_table(recreate="always")`` from 0060's exact
table, given as ``copy_from`` with its index, so nothing depends on reflection):

- two nullable columns, so every existing run gets NULL: ``scheduled_for`` (a date, stored as
  ISO ``YYYY-MM-DD`` text: the local date a scheduled run or a ``pool_sufficient`` row is for)
  and ``ready_pool`` (an integer: the Campaign's ready Leads when the top-up found its pool
  sufficient). Both are PII class ``none``;
- ``ck_discovery_runs_status`` also allows ``target_reached``, ``paced`` and
  ``pool_sufficient``;
- ``ck_discovery_runs_location`` holds a location exactly on a ``places`` run that isn't
  ``pool_sufficient`` (a ``pool_sufficient`` row records no run, so no location);
- new ``ck_discovery_runs_scheduled_for``: every ``pool_sufficient`` row has ``scheduled_for``;
- new ``ck_discovery_runs_ready_pool``: ``ready_pool`` is set exactly on a ``pool_sufficient``
  row, and never negative.

Every row, every other check, both foreign keys and ``ix_discovery_runs_started_at`` are kept.
``env.py`` runs with ``foreign_keys`` off, so the rebuild is safe; no table references
``discovery_runs``.

Revision ID: 0061_8_10_discovery_topup
Revises: 0060_8_4_registry_discovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_8_10_discovery_topup"
down_revision: str | None = "0060_8_4_registry_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNTS_0047: tuple[str, ...] = (
    "seen",
    "created",
    "merged",
    "suppressed",
    "no_website",
    "market_blocked",
)
COUNTS_0060: tuple[str, ...] = (*COUNTS_0047, "inactive")
STATUSES_0047 = "'running','finished','deferred','failed','stopped'"
STATUSES = f"{STATUSES_0047},'target_reached','paced','pool_sufficient'"


def _discovery_runs_0060() -> sa.Table:
    """``discovery_runs`` exactly as ``0060_8_4_registry_discovery`` left it, its index
    included."""
    return sa.Table(
        "discovery_runs",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("campaign_id", sa.String(26), nullable=False),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column("query_hash", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=True),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in COUNTS_0047),
        sa.Column("inactive", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_runs"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_discovery_runs_campaign_id_campaigns"
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_discovery_runs_segment_id_segments"
        ),
        sa.CheckConstraint(
            "source IN ('places','companies_house')", name="ck_discovery_runs_source"
        ),
        sa.CheckConstraint(f"status IN ({STATUSES_0047})", name="ck_discovery_runs_status"),
        sa.CheckConstraint(
            "country_code GLOB '[A-Z][A-Z]'", name="ck_discovery_runs_country_code"
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_discovery_runs_finished_at",
        ),
        sa.CheckConstraint(
            "(source = 'places') = (location IS NOT NULL)", name="ck_discovery_runs_location"
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in COUNTS_0060),
            name="ck_discovery_runs_counts",
        ),
        sa.Index("ix_discovery_runs_started_at", "started_at"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "discovery_runs", recreate="always", copy_from=_discovery_runs_0060()
    ) as batch:
        batch.add_column(sa.Column("scheduled_for", sa.Date(), nullable=True))
        batch.add_column(sa.Column("ready_pool", sa.Integer(), nullable=True))
        batch.drop_constraint(op.f("ck_discovery_runs_status"), type_="check")
        batch.drop_constraint(op.f("ck_discovery_runs_location"), type_="check")
        batch.create_check_constraint(op.f("ck_discovery_runs_status"), f"status IN ({STATUSES})")
        batch.create_check_constraint(
            op.f("ck_discovery_runs_location"),
            "(source = 'places' AND status <> 'pool_sufficient') = (location IS NOT NULL)",
        )
        batch.create_check_constraint(
            op.f("ck_discovery_runs_scheduled_for"),
            "status <> 'pool_sufficient' OR scheduled_for IS NOT NULL",
        )
        batch.create_check_constraint(
            op.f("ck_discovery_runs_ready_pool"),
            "(status = 'pool_sufficient') = (ready_pool IS NOT NULL) "
            "AND (ready_pool IS NULL OR ready_pool >= 0)",
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
