"""Story 8.3: the ``discovery_runs`` table (FR-5, AD-7), owned by ``discovery``.

Creates ``discovery_runs``: one row per discovery run. A ULID ``id``; ``source`` (16,
``ck_discovery_runs_source``: ``places``); ``campaign_id``
(``fk_discovery_runs_campaign_id_campaigns``) and ``segment_id``
(``fk_discovery_runs_segment_id_segments``), no cascade; ``country_code`` (2,
``ck_discovery_runs_country_code``: two uppercase letters); ``location`` (100, the Operator's
setting text); ``query_hash`` (16); ``status`` (16, ``ck_discovery_runs_status``: ``running``,
``finished``, ``deferred``, ``failed`` or ``stopped``); ``started_at`` and the nullable
``finished_at`` (NULL exactly while ``running``: ``ck_discovery_runs_finished_at``); and the
counts ``seen``, ``created``, ``merged``, ``suppressed``, ``no_website`` and ``market_blocked``
(``ck_discovery_runs_counts``: none negative). Index ``ix_discovery_runs_started_at``. Every
column is PII class ``none``, and none holds anything a Places search returned.

Revision ID: 0038_8_3_discovery_runs
Revises: 0037_8_2_place_coords
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_8_3_discovery_runs"
down_revision: str | None = "0037_8_2_place_coords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNTS: tuple[str, ...] = (
    "seen",
    "created",
    "merged",
    "suppressed",
    "no_website",
    "market_blocked",
)


def upgrade() -> None:
    op.create_table(
        "discovery_runs",
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
        *(sa.Column(name, sa.Integer(), nullable=False) for name in COUNTS),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_runs"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_discovery_runs_campaign_id_campaigns"
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_discovery_runs_segment_id_segments"
        ),
        sa.CheckConstraint("source IN ('places')", name=op.f("ck_discovery_runs_source")),
        sa.CheckConstraint(
            "status IN ('running','finished','deferred','failed','stopped')",
            name=op.f("ck_discovery_runs_status"),
        ),
        sa.CheckConstraint(
            "country_code GLOB '[A-Z][A-Z]'", name=op.f("ck_discovery_runs_country_code")
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_discovery_runs_finished_at"),
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in COUNTS),
            name=op.f("ck_discovery_runs_counts"),
        ),
    )
    op.create_index("ix_discovery_runs_started_at", "discovery_runs", ["started_at"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
