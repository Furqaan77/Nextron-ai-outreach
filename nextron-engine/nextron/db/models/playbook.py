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
route. ``test_run_id`` has no foreign key yet: Epic 4 adds ``ai_test_runs``.

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

Prompts are the engine's own text and an activation holds codes and times; facts are claims
about Nextron, its work, its Offer and public regulation, never about a person. So no column
is personal data (PII class ``none``).
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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
    )

    id: Mapped[str] = ulid_pk()
    route: Mapped[str] = mapped_column(String(ROUTE_LENGTH))
    model: Mapped[str] = mapped_column(String(MODEL_LENGTH))
    effort: Mapped[str] = mapped_column(String(EFFORT_LENGTH))
    prompt_version: Mapped[str] = mapped_column(String(PROMPT_VERSION_LENGTH))
    prompt_sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    schema_version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    bootstrap: Mapped[bool] = mapped_column(Boolean)
    test_run_id: Mapped[str | None] = mapped_column(Ulid)
    activated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    deactivated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


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
