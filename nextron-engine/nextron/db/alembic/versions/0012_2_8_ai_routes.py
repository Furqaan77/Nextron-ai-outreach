"""Story 2.8: the ``prompt_snapshots`` and ``ai_route_activations`` tables (FR-71, AD-8, AD-12,
AD-18), owned by ``playbook``.

Creates ``prompt_snapshots``: a ULID ``id``, the ``route``, the ``prompt_version``, the
``sha256`` of the text, the ``text`` and ``created_at``. It is unique on ``(route,
prompt_version)`` (``uq_prompt_snapshots_route_prompt_version``) and on ``(route,
prompt_version, sha256)`` (``uq_prompt_snapshots_route_prompt_version_sha256``, the foreign
key's target). ``ck_prompt_snapshots_sha256``: 64 lowercase hex digits;
``ck_prompt_snapshots_prompt_version``: ``v`` and 1 to 9999.

Creates ``ai_route_activations``: a ULID ``id``, the ``route``, ``model``, ``effort``,
``prompt_version``, ``prompt_sha256`` and ``schema_version`` of the activated tuple, the
``actor``, ``bootstrap``, a nullable ``test_run_id``, ``activated_at`` and a nullable
``deactivated_at`` (NULL while active). ``(route, prompt_version, prompt_sha256)`` references
the snapshot (``fk_ai_route_activations_route_prompt_version_prompt_sha256_prompt_snapshots``,
no cascade). The checks: ``ck_ai_route_activations_effort`` (the five efforts),
``ck_ai_route_activations_schema_version`` (1 to 9999) and ``ck_ai_route_activations_tested`` (a
bootstrap activation has no test run, any other has one). The partial unique index
``uq_ai_route_activations_active_route`` allows one active activation per route.

Every column but the two nullable ones is NOT NULL.

Revision ID: 0012_2_8_ai_routes
Revises: 0011_2_2_leads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_2_8_ai_routes"
down_revision: str | None = "0011_2_2_leads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_snapshots",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(5), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_snapshots"),
        sa.UniqueConstraint(
            "route", "prompt_version", name="uq_prompt_snapshots_route_prompt_version"
        ),
        sa.UniqueConstraint(
            "route",
            "prompt_version",
            "sha256",
            name="uq_prompt_snapshots_route_prompt_version_sha256",
        ),
        sa.CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_prompt_snapshots_sha256"),
        ),
        sa.CheckConstraint(
            "length(prompt_version) BETWEEN 2 AND 5 AND prompt_version GLOB 'v[1-9]*' "
            "AND substr(prompt_version, 2) NOT GLOB '*[^0-9]*'",
            name=op.f("ck_prompt_snapshots_prompt_version"),
        ),
    )
    op.create_table(
        "ai_route_activations",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("effort", sa.String(8), nullable=False),
        sa.Column("prompt_version", sa.String(5), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
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
        sa.CheckConstraint(
            "effort IN ('low','medium','high','xhigh','max')",
            name=op.f("ck_ai_route_activations_effort"),
        ),
        sa.CheckConstraint(
            "schema_version BETWEEN 1 AND 9999",
            name=op.f("ck_ai_route_activations_schema_version"),
        ),
        sa.CheckConstraint(
            "(bootstrap = 1 AND test_run_id IS NULL) "
            "OR (bootstrap = 0 AND test_run_id IS NOT NULL)",
            name=op.f("ck_ai_route_activations_tested"),
        ),
    )
    op.create_index(
        "uq_ai_route_activations_active_route",
        "ai_route_activations",
        ["route"],
        unique=True,
        sqlite_where=sa.text("deactivated_at IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
