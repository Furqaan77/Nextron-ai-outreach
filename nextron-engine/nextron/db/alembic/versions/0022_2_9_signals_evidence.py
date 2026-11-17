"""Story 2.9: the ``evidence``, ``signals``, ``signal_evidence`` and ``extraction_runs`` tables and
the Lead's headquarters zone (FR-10, FR-11, FR-57, AD-7, AD-11, AD-18, NFR-24), all owned by
``research``.

Creates ``evidence``, one immutable observation: a ULID ``id``; ``lead_id``
(``fk_evidence_lead_id_leads``, indexed by ``ix_evidence_lead_id``); ``type``
(``ck_evidence_type``: ``quote``, ``absence`` or ``count``); the nullable ``quote`` (normalised,
1 to 300 characters), ``sought`` (1 to 300) and ``number`` (0 or more); the nullable ``fact``
(``ck_evidence_fact``: a profile-fact or size-clue kind) and ``value`` (1 to 200);
``pages_checked`` (a JSON array of URLs); ``source`` (``ck_evidence_source``: ``website``,
``ats``, ``registry``, ``signal_inbox``, ``csv``, ``referral`` or ``operator``); the nullable
``source_url``; ``retrieved_at``; ``extracted_by`` (``ck_evidence_extracted_by``: ``rule``,
``operator`` or ``ai:<route>@<version>``); the nullable ``person_contact_id``
(``fk_evidence_person_contact_id_contacts``), ``superseded_by``
(``fk_evidence_superseded_by_evidence``) and ``retracted_at``; ``last_confirmed_at`` and
``created_at``. The shape checks: ``ck_evidence_quote_shape`` (a quote has a quote and no
``sought``), ``ck_evidence_sought_shape`` (an absence or count has a ``sought`` and no quote),
``ck_evidence_number_shape`` (a count has a number, an absence none), ``ck_evidence_value_shape``
(a profile fact has a value, and only a profile fact has one), ``ck_evidence_size_number`` (a
size fact has a number), ``ck_evidence_lengths``, ``ck_evidence_number`` and
``ck_evidence_not_superseded_and_retracted`` (``superseded_by`` and ``retracted_at`` are never
both set). Two triggers, after ``company_facts`` (migration 0017): ``trg_evidence_no_delete``
aborts any ``DELETE``, and ``trg_evidence_immutable`` aborts an ``UPDATE`` that changes any
column but ``lead_id``, ``superseded_by``, ``retracted_at`` and ``last_confirmed_at``, or that
changes ``superseded_by`` or ``retracted_at`` once it is set.

Creates ``signals``: a ULID ``id``; ``lead_id`` (``fk_signals_lead_id_leads``, indexed by
``ix_signals_lead_id``); ``signal_type`` (64); ``signal_class`` (``ck_signals_signal_class``:
``event`` or ``attribute``); ``tier`` (``ck_signals_tier``: 1 to 3); ``score_only``; the nullable
``event_date`` (a date, an Event's only: ``ck_signals_event_date``); ``first_seen_at`` and the
nullable ``retracted_at``.

Creates ``signal_evidence``: a ULID ``id``, ``signal_id`` (``fk_signal_evidence_signal_id_signals``)
and ``evidence_id`` (``fk_signal_evidence_evidence_id_evidence``), unique together
(``uq_signal_evidence_signal_id_evidence_id``) and indexed on ``evidence_id``
(``ix_signal_evidence_evidence_id``).

Creates ``extraction_runs``: a ULID ``id``; ``lead_id`` (``fk_extraction_runs_lead_id_leads``,
indexed by ``ix_extraction_runs_lead_id``); the nullable ``crawl_run_id``
(``fk_extraction_runs_crawl_run_id_crawl_runs``); ``route``; the nullable ``actor``;
``outcome`` (``ck_extraction_runs_outcome``); the nullable ``error_code``; the counts
``pages_sent``, ``pages_truncated``, ``stored`` and ``discarded`` (``ck_extraction_runs_counts``:
none negative); ``started_at`` and ``ended_at``.

Adds to ``leads`` (the profile group) the nullable ``hq_tz`` (64) and ``hq_evidence_id`` (a
ULID with no foreign key, which would make a ``leads``/``evidence`` cycle), with
``batch_alter_table`` and ``add_column`` only: SQLite adds them in place, without a rebuild, and
no CHECK ties them (code keeps them NULL together).

No foreign key cascades.

Revision ID: 0022_2_9_signals_evidence
Revises: 0021_4_2_campaigns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_2_9_signals_evidence"
down_revision: str | None = "0021_4_2_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE = "Evidence is immutable"
PROFILE_FACTS = "('what_we_do','location','technology')"
SIZE_FACTS = "('lawyers_at_office','lawyers_firm_wide','staff','group_staff','locations')"
# Every column but lead_id (a merge moves it), superseded_by and retracted_at (set once, from
# NULL) and last_confirmed_at (each re-observation).
FIXED_COLUMNS = (
    "id",
    "type",
    "quote",
    "sought",
    "number",
    "fact",
    "value",
    "pages_checked",
    "source",
    "source_url",
    "retrieved_at",
    "extracted_by",
    "person_contact_id",
    "created_at",
)


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("quote", sa.String(300), nullable=True),
        sa.Column("sought", sa.String(300), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("fact", sa.String(32), nullable=True),
        sa.Column("value", sa.String(200), nullable=True),
        sa.Column("pages_checked", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("retrieved_at", sa.String(27), nullable=False),
        sa.Column("extracted_by", sa.String(128), nullable=False),
        sa.Column("person_contact_id", sa.String(26), nullable=True),
        sa.Column("superseded_by", sa.String(26), nullable=True),
        sa.Column("retracted_at", sa.String(27), nullable=True),
        sa.Column("last_confirmed_at", sa.String(27), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_evidence_lead_id_leads"),
        sa.ForeignKeyConstraint(
            ["person_contact_id"],
            ["contacts.id"],
            name="fk_evidence_person_contact_id_contacts",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"], ["evidence.id"], name="fk_evidence_superseded_by_evidence"
        ),
        sa.CheckConstraint(
            "type IN ('quote','absence','count')", name=op.f("ck_evidence_type")
        ),
        sa.CheckConstraint(
            "source IN ('website','ats','registry','signal_inbox','csv','referral','operator')",
            name=op.f("ck_evidence_source"),
        ),
        sa.CheckConstraint(
            f"fact IS NULL OR fact IN {PROFILE_FACTS} OR fact IN {SIZE_FACTS}",
            name=op.f("ck_evidence_fact"),
        ),
        sa.CheckConstraint(
            "extracted_by IN ('rule','operator') OR extracted_by GLOB 'ai:?*@?*'",
            name=op.f("ck_evidence_extracted_by"),
        ),
        sa.CheckConstraint(
            "type <> 'quote' OR (quote IS NOT NULL AND sought IS NULL)",
            name=op.f("ck_evidence_quote_shape"),
        ),
        sa.CheckConstraint(
            "type = 'quote' OR (sought IS NOT NULL AND quote IS NULL)",
            name=op.f("ck_evidence_sought_shape"),
        ),
        sa.CheckConstraint(
            "(type <> 'count' OR number IS NOT NULL) AND (type <> 'absence' OR number IS NULL)",
            name=op.f("ck_evidence_number_shape"),
        ),
        sa.CheckConstraint(
            f"(value IS NOT NULL) = (fact IS NOT NULL AND fact IN {PROFILE_FACTS})",
            name=op.f("ck_evidence_value_shape"),
        ),
        sa.CheckConstraint(
            f"fact IS NULL OR fact NOT IN {SIZE_FACTS} OR number IS NOT NULL",
            name=op.f("ck_evidence_size_number"),
        ),
        sa.CheckConstraint(
            "(quote IS NULL OR length(quote) BETWEEN 1 AND 300) "
            "AND (sought IS NULL OR length(sought) BETWEEN 1 AND 300) "
            "AND (value IS NULL OR length(value) BETWEEN 1 AND 200)",
            name=op.f("ck_evidence_lengths"),
        ),
        sa.CheckConstraint("number IS NULL OR number >= 0", name=op.f("ck_evidence_number")),
        sa.CheckConstraint(
            "superseded_by IS NULL OR retracted_at IS NULL",
            name=op.f("ck_evidence_not_superseded_and_retracted"),
        ),
    )
    op.create_index("ix_evidence_lead_id", "evidence", ["lead_id"])
    op.execute(
        "CREATE TRIGGER trg_evidence_no_delete BEFORE DELETE ON evidence "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in FIXED_COLUMNS)
    op.execute(
        "CREATE TRIGGER trg_evidence_immutable BEFORE UPDATE ON evidence "
        f"WHEN {changed} "
        "OR (OLD.superseded_by IS NOT NULL AND NEW.superseded_by IS NOT OLD.superseded_by) "
        "OR (OLD.retracted_at IS NOT NULL AND NEW.retracted_at IS NOT OLD.retracted_at) "
        f"BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("signal_class", sa.String(16), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("score_only", sa.Boolean(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("first_seen_at", sa.String(27), nullable=False),
        sa.Column("retracted_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_signals"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_signals_lead_id_leads"),
        sa.CheckConstraint(
            "signal_class IN ('event','attribute')", name=op.f("ck_signals_signal_class")
        ),
        sa.CheckConstraint("tier BETWEEN 1 AND 3", name=op.f("ck_signals_tier")),
        sa.CheckConstraint(
            "event_date IS NULL OR signal_class = 'event'", name=op.f("ck_signals_event_date")
        ),
    )
    op.create_index("ix_signals_lead_id", "signals", ["lead_id"])
    op.create_table(
        "signal_evidence",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("signal_id", sa.String(26), nullable=False),
        sa.Column("evidence_id", sa.String(26), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_signal_evidence"),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.id"], name="fk_signal_evidence_signal_id_signals"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="fk_signal_evidence_evidence_id_evidence"
        ),
        sa.UniqueConstraint(
            "signal_id", "evidence_id", name="uq_signal_evidence_signal_id_evidence_id"
        ),
    )
    op.create_index("ix_signal_evidence_evidence_id", "signal_evidence", ["evidence_id"])
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("crawl_run_id", sa.String(26), nullable=True),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("pages_sent", sa.Integer(), nullable=False),
        sa.Column("pages_truncated", sa.Integer(), nullable=False),
        sa.Column("stored", sa.Integer(), nullable=False),
        sa.Column("discarded", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("ended_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_runs"),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_extraction_runs_lead_id_leads"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_extraction_runs_crawl_run_id_crawl_runs",
        ),
        sa.CheckConstraint(
            "outcome IN ('ok','refused','truncated','invalid','failed','no_activation',"
            "'no_pages','lead_merged')",
            name=op.f("ck_extraction_runs_outcome"),
        ),
        sa.CheckConstraint(
            "pages_sent >= 0 AND pages_truncated >= 0 AND stored >= 0 AND discarded >= 0",
            name=op.f("ck_extraction_runs_counts"),
        ),
    )
    op.create_index("ix_extraction_runs_lead_id", "extraction_runs", ["lead_id"])
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("hq_tz", sa.String(64), nullable=True))
        batch.add_column(sa.Column("hq_evidence_id", sa.String(26), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )
