"""Story 4.13: one grounding result per Draft revision (FR-21, AD-6, AD-7, AD-18), owned by
``drafting``.

Creates ``grounding_results``: a ULID ``id``; ``draft_id`` and ``rev``, unique as a pair
(``uq_grounding_results_draft_id_rev``) and together a foreign key to ``draft_revisions``
(``draft_id``, ``rev``) (``fk_grounding_results_draft_id_rev_draft_revisions``, no cascade), so a
result belongs to exactly one existing revision; ``status`` (``ck_grounding_results_status``:
``pending``, ``pass`` or ``fail``); ``flags`` (a JSON array of ``{rule, span, explanation}``
objects; ``ck_grounding_results_flags``), with ``ck_grounding_results_fail``: the status is
``fail`` exactly when ``flags`` holds a flag; ``rules_version``
(``ck_grounding_results_rules_version``: at least 1); the nullable ``route_tuple_id``
(``fk_grounding_results_route_tuple_id_ai_route_tuples``) and ``served_model``, NULL until the
AI check (Story 4.14); and ``checked_at``.

Revision ID: 0046_4_13_grounding_results
Revises: 0045_4_12_draft_revisions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_4_13_grounding_results"
down_revision: str | None = "0045_4_12_draft_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "pass", "fail")


def upgrade() -> None:
    op.create_table(
        "grounding_results",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("draft_id", sa.String(26), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("flags", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Integer(), nullable=False),
        sa.Column("route_tuple_id", sa.String(26), nullable=True),
        sa.Column("served_model", sa.String(64), nullable=True),
        sa.Column("checked_at", sa.String(27), nullable=False),
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
            name=op.f("ck_grounding_results_status"),
        ),
        sa.CheckConstraint(
            "json_valid(flags) AND json_type(flags) = 'array'",
            name=op.f("ck_grounding_results_flags"),
        ),
        sa.CheckConstraint(
            "(status = 'fail') = (json_array_length(flags) > 0)",
            name=op.f("ck_grounding_results_fail"),
        ),
        sa.CheckConstraint("rules_version >= 1", name=op.f("ck_grounding_results_rules_version")),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
