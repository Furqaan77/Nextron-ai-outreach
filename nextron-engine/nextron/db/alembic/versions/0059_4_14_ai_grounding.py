"""Story 4.14: the AI grounding check's part of ``grounding_results`` (FR-21, AD-7, AD-8, AD-18),
owned by ``drafting``.

Rebuilds ``grounding_results`` (``batch_alter_table(recreate="always")`` from 0058's exact
table, given as ``copy_from``, so nothing depends on reflection) with:

- ``ai_checked_at`` (UTC text, 27 characters, nullable; PII class ``none``): when the AI check's
  result was recorded, NULL until then;
- ``ck_grounding_results_pass``: a ``pass`` row names the tuple that served the check
  (``route_tuple_id``), the model that answered (``served_model``) and ``ai_checked_at``, so a
  Draft never passes without an AI check (fail closed).

Every 0058 check, the unique pair, both foreign keys and every row are kept. Each existing
flag (all of them the deterministic rules', Story 4.13) gains ``"source": "rules"``, so every
stored flag is ``{rule, span, explanation, source}`` with ``source`` ``rules`` or ``ai``.
``env.py`` runs with ``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0059_4_14_ai_grounding
Revises: 0058_4_13_grounding_results
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_4_14_ai_grounding"
down_revision: str | None = "0058_4_13_grounding_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "pass", "fail")
PASS_CHECK = (
    "status != 'pass' OR (route_tuple_id IS NOT NULL AND served_model IS NOT NULL AND "
    "ai_checked_at IS NOT NULL)"
)


def _results_0046() -> sa.Table:
    """``grounding_results`` exactly as ``0058_4_13_grounding_results`` created it."""
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
    )


def _tag_rules_flags() -> None:
    """Give every stored flag ``"source": "rules"`` (every flag before 4.14 is the rules')."""
    bind = op.get_bind()
    table = sa.table("grounding_results", sa.column("id"), sa.column("flags"))
    for row in bind.execute(sa.select(table.c.id, table.c.flags)).all():
        flags = json.loads(row.flags)
        tagged = [dict(flag, source=flag.get("source", "rules")) for flag in flags]
        if tagged != flags:
            bind.execute(
                sa.update(table).where(table.c.id == row.id).values(flags=json.dumps(tagged))
            )


def upgrade() -> None:
    with op.batch_alter_table(
        "grounding_results", recreate="always", copy_from=_results_0046()
    ) as batch:
        batch.add_column(sa.Column("ai_checked_at", sa.String(27), nullable=True))
        batch.create_check_constraint(op.f("ck_grounding_results_pass"), PASS_CHECK)
    _tag_rules_flags()


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
