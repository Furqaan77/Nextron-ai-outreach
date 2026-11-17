"""Story 4.4: the Sales Playbook snapshots, the candidate route tuples and the Playbook checks
(FR-71, AD-8, AD-12, NFR-9, NFR-25), owned by ``playbook``.

Creates ``playbook_snapshots``: a ULID ``id``, the ``sha256`` of the content (unique,
``uq_playbook_snapshots_sha256``; ``ck_playbook_snapshots_sha256``: 64 lowercase hex digits), the
front matter's ``version`` (``ck_playbook_snapshots_version``: 1 to 32 letters, digits, dots,
plus and minus signs, starting with a letter or digit), the ``content`` and ``captured_at``.

Rebuilds ``ai_route_activations`` (``batch_alter_table(recreate="always")`` from 0014's exact
table, which 0015 to 0021 leave unchanged, given as ``copy_from`` with its partial index, so
nothing depends on reflection) with a nullable ``playbook_sha256`` (64) after
``prompt_sha256``: a foreign key to ``playbook_snapshots.sha256``
(``fk_ai_route_activations_playbook_sha256_playbook_snapshots``, no cascade) and
``ck_ai_route_activations_playbook_sha256`` (NULL or 64 lowercase hex digits). Every 0014 check
(``ck_ai_route_activations_tested`` unchanged), the composite foreign key and the partial
unique index ``uq_ai_route_activations_active_route`` (``WHERE deactivated_at IS NULL``) are
kept, and existing rows keep every value with ``playbook_sha256`` NULL.

Creates ``ai_route_tuples``: a ULID ``id``, the ``route``, ``model``, ``effort``,
``prompt_version``, ``prompt_sha256``, a nullable ``playbook_sha256``, the ``schema_version``
and ``created_at``. ``(route, prompt_version, prompt_sha256)`` references the prompt snapshot
(``fk_ai_route_tuples_route_prompt_version_prompt_sha256_prompt_snapshots``) and
``playbook_sha256`` the Playbook snapshot
(``fk_ai_route_tuples_playbook_sha256_playbook_snapshots``), neither with a cascade. The checks ``ck_ai_route_tuples_effort``,
``ck_ai_route_tuples_schema_version`` and ``ck_ai_route_tuples_playbook_sha256`` are the
activations'. Two partial unique indexes keep one row per tuple:
``uq_ai_route_tuples_no_playbook`` on ``(route, model, effort, prompt_version,
schema_version)`` ``WHERE playbook_sha256 IS NULL`` and ``uq_ai_route_tuples_playbook`` on those
columns and ``playbook_sha256`` ``WHERE playbook_sha256 IS NOT NULL``.

Creates ``playbook_checks``: a ULID ``id``, ``checked_at``, the ``trigger``
(``ck_playbook_checks_trigger``: ``startup`` or ``operator_check``), a nullable
``playbook_sha256`` (a foreign key to ``playbook_snapshots.sha256``,
``fk_playbook_checks_playbook_sha256_playbook_snapshots``) and a nullable ``problem``
(``ck_playbook_checks_problem``: one of the five Playbook problems);
``ck_playbook_checks_outcome``: exactly one of the two is set.

Every column but the nullable ones named here is NOT NULL. ``env.py`` runs with
``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0022_4_4_playbook_snapshots
Revises: 0021_4_2_campaigns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_4_4_playbook_snapshots"
down_revision: str | None = "0021_4_2_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EFFORT_CHECK = "effort IN ('low','medium','high','xhigh','max')"
SCHEMA_VERSION_CHECK = "schema_version BETWEEN 1 AND 9999"
PLAYBOOK_SHA256_CHECK = (
    "playbook_sha256 IS NULL OR "
    "(length(playbook_sha256) = 64 AND playbook_sha256 NOT GLOB '*[^0-9a-f]*')"
)
PROBLEMS = "('unreadable','too_large','not_utf8','no_version','bad_version')"
TUPLE_COLUMNS = ["route", "model", "effort", "prompt_version", "schema_version"]


def _activations_0014() -> sa.Table:
    """``ai_route_activations`` exactly as ``0014_2_8_ai_routes`` created it (0015 to 0021
    don't change it), its partial unique index included."""
    return sa.Table(
        "ai_route_activations",
        sa.MetaData(),
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
        sa.CheckConstraint(EFFORT_CHECK, name="ck_ai_route_activations_effort"),
        sa.CheckConstraint(SCHEMA_VERSION_CHECK, name="ck_ai_route_activations_schema_version"),
        sa.CheckConstraint(
            "(bootstrap = 1 AND test_run_id IS NULL) "
            "OR (bootstrap = 0 AND test_run_id IS NOT NULL)",
            name="ck_ai_route_activations_tested",
        ),
        sa.Index(
            "uq_ai_route_activations_active_route",
            "route",
            unique=True,
            sqlite_where=sa.text("deactivated_at IS NULL"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "playbook_snapshots",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_snapshots"),
        sa.UniqueConstraint("sha256", name="uq_playbook_snapshots_sha256"),
        sa.CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_playbook_snapshots_sha256"),
        ),
        sa.CheckConstraint(
            "length(version) BETWEEN 1 AND 32 AND version GLOB '[0-9A-Za-z]*' "
            "AND version NOT GLOB '*[^0-9A-Za-z.+-]*'",
            name=op.f("ck_playbook_snapshots_version"),
        ),
    )
    with op.batch_alter_table(
        "ai_route_activations", recreate="always", copy_from=_activations_0014()
    ) as batch:
        batch.add_column(
            sa.Column("playbook_sha256", sa.String(64), nullable=True),
            insert_after="prompt_sha256",
        )
        batch.create_foreign_key(
            "fk_ai_route_activations_playbook_sha256_playbook_snapshots",
            "playbook_snapshots",
            ["playbook_sha256"],
            ["sha256"],
        )
        batch.create_check_constraint(
            op.f("ck_ai_route_activations_playbook_sha256"), PLAYBOOK_SHA256_CHECK
        )
    op.create_table(
        "ai_route_tuples",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("effort", sa.String(8), nullable=False),
        sa.Column("prompt_version", sa.String(5), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("playbook_sha256", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_route_tuples"),
        sa.ForeignKeyConstraint(
            ["route", "prompt_version", "prompt_sha256"],
            [
                "prompt_snapshots.route",
                "prompt_snapshots.prompt_version",
                "prompt_snapshots.sha256",
            ],
            name="fk_ai_route_tuples_route_prompt_version_prompt_sha256_prompt_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["playbook_sha256"],
            ["playbook_snapshots.sha256"],
            name="fk_ai_route_tuples_playbook_sha256_playbook_snapshots",
        ),
        sa.CheckConstraint(EFFORT_CHECK, name=op.f("ck_ai_route_tuples_effort")),
        sa.CheckConstraint(SCHEMA_VERSION_CHECK, name=op.f("ck_ai_route_tuples_schema_version")),
        sa.CheckConstraint(
            PLAYBOOK_SHA256_CHECK, name=op.f("ck_ai_route_tuples_playbook_sha256")
        ),
    )
    op.create_index(
        "uq_ai_route_tuples_no_playbook",
        "ai_route_tuples",
        TUPLE_COLUMNS,
        unique=True,
        sqlite_where=sa.text("playbook_sha256 IS NULL"),
    )
    op.create_index(
        "uq_ai_route_tuples_playbook",
        "ai_route_tuples",
        [*TUPLE_COLUMNS, "playbook_sha256"],
        unique=True,
        sqlite_where=sa.text("playbook_sha256 IS NOT NULL"),
    )
    op.create_table(
        "playbook_checks",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("checked_at", sa.String(27), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("playbook_sha256", sa.String(64), nullable=True),
        sa.Column("problem", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_checks"),
        sa.ForeignKeyConstraint(
            ["playbook_sha256"],
            ["playbook_snapshots.sha256"],
            name="fk_playbook_checks_playbook_sha256_playbook_snapshots",
        ),
        sa.CheckConstraint(
            "trigger IN ('startup','operator_check')", name=op.f("ck_playbook_checks_trigger")
        ),
        sa.CheckConstraint(
            f"problem IS NULL OR problem IN {PROBLEMS}", name=op.f("ck_playbook_checks_problem")
        ),
        sa.CheckConstraint(
            "(playbook_sha256 IS NULL) <> (problem IS NULL)",
            name=op.f("ck_playbook_checks_outcome"),
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
