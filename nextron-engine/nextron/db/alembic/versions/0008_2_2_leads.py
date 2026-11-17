"""Story 2.2: the ``leads`` table (FR-9, AD-3, AD-4), owned by ``discovery`` and ``pipeline``.

Creates ``leads``: a ULID ``id``; ``name``; the keys ``web_domain``, ``ch_number`` and
``place_id``, each nullable with a unique index ``uq_leads_<column>``; ``segment_id``
(``fk_leads_segment_id_segments``); a nullable ``country_code``; a nullable ``merged_into``
(``fk_leads_merged_into_leads``); ``created_at``; and ``pipeline_stage``, server default
``'new'``. Every other column is NOT NULL, and no foreign key cascades. The checks are
``ck_leads_pipeline_stage`` (the 14 stages), ``ck_leads_country_code`` (two uppercase letters),
``ck_leads_identity_key`` (a Lead that isn't merged holds a key) and
``ck_leads_not_self_merged``.

Revision ID: 0008_2_2_leads
Revises: 0007_2_1_segments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_2_2_leads"
down_revision: str | None = "0007_2_1_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("web_domain", sa.String(253), nullable=True),
        sa.Column("ch_number", sa.String(8), nullable=True),
        sa.Column("place_id", sa.String(512), nullable=True),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("merged_into", sa.String(26), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column(
            "pipeline_stage", sa.String(32), server_default=sa.text("'new'"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leads"),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_leads_segment_id_segments"
        ),
        sa.ForeignKeyConstraint(["merged_into"], ["leads.id"], name="fk_leads_merged_into_leads"),
        sa.CheckConstraint(
            "pipeline_stage IN ('new','queued','contacted','replied','interested','call_booked',"
            "'call_held','audit_proposed','audit_won','build_proposed','won','not_now','lost',"
            "'disqualified')",
            name=op.f("ck_leads_pipeline_stage"),
        ),
        sa.CheckConstraint(
            "country_code IS NULL OR country_code GLOB '[A-Z][A-Z]'",
            name=op.f("ck_leads_country_code"),
        ),
        sa.CheckConstraint(
            "merged_into IS NOT NULL OR web_domain IS NOT NULL OR ch_number IS NOT NULL "
            "OR place_id IS NOT NULL",
            name=op.f("ck_leads_identity_key"),
        ),
        sa.CheckConstraint(
            "merged_into IS NULL OR merged_into <> id", name=op.f("ck_leads_not_self_merged")
        ),
    )
    op.create_index("uq_leads_web_domain", "leads", ["web_domain"], unique=True)
    op.create_index("uq_leads_ch_number", "leads", ["ch_number"], unique=True)
    op.create_index("uq_leads_place_id", "leads", ["place_id"], unique=True)


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
