"""Story 1.13: the ``api_usage`` and ``ai_usage`` tables (AD-8, AD-9, FR-57), written only by
``nextron.db.metering``.

Creates ``api_usage``: one row per attempted HTTP request (provider, operation, SKU, units, the
outcome and its time), with ``ck_api_usage_units``, ``ck_api_usage_ok`` and
``ck_api_usage_outcome`` (the error code is set exactly when the call failed), and
``ix_api_usage_at``. Creates ``ai_usage``: one row per AI call (route, route tuple, served
model, token counts, iterations, the batch flag, the cost in micro-dollars, the refused flag,
the error code and its time), with ``ck_ai_usage_counts``, ``ck_ai_usage_flags`` and
``ix_ai_usage_at``.

Revision ID: 0006_1_13_metering
Revises: 0005_1_12_notify
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_1_13_metering"
down_revision: str | None = "0005_1_12_notify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_usage",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(32), nullable=True),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_api_usage"),
        sa.CheckConstraint("units >= 0", name=op.f("ck_api_usage_units")),
        sa.CheckConstraint("ok IN (0, 1)", name=op.f("ck_api_usage_ok")),
        sa.CheckConstraint(
            "(ok = 1) = (error_code IS NULL)", name=op.f("ck_api_usage_outcome")
        ),
    )
    op.create_index("ix_api_usage_at", "api_usage", ["at"])
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("route_tuple", sa.String(128), nullable=False),
        sa.Column("served_model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("iterations", sa.Integer(), nullable=False),
        sa.Column("batch", sa.Boolean(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("refused", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_usage"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 "
            "AND cache_write_tokens >= 0 AND iterations >= 0 AND cost_micros >= 0",
            name=op.f("ck_ai_usage_counts"),
        ),
        sa.CheckConstraint(
            "batch IN (0, 1) AND refused IN (0, 1)", name=op.f("ck_ai_usage_flags")
        ),
    )
    op.create_index("ix_ai_usage_at", "ai_usage", ["at"])


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
