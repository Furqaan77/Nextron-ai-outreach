"""The ``playbook`` service's tables (FR-71, FR-72, AD-3, AD-7, AD-8, AD-12, AD-18).

``prompt_snapshots`` (Story 2.8, migration ``0014_2_8_ai_routes``) holds the text of every AI prompt
version ever stored, written only by ``nextron.db.repos.playbook.snapshots``: ``route``,
``prompt_version`` (``v1`` to ``v9999``, ``ck_prompt_snapshots_prompt_version``), ``sha256`` (64
lowercase hex digits, ``ck_prompt_snapshots_sha256``), ``text`` and ``created_at``. ``(route,
prompt_version)`` is unique, so a used prompt version never changes; ``(route, prompt_version,
sha256)`` is unique too, as the target of the activations' foreign key. A row is never updated or
deleted.

``ai_route_activations`` holds which tuple each route runs, written only by
``nextron.db.repos.playbook.activations``: the ``route``, ``model``, ``effort`` (one of the five
efforts, ``ck_ai_route_activations_effort``), ``prompt_version``, ``prompt_sha256`` and
``schema_version`` (1 to 9999, ``ck_ai_route_activations_schema_version``) of the tuple; the
``actor`` who activated it; ``bootstrap`` with a nullable ``test_run_id``
(``ck_ai_route_activations_tested``: a bootstrap activation has no test run, any other has one,
so Epic 4 can't activate an untested tuple without a schema change); ``activated_at``; and
``deactivated_at``, NULL while the activation is active. ``(route, prompt_version,
prompt_sha256)`` references the snapshot, so an activation always names stored text, and the
partial unique index ``uq_ai_route_activations_active_route`` allows one active activation per
route. Story 4.5 (migration ``0030_4_5_ai_test_runs``, which rebuilds the table again) gives
``test_run_id`` its foreign key to ``ai_test_runs.id``: the passing run that qualified a tested
activation. Story 4.4
(migration ``0026_4_4_playbook_snapshots``, which rebuilds the table) adds a nullable
``playbook_sha256`` after ``prompt_sha256``: the Sales Playbook snapshot the tuple runs under
(a foreign key to ``playbook_snapshots.sha256``; ``ck_ai_route_activations_playbook_sha256``:
NULL or 64 lowercase hex digits). A bootstrap activation never has one.

``playbook_snapshots`` (Story 4.4) holds every Sales Playbook text ever read, written only by
``nextron.db.repos.playbook.playbook_snapshots``: ``sha256`` (unique,
``uq_playbook_snapshots_sha256``; ``ck_playbook_snapshots_sha256``), the front matter's
``version`` (``ck_playbook_snapshots_version``: 1 to 32 letters, digits, dots, plus and minus
signs, starting with a letter or digit), the normalised ``content`` and ``captured_at``.

``ai_route_tuples`` (Story 4.4) holds every candidate tuple the sync has seen, active ones
included, written only by ``nextron.db.repos.playbook.route_tuples``: ``route``, ``model``,
``effort`` (``ck_ai_route_tuples_effort``), ``prompt_version``, ``prompt_sha256`` (with the
route and version, a foreign key to the prompt snapshot), a nullable ``playbook_sha256`` (a
foreign key to ``playbook_snapshots.sha256``; ``ck_ai_route_tuples_playbook_sha256``),
``schema_version`` (``ck_ai_route_tuples_schema_version``) and ``created_at``. Two partial
unique indexes keep one row per tuple: ``uq_ai_route_tuples_no_playbook`` on (``route``,
``model``, ``effort``, ``prompt_version``, ``schema_version``) where ``playbook_sha256`` is NULL,
and ``uq_ai_route_tuples_playbook`` on those columns and ``playbook_sha256`` where it isn't. Its
ULID is the tuple id Stories 4.5 and 4.12 name.

``playbook_checks`` (Story 4.4) holds one row per read of the Playbook file, written only by
``nextron.db.repos.playbook.playbook_checks``: ``checked_at``, ``trigger``
(``ck_playbook_checks_trigger``: ``startup`` or ``operator_check``), and either the
``playbook_sha256`` read (a foreign key to ``playbook_snapshots.sha256``) or the ``problem``
(``ck_playbook_checks_problem``: one of the five ``PlaybookProblem`` codes);
``ck_playbook_checks_outcome``: exactly one of the two is set. None of the three Story 4.4
tables is ever updated or deleted.

``ai_test_cases`` (Story 4.5) holds every AI Test Set case ever loaded, written only by
``nextron.db.repos.playbook.test_cases``: ``case_key``, ``test_set``
(``ck_ai_test_cases_test_set``: one of the four ``TestSet`` values), ``route``, the canonical
JSON ``input``, ``expected`` and ``covers``, the case's ``sha256`` (``ck_ai_test_cases_sha256``),
``loaded_at`` and ``retired_at`` (NULL while the case is live). The partial unique index
``uq_ai_test_cases_live_key`` keeps one live case per ``(test_set, case_key)``. A row is never
deleted, and only ``retired_at`` is ever set.

``ai_test_runs`` (Story 4.5) holds every run of a tuple's AI Test Set, written only by
``nextron.db.repos.playbook.test_runs``: the ``tuple_id`` (a foreign key to
``ai_route_tuples.id``), ``test_set``, ``outcome`` (``ck_ai_test_runs_outcome``: ``passed`` or
``failed``), ``error`` (``ck_ai_test_runs_error``: NULL or a ``RunError``;
``ck_ai_test_runs_error_when_failed``: only a failed run has one), ``case_count`` and
``passed_count`` (``ck_ai_test_runs_counts``, with ``cost_micros``), the canonical JSON
``metrics`` and ``results``, ``cost_micros``, ``started_at`` and ``finished_at``; indexed by
``(tuple_id, test_set)``. A row is never updated or deleted.

``company_facts`` holds the Approved Company Facts (FR-72, Story 4.3) as append-only versions,
written only by ``nextron.db.repos.playbook.company_facts`` (migration
``0017_4_3_company_facts``): one row per version, keyed (``fact_id``, ``version``)
(``uq_company_facts_fact_id_version``; ``ck_company_facts_version``: at least 1). Each row holds
the ``nextron.domain.company_facts.FactContent`` fields as typed columns: ``type``
(``ck_company_facts_type``: one of the five ``FactType`` values), ``wording``, ``source_url``,
``allowed_segment_ids`` and ``allowed_market_ids`` (canonical JSON sorted lists of
``segments.id`` and ``markets.id`` ULIDs), ``metric`` (NULL when none), ``metric_confirmed_at``
(the client's confirmation date, NULL when none), ``scope_note`` (NULL when none) and
``last_checked_on``; then ``created_by`` (``ck_company_facts_created_by``: ``seed`` or
``operator``), ``created_at`` and ``retracted_at`` (NULL until the fact is retracted). Every
other column is NOT NULL.

Three triggers keep it append-only (AD-7, the ``events`` precedent):
``trg_company_facts_no_delete`` aborts any ``DELETE``; ``trg_company_facts_no_id_reuse`` aborts
an ``INSERT`` whose ``id``, or whose (``fact_id``, ``version``), already exists (so ``INSERT OR
REPLACE`` can't replace a version); and ``trg_company_facts_immutable`` aborts an ``UPDATE``
that changes any column but ``retracted_at``, or changes ``retracted_at`` once it isn't NULL. A
later migration that rebuilds the table in batch mode must create them again.

Prompts and the Playbook are the engine's own text, activations, tuples, checks and runs hold
codes, hashes, counts and times, and test cases are synthetic (invented firms and people on
``.test`` domains); facts are claims
about Nextron, its work, its Offer and public regulation, never about a person. So no column
is personal data (PII class ``none``).
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.ai import Effort
from nextron.domain.ai_test_gate import RunError, RunOutcome, TestSet
from nextron.domain.playbook import PlaybookProblem, SyncTrigger

ROUTE_LENGTH: Final = 64  # nextron.domain.budget.CODE_PATTERN allows 64 characters
MODEL_LENGTH: Final = 64  # nextron.domain.ai.MODEL_NAME_PATTERN
PROMPT_VERSION_LENGTH: Final = 5  # v1 to v9999
SHA256_LENGTH: Final = 64
EFFORT_LENGTH: Final = 8
ACTOR_LENGTH: Final = 128  # nextron.db.eventlog.ACTOR_PATTERN: at most 100 characters

SHA256_CHECK: Final = "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'"
PROMPT_VERSION_CHECK: Final = (
    "length(prompt_version) BETWEEN 2 AND 5 AND prompt_version GLOB 'v[1-9]*' "
    "AND substr(prompt_version, 2) NOT GLOB '*[^0-9]*'"
)
EFFORT_CHECK: Final = "effort IN ({})".format(",".join(f"'{effort}'" for effort in Effort))
SCHEMA_VERSION_CHECK: Final = "schema_version BETWEEN 1 AND 9999"
TESTED_CHECK: Final = (
    "(bootstrap = 1 AND test_run_id IS NULL) OR (bootstrap = 0 AND test_run_id IS NOT NULL)"
)
ACTIVE_WHERE: Final = "deactivated_at IS NULL"

PLAYBOOK_VERSION_LENGTH: Final = 32  # nextron.domain.playbook.VERSION_PATTERN
TRIGGER_LENGTH: Final = 16
PROBLEM_LENGTH: Final = 16
PLAYBOOK_SHA256_CHECK: Final = (
    "playbook_sha256 IS NULL OR "
    "(length(playbook_sha256) = 64 AND playbook_sha256 NOT GLOB '*[^0-9a-f]*')"
)
PLAYBOOK_VERSION_CHECK: Final = (
    "length(version) BETWEEN 1 AND 32 AND version GLOB '[0-9A-Za-z]*' "
    "AND version NOT GLOB '*[^0-9A-Za-z.+-]*'"
)
TRIGGER_CHECK: Final = "trigger IN ({})".format(",".join(f"'{t}'" for t in SyncTrigger))
PROBLEM_CHECK: Final = "problem IS NULL OR problem IN ({})".format(
    ",".join(f"'{problem}'" for problem in PlaybookProblem)
)
OUTCOME_CHECK: Final = "(playbook_sha256 IS NULL) <> (problem IS NULL)"
NO_PLAYBOOK_WHERE: Final = "playbook_sha256 IS NULL"
PLAYBOOK_WHERE: Final = "playbook_sha256 IS NOT NULL"
TUPLE_COLUMNS: Final = ("route", "model", "effort", "prompt_version", "schema_version")

CASE_KEY_LENGTH: Final = 64  # nextron.domain.ai_test_cases.CASE_KEY_PATTERN
TEST_SET_LENGTH: Final = 16
OUTCOME_LENGTH: Final = 8
RUN_ERROR_LENGTH: Final = 16
TEST_SET_CHECK: Final = "test_set IN ({})".format(",".join(f"'{found}'" for found in TestSet))
RUN_OUTCOME_CHECK: Final = "outcome IN ({})".format(",".join(f"'{found}'" for found in RunOutcome))
RUN_ERROR_CHECK: Final = "error IS NULL OR error IN ({})".format(
    ",".join(f"'{found}'" for found in RunError)
)
ERROR_WHEN_FAILED_CHECK: Final = f"error IS NULL OR outcome = '{RunOutcome.FAILED}'"
RUN_COUNTS_CHECK: Final = (
    "case_count >= 0 AND passed_count >= 0 AND passed_count <= case_count "
    "AND cost_micros >= 0"
)
LIVE_WHERE: Final = "retired_at IS NULL"

FACT_ID_LENGTH: Final = 31  # nextron.domain.company_facts.FACT_ID_PATTERN
FACT_TYPE_LENGTH: Final = 16  # company, case_study, offer, regulation, statistic
METRIC_LENGTH: Final = 200  # nextron.domain.company_facts.MAX_METRIC
CREATED_BY_LENGTH: Final = 8  # seed, operator
FACT_TYPE_CHECK: Final = "type IN ('company', 'case_study', 'offer', 'regulation', 'statistic')"
CREATED_BY_CHECK: Final = "created_by IN ('seed', 'operator')"


class PromptSnapshotRecord(Base):
    """One stored prompt version's text."""

    __tablename__ = "prompt_snapshots"
    __table_args__ = (
        UniqueConstraint("route", "prompt_version"),
        UniqueConstraint("route", "prompt_version", "sha256"),
        CheckConstraint(SHA256_CHECK, name="sha256"),
        CheckConstraint(PROMPT_VERSION_CHECK, name="prompt_version"),
    )

    id: Mapped[str] = ulid_pk()
    route: Mapped[str] = mapped_column(String(ROUTE_LENGTH))
    prompt_version: Mapped[str] = mapped_column(String(PROMPT_VERSION_LENGTH))
    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class AiRouteActivationRecord(Base):
    """One activation of a route tuple; active while ``deactivated_at`` is NULL."""

    __tablename__ = "ai_route_activations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["route", "prompt_version", "prompt_sha256"],
            [
                "prompt_snapshots.route",
                "prompt_snapshots.prompt_version",
                "prompt_snapshots.sha256",
            ],
        ),
        Index(
            "uq_ai_route_activations_active_route",
            "route",
            unique=True,
            sqlite_where=text(ACTIVE_WHERE),
        ),
        CheckConstraint(EFFORT_CHECK, name="effort"),
        CheckConstraint(SCHEMA_VERSION_CHECK, name="schema_version"),
        CheckConstraint(TESTED_CHECK, name="tested"),
        CheckConstraint(PLAYBOOK_SHA256_CHECK, name="playbook_sha256"),
    )

    id: Mapped[str] = ulid_pk()
    route: Mapped[str] = mapped_column(String(ROUTE_LENGTH))
    model: Mapped[str] = mapped_column(String(MODEL_LENGTH))
    effort: Mapped[str] = mapped_column(String(EFFORT_LENGTH))
    prompt_version: Mapped[str] = mapped_column(String(PROMPT_VERSION_LENGTH))
    prompt_sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    playbook_sha256: Mapped[str | None] = mapped_column(
        String(SHA256_LENGTH), ForeignKey("playbook_snapshots.sha256")
    )
    schema_version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    bootstrap: Mapped[bool] = mapped_column(Boolean)
    test_run_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("ai_test_runs.id"))
    activated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    deactivated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class PlaybookSnapshotRecord(Base):
    """One stored Sales Playbook text, by its SHA-256."""

    __tablename__ = "playbook_snapshots"
    __table_args__ = (
        UniqueConstraint("sha256"),
        CheckConstraint(SHA256_CHECK, name="sha256"),
        CheckConstraint(PLAYBOOK_VERSION_CHECK, name="version"),
    )

    id: Mapped[str] = ulid_pk()
    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    version: Mapped[str] = mapped_column(String(PLAYBOOK_VERSION_LENGTH))
    content: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime)


class AiRouteTupleRecord(Base):
    """One candidate tuple of a route: what Story 4.5's test gate runs and activates."""

    __tablename__ = "ai_route_tuples"
    __table_args__ = (
        ForeignKeyConstraint(
            ["route", "prompt_version", "prompt_sha256"],
            [
                "prompt_snapshots.route",
                "prompt_snapshots.prompt_version",
                "prompt_snapshots.sha256",
            ],
        ),
        Index(
            "uq_ai_route_tuples_no_playbook",
            *TUPLE_COLUMNS,
            unique=True,
            sqlite_where=text(NO_PLAYBOOK_WHERE),
        ),
        Index(
            "uq_ai_route_tuples_playbook",
            *TUPLE_COLUMNS,
            "playbook_sha256",
            unique=True,
            sqlite_where=text(PLAYBOOK_WHERE),
        ),
        CheckConstraint(EFFORT_CHECK, name="effort"),
        CheckConstraint(SCHEMA_VERSION_CHECK, name="schema_version"),
        CheckConstraint(PLAYBOOK_SHA256_CHECK, name="playbook_sha256"),
    )

    id: Mapped[str] = ulid_pk()
    route: Mapped[str] = mapped_column(String(ROUTE_LENGTH))
    model: Mapped[str] = mapped_column(String(MODEL_LENGTH))
    effort: Mapped[str] = mapped_column(String(EFFORT_LENGTH))
    prompt_version: Mapped[str] = mapped_column(String(PROMPT_VERSION_LENGTH))
    prompt_sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    playbook_sha256: Mapped[str | None] = mapped_column(
        String(SHA256_LENGTH), ForeignKey("playbook_snapshots.sha256")
    )
    schema_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PlaybookCheckRecord(Base):
    """One read of the Sales Playbook file: the snapshot it gave, or why it gave none."""

    __tablename__ = "playbook_checks"
    __table_args__ = (
        CheckConstraint(TRIGGER_CHECK, name="trigger"),
        CheckConstraint(PROBLEM_CHECK, name="problem"),
        CheckConstraint(OUTCOME_CHECK, name="outcome"),
    )

    id: Mapped[str] = ulid_pk()
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime)
    trigger: Mapped[str] = mapped_column(String(TRIGGER_LENGTH))
    playbook_sha256: Mapped[str | None] = mapped_column(
        String(SHA256_LENGTH), ForeignKey("playbook_snapshots.sha256")
    )
    problem: Mapped[str | None] = mapped_column(String(PROBLEM_LENGTH))


class AiTestCaseRecord(Base):
    """One loaded AI Test Set case; live while ``retired_at`` is NULL."""

    __tablename__ = "ai_test_cases"
    __table_args__ = (
        Index(
            "uq_ai_test_cases_live_key",
            "test_set",
            "case_key",
            unique=True,
            sqlite_where=text(LIVE_WHERE),
        ),
        CheckConstraint(TEST_SET_CHECK, name="test_set"),
        CheckConstraint(SHA256_CHECK, name="sha256"),
    )

    id: Mapped[str] = ulid_pk()
    case_key: Mapped[str] = mapped_column(String(CASE_KEY_LENGTH))
    test_set: Mapped[str] = mapped_column(String(TEST_SET_LENGTH))
    route: Mapped[str] = mapped_column(String(ROUTE_LENGTH))
    input: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    covers: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    loaded_at: Mapped[datetime] = mapped_column(UtcDateTime)
    retired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class AiTestRunRecord(Base):
    """One run of a tuple's AI Test Set: its outcome, counts, metrics and case results."""

    __tablename__ = "ai_test_runs"
    __table_args__ = (
        Index("ix_ai_test_runs_tuple_id_test_set", "tuple_id", "test_set"),
        CheckConstraint(TEST_SET_CHECK, name="test_set"),
        CheckConstraint(RUN_OUTCOME_CHECK, name="outcome"),
        CheckConstraint(RUN_ERROR_CHECK, name="error"),
        CheckConstraint(ERROR_WHEN_FAILED_CHECK, name="error_when_failed"),
        CheckConstraint(RUN_COUNTS_CHECK, name="counts"),
    )

    id: Mapped[str] = ulid_pk()
    tuple_id: Mapped[str] = mapped_column(Ulid, ForeignKey("ai_route_tuples.id"))
    test_set: Mapped[str] = mapped_column(String(TEST_SET_LENGTH))
    outcome: Mapped[str] = mapped_column(String(OUTCOME_LENGTH))
    error: Mapped[str | None] = mapped_column(String(RUN_ERROR_LENGTH))
    case_count: Mapped[int] = mapped_column(Integer)
    passed_count: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[str] = mapped_column(Text)
    results: Mapped[str] = mapped_column(Text)
    cost_micros: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime] = mapped_column(UtcDateTime)


class CompanyFactRecord(Base):
    """One version of an Approved Company Fact."""

    __tablename__ = "company_facts"
    __table_args__ = (
        UniqueConstraint("fact_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(FACT_TYPE_CHECK, name="type"),
        CheckConstraint(CREATED_BY_CHECK, name="created_by"),
    )

    id: Mapped[str] = ulid_pk()
    fact_id: Mapped[str] = mapped_column(String(FACT_ID_LENGTH))
    version: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(FACT_TYPE_LENGTH))
    wording: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    allowed_segment_ids: Mapped[str] = mapped_column(Text)
    allowed_market_ids: Mapped[str] = mapped_column(Text)
    metric: Mapped[str | None] = mapped_column(String(METRIC_LENGTH))
    metric_confirmed_at: Mapped[date | None] = mapped_column(Date)
    scope_note: Mapped[str | None] = mapped_column(Text)
    last_checked_on: Mapped[date] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(CREATED_BY_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    retracted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
