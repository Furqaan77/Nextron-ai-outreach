"""Story 4.5: the AI Test Set cases and runs, and the run that qualified each activation (FR-73,
FR-71, AD-8, AD-12, NFR-26), owned by ``playbook``.

Creates ``ai_test_cases``: a ULID ``id``, the ``case_key`` (64), the ``test_set`` (16,
``ck_ai_test_cases_test_set``: ``reply``, ``grounding``, ``drafting_smoke`` or ``extraction``),
the ``route`` (64), the ``input``, ``expected`` and ``covers`` (canonical JSON text), the
case's ``sha256`` (``ck_ai_test_cases_sha256``: 64 lowercase hex digits), ``loaded_at`` and a
nullable ``retired_at``. The partial unique index ``uq_ai_test_cases_live_key`` on ``(test_set,
case_key)`` ``WHERE retired_at IS NULL`` keeps one live case per key.

Creates ``ai_test_runs``: a ULID ``id``, the ``tuple_id`` (a foreign key to
``ai_route_tuples.id``, ``fk_ai_test_runs_tuple_id_ai_route_tuples``, no cascade), the
``test_set`` (``ck_ai_test_runs_test_set``), the ``outcome`` (8, ``ck_ai_test_runs_outcome``:
``passed`` or ``failed``), a nullable ``error`` (16, ``ck_ai_test_runs_error``: NULL,
``set_too_small`` or ``unknown_route``; ``ck_ai_test_runs_error_when_failed``: set only on a
failed run), ``case_count`` and ``passed_count`` (``ck_ai_test_runs_counts``: both at least 0,
``passed_count`` at most ``case_count``, and ``cost_micros`` at least 0), the ``metrics`` and
``results`` (canonical JSON text), ``cost_micros``, ``started_at`` and ``finished_at``, with the
index ``ix_ai_test_runs_tuple_id_test_set``.

Rebuilds ``ai_route_activations`` (``batch_alter_table(recreate="always")`` from 0022's exact
table, given as ``copy_from`` with its partial index, so nothing depends on reflection) with the
foreign key ``fk_ai_route_activations_test_run_id_ai_test_runs`` from ``test_run_id`` to
``ai_test_runs.id`` (no cascade). Every 0022 check (``ck_ai_route_activations_tested``
unchanged), both foreign keys and ``uq_ai_route_activations_active_route`` (``WHERE
deactivated_at IS NULL``) are kept, and so is every row.

Every column but the nullable ones named here is NOT NULL. ``env.py`` runs with ``foreign_keys``
off, so the rebuild is safe.

Revision ID: 0023_4_5_ai_test_runs
Revises: 0022_4_4_playbook_snapshots
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_4_5_ai_test_runs"
down_revision: str | None = "0022_4_4_playbook_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EFFORT_CHECK = "effort IN ('low','medium','high','xhigh','max')"
SCHEMA_VERSION_CHECK = "schema_version BETWEEN 1 AND 9999"
PLAYBOOK_SHA256_CHECK = (
    "playbook_sha256 IS NULL OR "
    "(length(playbook_sha256) = 64 AND playbook_sha256 NOT GLOB '*[^0-9a-f]*')"
)
TEST_SET_CHECK = "test_set IN ('reply','grounding','drafting_smoke','extraction')"


def _activations_0022() -> sa.Table:
    """``ai_route_activations`` exactly as ``0022_4_4_playbook_snapshots`` left it, its partial
    unique index included."""
    return sa.Table(
        "ai_route_activations",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("effort", sa.String(8), nullable=False),
        sa.Column("prompt_version", sa.String(5), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("playbook_sha256", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("bootstrap", sa.Boolean(), nullable=False),
        sa.Column("test_run_id", sa.String(26), nullable=True),
        sa.Column("activated_at", sa.String(27), nullable=False),
        sa.Column("deactivated_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ai_route_activations"),
        sa.ForeignKeyConstraint(
            ["route", "prompt_version", "prompt_sha256"],
            [
                "prompt_snapshots.route",
                "prompt_snapshots.prompt_version",
                "prompt_snapshots.sha256",
            ],
            name="fk_ai_route_activations_route_prompt_version_prompt_sha256_prompt_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["playbook_sha256"],
            ["playbook_snapshots.sha256"],
            name="fk_ai_route_activations_playbook_sha256_playbook_snapshots",
        ),
        sa.CheckConstraint(EFFORT_CHECK, name="ck_ai_route_activations_effort"),
        sa.CheckConstraint(SCHEMA_VERSION_CHECK, name="ck_ai_route_activations_schema_version"),
        sa.CheckConstraint(
            "(bootstrap = 1 AND test_run_id IS NULL) "
            "OR (bootstrap = 0 AND test_run_id IS NOT NULL)",
            name="ck_ai_route_activations_tested",
        ),
        sa.CheckConstraint(PLAYBOOK_SHA256_CHECK, name="ck_ai_route_activations_playbook_sha256"),
        sa.Index(
            "uq_ai_route_activations_active_route",
            "route",
            unique=True,
            sqlite_where=sa.text("deactivated_at IS NULL"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "ai_test_cases",
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
        sa.CheckConstraint(TEST_SET_CHECK, name=op.f("ck_ai_test_cases_test_set")),
        sa.CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_ai_test_cases_sha256"),
        ),
    )
    op.create_index(
        "uq_ai_test_cases_live_key",
        "ai_test_cases",
        ["test_set", "case_key"],
        unique=True,
        sqlite_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "ai_test_runs",
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
        sa.CheckConstraint(TEST_SET_CHECK, name=op.f("ck_ai_test_runs_test_set")),
        sa.CheckConstraint(
            "outcome IN ('passed','failed')", name=op.f("ck_ai_test_runs_outcome")
        ),
        sa.CheckConstraint(
            "error IS NULL OR error IN ('set_too_small','unknown_route')",
            name=op.f("ck_ai_test_runs_error"),
        ),
        sa.CheckConstraint(
            "error IS NULL OR outcome = 'failed'", name=op.f("ck_ai_test_runs_error_when_failed")
        ),
        sa.CheckConstraint(
            "case_count >= 0 AND passed_count >= 0 AND passed_count <= case_count "
            "AND cost_micros >= 0",
            name=op.f("ck_ai_test_runs_counts"),
        ),
    )
    op.create_index(
        "ix_ai_test_runs_tuple_id_test_set", "ai_test_runs", ["tuple_id", "test_set"]
    )
    with op.batch_alter_table(
        "ai_route_activations", recreate="always", copy_from=_activations_0022()
    ) as batch:
        batch.create_foreign_key(
            "fk_ai_route_activations_test_run_id_ai_test_runs",
            "ai_test_runs",
            ["test_run_id"],
            ["id"],
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
