"""The ``playbook`` service's tables (FR-71, AD-8, AD-12, AD-18; migration ``0012_2_8_ai_routes``).

``prompt_snapshots`` holds the text of every AI prompt version ever stored, written only by
``nextron.db.repos.playbook.snapshots``: ``route``, ``prompt_version`` (``v1`` to ``v9999``,
``ck_prompt_snapshots_prompt_version``), ``sha256`` (64 lowercase hex digits,
``ck_prompt_snapshots_sha256``), ``text`` and ``created_at``. ``(route, prompt_version)`` is
unique, so a used prompt version never changes; ``(route, prompt_version, sha256)`` is unique
too, as the target of the activations' foreign key. A row is never updated or deleted.

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

Prompts are the engine's own text and an activation holds codes and times, so every column is
PII class ``none``.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
