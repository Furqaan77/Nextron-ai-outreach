"""Story 4.3: the ``company_facts`` table (FR-72, AD-7, NFR-24), owned by ``playbook``.

Creates ``company_facts``, the Approved Company Facts as append-only versions: a ULID ``id``,
the ``fact_id`` and its ``version`` (unique together, ``uq_company_facts_fact_id_version``;
``ck_company_facts_version``: at least 1), the ``type`` (``ck_company_facts_type``: ``company``,
``case_study``, ``offer``, ``regulation`` or ``statistic``), ``wording``, ``source_url``,
``allowed_segment_ids`` and ``allowed_market_ids`` (canonical JSON sorted lists of ULIDs),
``metric`` (nullable), ``metric_confirmed_at`` (a date, nullable), ``scope_note`` (nullable),
``last_checked_on`` (a date), ``created_by`` (``ck_company_facts_created_by``: ``seed`` or
``operator``), ``created_at`` and ``retracted_at`` (nullable). Every other column is NOT NULL.

Three triggers, as ``events`` has (migration 0001): ``trg_company_facts_no_delete`` aborts any
``DELETE``; ``trg_company_facts_no_id_reuse`` aborts an ``INSERT`` whose ``id``, or whose
(``fact_id``, ``version``), already exists, which stops ``INSERT OR REPLACE``; and
``trg_company_facts_immutable`` aborts an ``UPDATE`` that changes any column but
``retracted_at``, or changes ``retracted_at`` once it isn't NULL.

Revision ID: 0017_4_3_company_facts
Revises: 0016_1_19_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_4_3_company_facts"
down_revision: str | None = "0016_1_19_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = "company_facts versions are append-only"
# Every column but retracted_at, which an UPDATE may set once, from NULL.
FIXED_COLUMNS = (
    "id",
    "fact_id",
    "version",
    "type",
    "wording",
    "source_url",
    "allowed_segment_ids",
    "allowed_market_ids",
    "metric",
    "metric_confirmed_at",
    "scope_note",
    "last_checked_on",
    "created_by",
    "created_at",
)


def upgrade() -> None:
    op.create_table(
        "company_facts",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("fact_id", sa.String(31), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("wording", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("allowed_segment_ids", sa.Text(), nullable=False),
        sa.Column("allowed_market_ids", sa.Text(), nullable=False),
        sa.Column("metric", sa.String(200), nullable=True),
        sa.Column("metric_confirmed_at", sa.Date(), nullable=True),
        sa.Column("scope_note", sa.Text(), nullable=True),
        sa.Column("last_checked_on", sa.Date(), nullable=False),
        sa.Column("created_by", sa.String(8), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("retracted_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_company_facts"),
        sa.UniqueConstraint("fact_id", "version", name="uq_company_facts_fact_id_version"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_company_facts_version")),
        sa.CheckConstraint(
            "type IN ('company', 'case_study', 'offer', 'regulation', 'statistic')",
            name=op.f("ck_company_facts_type"),
        ),
        sa.CheckConstraint(
            "created_by IN ('seed', 'operator')", name=op.f("ck_company_facts_created_by")
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_company_facts_no_delete BEFORE DELETE ON company_facts "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_facts_no_id_reuse BEFORE INSERT ON company_facts "
        "WHEN EXISTS (SELECT 1 FROM company_facts WHERE id = NEW.id "
        "OR (fact_id = NEW.fact_id AND version = NEW.version)) "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in FIXED_COLUMNS)
    op.execute(
        "CREATE TRIGGER trg_company_facts_immutable BEFORE UPDATE ON company_facts "
        f"WHEN {changed} "
        "OR (OLD.retracted_at IS NOT NULL AND NEW.retracted_at IS NOT OLD.retracted_at) "
        f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
