"""Story 4.19: Segment fallback copy for hand-picked Tier C Leads (FR-22, AD-7, AD-12).

Creates ``fallback_templates`` (owner ``setup``; every column PII class ``none``): one row per
Segment fallback template, ``segment_id`` (a foreign key to ``segments``, no cascade), ``step``
(``ck_fallback_templates_step``: 1 to 6), ``english_variant``
(``ck_fallback_templates_english_variant``: ``en_us`` or ``en_gb``), unique together, the
``subject`` (set exactly on Step 1, ``ck_fallback_templates_subject``), the ``body``,
``created_at`` and ``updated_at``.

Rebuilds ``grounding_results`` (owner ``drafting``; ``batch_alter_table(recreate="always")``
from 0059's exact table, given as ``copy_from``, so nothing depends on reflection) with
``waived_rules`` (a JSON array of rule ids, NOT NULL, server default ``'[]'``,
``ck_grounding_results_waived_rules``; PII class ``none``): the rules a generic revision
waived. Every existing row backfills ``[]``. Every 0058 and 0059 check, the unique pair, both
foreign keys and every row are kept; the table has no trigger. ``env.py`` runs with
``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0066_4_19_fallback_templates
Revises: 0065_4_16_draft_when_ready
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_4_19_fallback_templates"
down_revision: str | None = "0065_4_16_draft_when_ready"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "pass", "fail")
PASS_CHECK = (
    "status != 'pass' OR (route_tuple_id IS NOT NULL AND served_model IS NOT NULL AND "
    "ai_checked_at IS NOT NULL)"
)
WAIVED_RULES_CHECK = "json_valid(waived_rules) AND json_type(waived_rules) = 'array'"


def _results_0059() -> sa.Table:
    """``grounding_results`` exactly as ``0059_4_14_ai_grounding`` left it."""
    return sa.Table(
        "grounding_results",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("draft_id", sa.String(26), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("flags", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Integer(), nullable=False),
        sa.Column("route_tuple_id", sa.String(26), nullable=True),
        sa.Column("served_model", sa.String(64), nullable=True),
        sa.Column("checked_at", sa.String(27), nullable=False),
        sa.Column("ai_checked_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_grounding_results"),
        sa.UniqueConstraint("draft_id", "rev", name="uq_grounding_results_draft_id_rev"),
        sa.ForeignKeyConstraint(
            ["draft_id", "rev"],
            ["draft_revisions.draft_id", "draft_revisions.rev"],
            name="fk_grounding_results_draft_id_rev_draft_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["route_tuple_id"],
            ["ai_route_tuples.id"],
            name="fk_grounding_results_route_tuple_id_ai_route_tuples",
        ),
        sa.CheckConstraint(
            "status IN ({})".format(",".join(f"'{status}'" for status in STATUSES)),
            name="ck_grounding_results_status",
        ),
        sa.CheckConstraint(
            "json_valid(flags) AND json_type(flags) = 'array'",
            name="ck_grounding_results_flags",
        ),
        sa.CheckConstraint(
            "(status = 'fail') = (json_array_length(flags) > 0)",
            name="ck_grounding_results_fail",
        ),
        sa.CheckConstraint("rules_version >= 1", name="ck_grounding_results_rules_version"),
        sa.CheckConstraint(PASS_CHECK, name="ck_grounding_results_pass"),
    )


def upgrade() -> None:
    op.create_table(
        "fallback_templates",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("segment_id", sa.String(26), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("english_variant", sa.String(5), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fallback_templates")),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["segments.id"],
            name=op.f("fk_fallback_templates_segment_id_segments"),
        ),
        sa.UniqueConstraint(
            "segment_id",
            "step",
            "english_variant",
            name=op.f("uq_fallback_templates_segment_id_step_english_variant"),
        ),
        sa.CheckConstraint("step BETWEEN 1 AND 6", name=op.f("ck_fallback_templates_step")),
        sa.CheckConstraint(
            "english_variant IN ('en_us', 'en_gb')",
            name=op.f("ck_fallback_templates_english_variant"),
        ),
        sa.CheckConstraint(
            "(step = 1) = (subject IS NOT NULL)", name=op.f("ck_fallback_templates_subject")
        ),
    )
    with op.batch_alter_table(
        "grounding_results", recreate="always", copy_from=_results_0059()
    ) as batch:
        batch.add_column(
            sa.Column("waived_rules", sa.Text(), server_default=sa.text("'[]'"), nullable=False)
        )
        batch.create_check_constraint(op.f("ck_grounding_results_waived_rules"), WAIVED_RULES_CHECK)


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
