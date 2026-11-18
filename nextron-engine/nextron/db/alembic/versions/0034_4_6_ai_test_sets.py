"""Story 4.6: bind each AI test run to the cases it measured, and let pseudonymised Operator
override cases be stored and erased (FR-73, FR-40, AD-12, AD-17, NFR-26), owned by ``playbook``.

Rebuilds ``ai_test_cases`` (``batch_alter_table(recreate="always")`` from 0030's exact table,
which 0031 to 0033 leave unchanged, given as ``copy_from`` with its partial index, so nothing
depends on reflection) with:

- ``origin`` (8, NOT NULL, server default ``'fixture'``, so every existing row is a fixture):
  ``fixture`` (a file under ``tests/ai_sets``) or ``override`` (an Operator's Reply class
  override, ``Playbook.add_override_case``);
- a nullable ``contact_id`` (26, no foreign key: the Contact an override case came from, so
  its erasure reaches the case; never the source Reply's id);
- ``ck_ai_test_cases_origin_contact``: an ``override`` row has a ``contact_id`` and a
  ``fixture`` row has none (so ``origin`` is one of the two).

``ck_ai_test_cases_test_set``, ``ck_ai_test_cases_sha256`` and ``uq_ai_test_cases_live_key``
(``WHERE retired_at IS NULL``) are kept, and so is every row.

Rebuilds ``ai_test_runs`` (the same way, from 0030's exact table with its foreign key and
``ix_ai_test_runs_tuple_id_test_set``; 0031 to 0033 leave it unchanged too) with a nullable
``cases_sha256`` (64): the fingerprint of the live cases the run measured
(``nextron.domain.ai_test_cases.case_set_sha256``), NULL only on a run stored before this
migration; ``ck_ai_test_runs_cases_sha256``: NULL or 64 lowercase hex
digits. Every 0030 check and every row are kept. ``ai_route_activations``' foreign key to
``ai_test_runs.id`` names the table, so it still holds after the rebuild.

``env.py`` runs with ``foreign_keys`` off, so the rebuilds are safe.

Revision ID: 0034_4_6_ai_test_sets
Revises: 0033_6_5_domain_checks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_4_6_ai_test_sets"
down_revision: str | None = "0033_6_5_domain_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEST_SET_CHECK = "test_set IN ('reply','grounding','drafting_smoke','extraction')"
SHA256_CHECK = "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'"
ORIGIN_CONTACT_CHECK = (
    "(origin = 'fixture' AND contact_id IS NULL) "
    "OR (origin = 'override' AND contact_id IS NOT NULL)"
)
CASES_SHA256_CHECK = (
    "cases_sha256 IS NULL OR "
    "(length(cases_sha256) = 64 AND cases_sha256 NOT GLOB '*[^0-9a-f]*')"
)


def _cases_0030() -> sa.Table:
    """``ai_test_cases`` exactly as ``0030_4_5_ai_test_runs`` left it (0031 to 0033 leave it
    unchanged), its partial unique index included."""
    return sa.Table(
        "ai_test_cases",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("case_key", sa.String(64), nullable=False),
        sa.Column("test_set", sa.String(16), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("covers", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("loaded_at", sa.String(27), nullable=False),
        sa.Column("retired_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ai_test_cases"),
        sa.CheckConstraint(TEST_SET_CHECK, name="ck_ai_test_cases_test_set"),
        sa.CheckConstraint(SHA256_CHECK, name="ck_ai_test_cases_sha256"),
        sa.Index(
            "uq_ai_test_cases_live_key",
            "test_set",
            "case_key",
            unique=True,
            sqlite_where=sa.text("retired_at IS NULL"),
        ),
    )


def _runs_0030() -> sa.Table:
    """``ai_test_runs`` exactly as ``0030_4_5_ai_test_runs`` left it (0031 to 0033 leave it
    unchanged), its index included."""
    return sa.Table(
        "ai_test_runs",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("tuple_id", sa.String(26), nullable=False),
        sa.Column("test_set", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("error", sa.String(16), nullable=True),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.Text(), nullable=False),
        sa.Column("results", sa.Text(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_test_runs"),
        sa.ForeignKeyConstraint(
            ["tuple_id"],
            ["ai_route_tuples.id"],
            name="fk_ai_test_runs_tuple_id_ai_route_tuples",
        ),
        sa.CheckConstraint(TEST_SET_CHECK, name="ck_ai_test_runs_test_set"),
        sa.CheckConstraint("outcome IN ('passed','failed')", name="ck_ai_test_runs_outcome"),
        sa.CheckConstraint(
            "error IS NULL OR error IN ('set_too_small','unknown_route')",
            name="ck_ai_test_runs_error",
        ),
        sa.CheckConstraint(
            "error IS NULL OR outcome = 'failed'", name="ck_ai_test_runs_error_when_failed"
        ),
        sa.CheckConstraint(
            "case_count >= 0 AND passed_count >= 0 AND passed_count <= case_count "
            "AND cost_micros >= 0",
            name="ck_ai_test_runs_counts",
        ),
        sa.Index("ix_ai_test_runs_tuple_id_test_set", "tuple_id", "test_set"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "ai_test_cases", recreate="always", copy_from=_cases_0030()
    ) as batch:
        batch.add_column(
            sa.Column("origin", sa.String(8), server_default=sa.text("'fixture'"), nullable=False)
        )
        batch.add_column(sa.Column("contact_id", sa.String(26), nullable=True))
        batch.create_check_constraint(op.f("ck_ai_test_cases_origin_contact"), ORIGIN_CONTACT_CHECK)
    with op.batch_alter_table("ai_test_runs", recreate="always", copy_from=_runs_0030()) as batch:
        batch.add_column(sa.Column("cases_sha256", sa.String(64), nullable=True))
        batch.create_check_constraint(op.f("ck_ai_test_runs_cases_sha256"), CASES_SHA256_CHECK)


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
