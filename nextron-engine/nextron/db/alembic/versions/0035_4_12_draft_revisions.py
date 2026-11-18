"""Story 4.12: a Draft's text as immutable revisions bound to a hash (FR-20, FR-23, AD-6,
AD-17), owned by ``drafting``.

Creates ``draft_revisions``: a ULID ``id`` (every primary key is one ULID column, so a revision
is keyed by the unique pair ``uq_draft_revisions_draft_id_rev``); ``draft_id``
(``fk_draft_revisions_draft_id_drafts``, no cascade); ``rev`` (``ck_draft_revisions_rev``: at
least 1); ``subject`` and ``body`` (text, the body may be empty); ``footer_snapshot_id``
(``fk_draft_revisions_footer_snapshot_id_footer_snapshots``); ``evidence_ids`` and
``fact_versions`` (JSON arrays, ``[]`` allowed: ``ck_draft_revisions_evidence_ids`` and
``ck_draft_revisions_fact_versions``); the nullable ``cited_signal_id``
(``fk_draft_revisions_cited_signal_id_signals``) and ``route_tuple_id``
(``fk_draft_revisions_route_tuple_id_ai_route_tuples``); ``origin``
(``ck_draft_revisions_origin``: ``generated``, ``regenerated``, ``operator_edit`` or
``footer_change``; ``ck_draft_revisions_first``: revision 1 is exactly the ``generated`` one);
the nullable ``playbook_sha256`` (``ck_draft_revisions_playbook_sha256``: 64 lowercase hex),
``playbook_version`` and ``prompt_version``; the nullable ``instruction``
(``ck_draft_revisions_instruction``: NULL unless ``origin`` is ``regenerated``, at most 200
characters); ``text_hash`` (``ck_draft_revisions_text_hash``: 64 lowercase hex); and
``created_at``. Three triggers keep a revision immutable:

- ``trg_draft_revisions_no_delete`` aborts any ``DELETE``;
- ``trg_draft_revisions_insert_new`` aborts an ``INSERT`` whose ``id`` or (``draft_id``,
  ``rev``) already exists (so ``INSERT OR REPLACE`` can't replace a revision);
- ``trg_draft_revisions_immutable`` aborts an ``UPDATE`` that changes any column other than
  ``subject``, ``body`` and ``instruction``, or changes one of those other than from non-NULL
  to ``[erased]`` (the erasure tombstone, AD-17).

Rebuilds ``drafts`` (``batch_alter_table(recreate="always")`` from 0034's exact table, given as
``copy_from``, so nothing depends on reflection) to add
``fk_drafts_id_current_rev_draft_revisions``: (``id``, ``current_rev``) references
``draft_revisions`` (``draft_id``, ``rev``), so ``current_rev`` can only name an existing
revision of the same Draft (NULL names none). Every row, column, check and index of ``drafts``
is kept. ``env.py`` runs with ``foreign_keys`` off, and no table referenced ``drafts`` before
this migration; ``drafts`` is rebuilt before ``draft_revisions`` is created.

Revision ID: 0035_4_12_draft_revisions
Revises: 0034_4_9_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_4_12_draft_revisions"
down_revision: str | None = "0034_4_9_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REDACTED = "[erased]"  # nextron.domain.erasure.REDACTED
IMMUTABLE = "a Draft revision is immutable"
KINDS = ("step", "recontact", "reply", "rebook", "channel")
STATUSES = ("ready", "approved", "sent", "rejected", "superseded", "cancelled")
STEP_KINDS = "kind IN ('step','recontact')"
ORIGINS = ("generated", "regenerated", "operator_edit", "footer_change")
FIXED = (
    "id",
    "draft_id",
    "rev",
    "footer_snapshot_id",
    "evidence_ids",
    "fact_versions",
    "cited_signal_id",
    "origin",
    "route_tuple_id",
    "playbook_sha256",
    "playbook_version",
    "prompt_version",
    "text_hash",
    "created_at",
)
REDACTABLE = ("subject", "body", "instruction")


def _one_of(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


def _hex64(column: str) -> str:
    return f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"


def _json_list(column: str) -> str:
    return f"json_valid({column}) AND json_type({column}) = 'array'"


def _flag(name: str) -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text("0"), nullable=False)


def _drafts_0034() -> sa.Table:
    """``drafts`` exactly as ``0034_4_9_drafts`` created it."""
    return sa.Table(
        "drafts",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enrollment_id", sa.String(26), nullable=True),
        sa.Column("step_no", sa.Integer(), nullable=True),
        sa.Column("day_offset", sa.Integer(), nullable=True),
        sa.Column("current_rev", sa.Integer(), nullable=True),
        sa.Column("due_at", sa.String(27), nullable=True),
        _flag("generic"),
        _flag("stale"),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'ready'"), nullable=False),
        _flag("reapproval_required"),
        _flag("event_expired"),
        sa.Column("skipped_at", sa.String(27), nullable=True),
        sa.Column("status_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_drafts"),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"], name="fk_drafts_enrollment_id_enrollments"
        ),
        sa.CheckConstraint(_one_of("kind", KINDS), name="ck_drafts_kind"),
        sa.CheckConstraint(_one_of("status", STATUSES), name="ck_drafts_status"),
        sa.CheckConstraint(
            f"NOT ({STEP_KINDS}) OR (enrollment_id IS NOT NULL AND step_no IS NOT NULL "
            "AND day_offset IS NOT NULL)",
            name="ck_drafts_step_slot",
        ),
        sa.CheckConstraint("step_no IS NULL OR step_no >= 1", name="ck_drafts_step_no"),
        sa.CheckConstraint("day_offset IS NULL OR day_offset >= 0", name="ck_drafts_day_offset"),
        sa.CheckConstraint(
            "current_rev IS NULL OR current_rev >= 1", name="ck_drafts_current_rev"
        ),
        sa.Index(
            "uq_drafts_enrollment_step",
            "enrollment_id",
            "step_no",
            unique=True,
            sqlite_where=sa.text(STEP_KINDS),
        ),
        sa.Index("ix_drafts_due_at", "due_at"),
        sa.Index("ix_drafts_enrollment_id", "enrollment_id"),
    )


def _changed(columns: Sequence[str]) -> str:
    return " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)


def _redaction_only(column: str) -> str:
    """The SQL of a change to ``column`` other than the erasure tombstone."""
    return (
        f"(NEW.{column} IS NOT OLD.{column} AND NOT (OLD.{column} IS NOT NULL "
        f"AND NEW.{column} IS '{REDACTED}'))"
    )


def upgrade() -> None:
    with op.batch_alter_table("drafts", recreate="always", copy_from=_drafts_0034()) as batch:
        batch.create_foreign_key(
            "fk_drafts_id_current_rev_draft_revisions",
            "draft_revisions",
            ["id", "current_rev"],
            ["draft_id", "rev"],
        )
    op.create_table(
        "draft_revisions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("draft_id", sa.String(26), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("footer_snapshot_id", sa.String(26), nullable=False),
        sa.Column("evidence_ids", sa.Text(), nullable=False),
        sa.Column("fact_versions", sa.Text(), nullable=False),
        sa.Column("cited_signal_id", sa.String(26), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("route_tuple_id", sa.String(26), nullable=True),
        sa.Column("playbook_sha256", sa.String(64), nullable=True),
        sa.Column("playbook_version", sa.String(32), nullable=True),
        sa.Column("prompt_version", sa.String(5), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_draft_revisions"),
        sa.UniqueConstraint("draft_id", "rev", name="uq_draft_revisions_draft_id_rev"),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name="fk_draft_revisions_draft_id_drafts"
        ),
        sa.ForeignKeyConstraint(
            ["footer_snapshot_id"],
            ["footer_snapshots.id"],
            name="fk_draft_revisions_footer_snapshot_id_footer_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["cited_signal_id"], ["signals.id"], name="fk_draft_revisions_cited_signal_id_signals"
        ),
        sa.ForeignKeyConstraint(
            ["route_tuple_id"],
            ["ai_route_tuples.id"],
            name="fk_draft_revisions_route_tuple_id_ai_route_tuples",
        ),
        sa.CheckConstraint(_one_of("origin", ORIGINS), name=op.f("ck_draft_revisions_origin")),
        sa.CheckConstraint("rev >= 1", name=op.f("ck_draft_revisions_rev")),
        sa.CheckConstraint(
            "(rev = 1) = (origin = 'generated')", name=op.f("ck_draft_revisions_first")
        ),
        sa.CheckConstraint(_hex64("text_hash"), name=op.f("ck_draft_revisions_text_hash")),
        sa.CheckConstraint(
            f"playbook_sha256 IS NULL OR ({_hex64('playbook_sha256')})",
            name=op.f("ck_draft_revisions_playbook_sha256"),
        ),
        sa.CheckConstraint(
            "instruction IS NULL OR (origin = 'regenerated' AND length(instruction) <= 200)",
            name=op.f("ck_draft_revisions_instruction"),
        ),
        sa.CheckConstraint(
            _json_list("evidence_ids"), name=op.f("ck_draft_revisions_evidence_ids")
        ),
        sa.CheckConstraint(
            _json_list("fact_versions"), name=op.f("ck_draft_revisions_fact_versions")
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_draft_revisions_no_delete BEFORE DELETE ON draft_revisions "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_draft_revisions_insert_new BEFORE INSERT ON draft_revisions "
        "WHEN EXISTS (SELECT 1 FROM draft_revisions WHERE id = NEW.id "
        "OR (draft_id = NEW.draft_id AND rev = NEW.rev)) "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    redactions = " OR ".join(_redaction_only(column) for column in REDACTABLE)
    op.execute(
        "CREATE TRIGGER trg_draft_revisions_immutable BEFORE UPDATE ON draft_revisions "
        f"WHEN {_changed(FIXED)} OR {redactions} "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
