"""Story 4.8: the ``enrollments`` and ``variant_counters`` tables (FR-3, FR-63, AD-3, AD-4,
AD-16, AD-21), owned by ``sequencing``.

Creates ``enrollments``: a ULID ``id``; the ``lead_id``, ``contact_id``, ``campaign_id``,
``variant_id``, ``sending_inbox_id``, ``sequence_id`` and ``offer_id`` it is bound to (foreign
keys, no cascade); the ``sequence_version`` (at least 1); the ``origin`` and ``status`` (the
Enrollment machine's values); the ``pause_cause`` (set exactly while ``paused``) and the
``stop_cause`` (set exactly when ``stopped``); ``created_at`` and ``updated_at``;
``last_step_sent_at`` and ``ended_at`` (set exactly when ``stopped`` or ``completed``, never
before ``last_step_sent_at``). ``uq_enrollments_one_live_per_lead``: at most one ``active`` or
``paused`` Enrollment per Lead (a partial unique index). Indexes on ``contact_id`` and
``sending_inbox_id``. ``trg_enrollments_bound``: an update that changes ``id`` or any bound
column (``contact_id``, ``campaign_id``, ``variant_id``, ``sending_inbox_id``, ``sequence_id``,
``sequence_version``, ``offer_id``, ``origin``, ``created_at``) aborts.
``trg_enrollments_erased_insert``: an insert for an erased Contact aborts.

Creates ``variant_counters``: a ULID ``id``, the ``campaign_id`` (unique index
``uq_variant_counters_campaign_id``: one counter per Campaign), the ``last_variant_id``
assigned and ``updated_at``.

Revision ID: 0038_4_8_enrollments
Revises: 0037_3_14_erasure_receipts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_4_8_enrollments"
down_revision: str | None = "0037_3_14_erasure_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOUND = (
    "id",
    "contact_id",
    "campaign_id",
    "variant_id",
    "sending_inbox_id",
    "sequence_id",
    "sequence_version",
    "offer_id",
    "origin",
    "created_at",
)
BOUND_MESSAGE = "the bound columns of an Enrollment never change"
ERASED_MESSAGE = "an erased Contact is never enrolled"
ORIGINS = ("selection", "operator_pick", "referral", "recontact", "restart")
STATUSES = ("active", "paused", "stopped", "completed")
PAUSE_CAUSES = ("inbound_pending", "uncertain_class", "operator")
STOP_CAUSES = (
    "reply",
    "manual_reply",
    "hard_bounce",
    "suppressed",
    "operator_stop",
    "operator_rejected",
    "lead_closed",
    "inbox_removed",
    "contact_erased",
)


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def _fk(column: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{table}.id"], name=f"fk_enrollments_{column}_{table}"
    )


def upgrade() -> None:
    op.create_table(
        "enrollments",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("campaign_id", sa.String(26), nullable=False),
        sa.Column("variant_id", sa.String(26), nullable=False),
        sa.Column("sending_inbox_id", sa.String(26), nullable=False),
        sa.Column("sequence_id", sa.String(26), nullable=False),
        sa.Column("sequence_version", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.String(26), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pause_cause", sa.String(24), nullable=True),
        sa.Column("stop_cause", sa.String(24), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.Column("last_step_sent_at", sa.String(27), nullable=True),
        sa.Column("ended_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_enrollments"),
        _fk("lead_id", "leads"),
        _fk("contact_id", "contacts"),
        _fk("campaign_id", "campaigns"),
        _fk("variant_id", "variants"),
        _fk("sending_inbox_id", "sending_inboxes"),
        _fk("sequence_id", "sequences"),
        _fk("offer_id", "offers"),
        sa.CheckConstraint(_one_of("origin", ORIGINS), name=op.f("ck_enrollments_origin")),
        sa.CheckConstraint(_one_of("status", STATUSES), name=op.f("ck_enrollments_status")),
        sa.CheckConstraint(
            "pause_cause IS NULL OR " + _one_of("pause_cause", PAUSE_CAUSES),
            name=op.f("ck_enrollments_pause_cause"),
        ),
        sa.CheckConstraint(
            "stop_cause IS NULL OR " + _one_of("stop_cause", STOP_CAUSES),
            name=op.f("ck_enrollments_stop_cause"),
        ),
        sa.CheckConstraint(
            "(status = 'paused') = (pause_cause IS NOT NULL)", name=op.f("ck_enrollments_paused")
        ),
        sa.CheckConstraint(
            "(status = 'stopped') = (stop_cause IS NOT NULL)", name=op.f("ck_enrollments_stopped")
        ),
        sa.CheckConstraint(
            "(status IN ('stopped','completed')) = (ended_at IS NOT NULL)",
            name=op.f("ck_enrollments_ended"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR last_step_sent_at IS NULL OR ended_at >= last_step_sent_at",
            name=op.f("ck_enrollments_ended_after_last_step"),
        ),
        sa.CheckConstraint("sequence_version >= 1", name=op.f("ck_enrollments_sequence_version")),
    )
    op.create_index(
        "uq_enrollments_one_live_per_lead",
        "enrollments",
        ["lead_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('active','paused')"),
    )
    op.create_index("ix_enrollments_contact_id", "enrollments", ["contact_id"])
    op.create_index("ix_enrollments_sending_inbox_id", "enrollments", ["sending_inbox_id"])
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in BOUND)
    op.execute(
        f"CREATE TRIGGER trg_enrollments_bound BEFORE UPDATE ON enrollments WHEN {changed} "
        f"BEGIN SELECT RAISE(ABORT, '{BOUND_MESSAGE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_enrollments_erased_insert BEFORE INSERT ON enrollments "
        "WHEN EXISTS (SELECT 1 FROM contacts WHERE id = NEW.contact_id "
        "AND erased_at IS NOT NULL) "
        f"BEGIN SELECT RAISE(ABORT, '{ERASED_MESSAGE}'); END"
    )
    op.create_table(
        "variant_counters",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("campaign_id", sa.String(26), nullable=False),
        sa.Column("last_variant_id", sa.String(26), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_variant_counters"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_variant_counters_campaign_id_campaigns"
        ),
        sa.ForeignKeyConstraint(
            ["last_variant_id"],
            ["variants.id"],
            name="fk_variant_counters_last_variant_id_variants",
        ),
    )
    op.create_index(
        "uq_variant_counters_campaign_id", "variant_counters", ["campaign_id"], unique=True
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
