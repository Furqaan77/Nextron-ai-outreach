"""The ``drafting`` service's tables (AD-3, AD-7), and ``drafts``, shared with ``approval``.

``footer_snapshots`` (FR-23, Story 4.11; migration ``0023_4_11_footer_snapshots``) holds each
distinct footer text a Market's emails were composed with, written only by
``nextron.db.repos.drafting.footer_snapshots``: a ULID ``id``, the ``market_id`` (a foreign key
to ``markets.id``, no cascade), the ``sha256`` of the text (``nextron.domain.render.
footer_sha256``; ``ck_footer_snapshots_sha256``: 64 characters of ``0-9a-f``), the ``text``
(``ck_footer_snapshots_text``: not empty) and ``created_at``. Every column is NOT NULL, and a
(``market_id``, ``sha256``) pair is stored once (``uq_footer_snapshots_market_id_sha256``), so
identical content reuses its row. The repository has no update or delete, so a snapshot never
changes; there is no trigger, because erasure must be able to redact ``text``.

``text`` holds the sender's name and Nextron's postal address, like ``settings.value``, so it is
classed ``redact``; every other column holds no personal data.

``drafts`` (AD-4, AD-6, AD-21, Story 4.9; migration ``0042_4_9_drafts``) holds one row per
Draft. Its content group belongs to ``drafting`` (``nextron.db.repos.drafting.drafts``): a ULID
``id``; ``kind`` (``nextron.domain.draft.DraftKind``; ``ck_drafts_kind``); ``enrollment_id``
(a foreign key to ``enrollments.id``, no cascade); ``step_no`` (``ck_drafts_step_no``: at
least 1); ``day_offset``, the Step's Sequence day snapshotted when the slot was requested
(``ck_drafts_day_offset``: at least 0); ``current_rev`` (``ck_drafts_current_rev``: at least 1;
NULL until Story 4.12 writes a revision); ``due_at`` (only ever from
``nextron.domain.sending_policy.step_due_at``); ``generic`` and ``stale`` (server default 0);
``created_at`` and ``updated_at``. A ``step`` or ``recontact`` Draft has its ``enrollment_id``,
``step_no`` and ``day_offset`` (``ck_drafts_step_slot``), and an Enrollment has one slot per
Step (``uq_drafts_enrollment_step``, partial on those kinds). Its status group belongs to
``approval`` (``nextron.db.repos.approval.drafts``): ``status`` (``DraftStatus``;
``ck_drafts_status``; server default ``ready``, so ``drafting`` inserts at ``ready`` without
writing it), ``reapproval_required`` and ``event_expired`` (server default 0), ``skipped_at``
and ``status_at`` (when ``approval`` last moved the status). Indexes on ``due_at`` and
``enrollment_id``. Every
column of ``drafts`` holds no personal data: IDs, codes, numbers, flags and times. Since Story
4.12 (migration ``0045_4_12_draft_revisions``) (``id``, ``current_rev``) references
``draft_revisions`` (``draft_id``, ``rev``), so ``current_rev`` names an existing revision of the
same Draft or nothing.

``draft_revisions`` (FR-20, AD-6, Story 4.12) holds a Draft's text, one immutable row per
revision, written only by ``nextron.db.repos.drafting.revisions``: a ULID ``id``; ``draft_id``
(a foreign key to ``drafts.id``) and ``rev`` (at least 1), unique as a pair; ``subject`` and
``body`` (the editable text, without the footer); ``footer_snapshot_id`` (the footer it was
composed with); ``evidence_ids`` (a JSON list of Evidence ULIDs) and ``fact_versions`` (a JSON
list of ``[fact_id, version]`` pairs); the nullable ``cited_signal_id``, ``route_tuple_id``,
``playbook_sha256``, ``playbook_version``, ``prompt_version`` and ``instruction`` (a regenerate's
instruction, at most 200 characters); ``origin`` (``nextron.domain.draft.RevisionOrigin``;
revision 1 is exactly the ``generated`` one, the AI's original text); ``text_hash``
(``nextron.domain.render.text_hash`` of the composed message) and ``created_at``. Triggers
refuse a ``DELETE``, a replacing ``INSERT`` and any ``UPDATE`` except the erasure tombstone of
``subject``, ``body`` and ``instruction`` (to ``[erased]``). Those three are classed
``redact``; ``text_hash`` is ``keep_hash`` (already a hash); the rest hold no personal data.

``grounding_results`` (FR-21, AD-6, AD-18, Story 4.13; migration ``0046_4_13_grounding_results``)
holds one grounding result per revision, written only by
``nextron.db.repos.drafting.grounding_results``: a ULID ``id``; ``draft_id`` and ``rev``, unique
as a pair and a foreign key to that revision; ``status``
(``nextron.domain.grounding_rules.GroundingStatus``); ``flags`` (a JSON array of ``{rule, span,
explanation}``; ``fail`` exactly when it holds one); ``rules_version``; the nullable
``route_tuple_id`` and ``served_model`` (the AI check's, Story 4.14) and ``checked_at``. A
flag's ``span`` quotes Draft text, so ``flags`` is classed ``redact``: the erasure turns each
span into ``[erased]`` and keeps the rule, explanation and status. The rest hold no personal
data. Story 4.14 (migration ``0047_4_14_ai_grounding``) adds the nullable ``ai_checked_at``
(when the AI check's result was recorded; PII class ``none``), gives each flag a ``source``
(``rules`` or ``ai``), and ``ck_grounding_results_pass``: a ``pass`` row has a
``route_tuple_id``, a ``served_model`` and an ``ai_checked_at``.

Story 4.15 (migration ``0048_4_15_draft_failed``) adds two nullable columns to the ``drafts``
content group: ``draft_failed_at`` (when the last drafting attempt stored no revision) and
``draft_failed_reason`` (its ``nextron.domain.step_drafting.DraftFailure`` code, at most 32
characters); ``ck_drafts_draft_failed``: both NULL or both set, and the reason one of the codes.
Neither holds personal data.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from nextron.domain.draft import DraftKind, DraftStatus, RevisionOrigin
from nextron.domain.grounding_rules import GroundingStatus
from nextron.domain.step_drafting import MAX_REASON, DraftFailure

SHA256_LENGTH: Final = 64  # lowercase hex SHA-256
SHA256_CHECK: Final = "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'"
TEXT_CHECK: Final = "length(text) > 0"
KIND_LENGTH: Final = 16
STATUS_LENGTH: Final = 16
STEP_KINDS_WHERE: Final = "kind IN ('step','recontact')"


def _one_of(column: str, values: Iterable[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


KIND_CHECK: Final = _one_of("kind", DraftKind)
STATUS_CHECK: Final = _one_of("status", DraftStatus)
STEP_SLOT_CHECK: Final = (
    f"NOT ({STEP_KINDS_WHERE}) OR (enrollment_id IS NOT NULL AND step_no IS NOT NULL "
    "AND day_offset IS NOT NULL)"
)
STEP_NO_CHECK: Final = "step_no IS NULL OR step_no >= 1"
DAY_OFFSET_CHECK: Final = "day_offset IS NULL OR day_offset >= 0"
CURRENT_REV_CHECK: Final = "current_rev IS NULL OR current_rev >= 1"
DRAFT_FAILED_CHECK: Final = (
    "(draft_failed_at IS NULL) = (draft_failed_reason IS NULL) AND "
    f"(draft_failed_reason IS NULL OR {_one_of('draft_failed_reason', DraftFailure)})"
)
ORIGIN_LENGTH: Final = 16
PLAYBOOK_VERSION_LENGTH: Final = 32  # nextron.domain.playbook.VERSION_PATTERN
PROMPT_VERSION_LENGTH: Final = 5  # v1 to v9999
MAX_INSTRUCTION: Final = 200


def _hex64(column: str) -> str:
    return f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"


def _json_list(column: str) -> str:
    return f"json_valid({column}) AND json_type({column}) = 'array'"


ORIGIN_CHECK: Final = _one_of("origin", RevisionOrigin)
REV_CHECK: Final = "rev >= 1"
FIRST_CHECK: Final = "(rev = 1) = (origin = 'generated')"
TEXT_HASH_CHECK: Final = _hex64("text_hash")
PLAYBOOK_SHA256_CHECK: Final = f"playbook_sha256 IS NULL OR ({_hex64('playbook_sha256')})"
INSTRUCTION_CHECK: Final = (
    f"instruction IS NULL OR (origin = 'regenerated' AND length(instruction) <= "
    f"{MAX_INSTRUCTION})"
)
EVIDENCE_IDS_CHECK: Final = _json_list("evidence_ids")
FACT_VERSIONS_CHECK: Final = _json_list("fact_versions")


class FooterSnapshotRecord(Base):
    """One distinct footer text of one Market."""

    __tablename__ = "footer_snapshots"
    __table_args__ = (
        UniqueConstraint("market_id", "sha256"),
        CheckConstraint(SHA256_CHECK, name="sha256"),
        CheckConstraint(TEXT_CHECK, name="text"),
    )

    id: Mapped[str] = ulid_pk()
    market_id: Mapped[str] = mapped_column(Ulid, ForeignKey("markets.id"))
    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


def _flag() -> Mapped[bool]:
    return mapped_column(Boolean, server_default=text("0"))


class DraftRecord(Base):
    """One Draft: content group ``drafting``, status group ``approval``."""

    __tablename__ = "drafts"
    __table_args__ = (
        Index(
            "uq_drafts_enrollment_step",
            "enrollment_id",
            "step_no",
            unique=True,
            sqlite_where=text(STEP_KINDS_WHERE),
        ),
        Index("ix_drafts_due_at", "due_at"),
        Index("ix_drafts_enrollment_id", "enrollment_id"),
        CheckConstraint(KIND_CHECK, name="kind"),
        CheckConstraint(STATUS_CHECK, name="status"),
        CheckConstraint(STEP_SLOT_CHECK, name="step_slot"),
        CheckConstraint(STEP_NO_CHECK, name="step_no"),
        CheckConstraint(DAY_OFFSET_CHECK, name="day_offset"),
        CheckConstraint(CURRENT_REV_CHECK, name="current_rev"),
        CheckConstraint(DRAFT_FAILED_CHECK, name="draft_failed"),
        # Story 4.12: current_rev names an existing revision of this Draft
        ForeignKeyConstraint(
            ["id", "current_rev"],
            ["draft_revisions.draft_id", "draft_revisions.rev"],
            use_alter=True,
        ),
    )

    # the content group (drafting)
    id: Mapped[str] = ulid_pk()
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    enrollment_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("enrollments.id"))
    step_no: Mapped[int | None] = mapped_column(Integer)
    day_offset: Mapped[int | None] = mapped_column(Integer)
    current_rev: Mapped[int | None] = mapped_column(Integer)
    due_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    generic: Mapped[bool] = _flag()
    stale: Mapped[bool] = _flag()
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    draft_failed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # Story 4.15
    draft_failed_reason: Mapped[str | None] = mapped_column(String(MAX_REASON))
    # the status group (approval)
    status: Mapped[str] = mapped_column(
        String(STATUS_LENGTH), server_default=text(f"'{DraftStatus.READY}'")
    )
    reapproval_required: Mapped[bool] = _flag()
    event_expired: Mapped[bool] = _flag()
    skipped_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class DraftRevisionRecord(Base):
    """One immutable revision of a Draft's text."""

    __tablename__ = "draft_revisions"
    __table_args__ = (
        UniqueConstraint("draft_id", "rev"),
        CheckConstraint(ORIGIN_CHECK, name="origin"),
        CheckConstraint(REV_CHECK, name="rev"),
        CheckConstraint(FIRST_CHECK, name="first"),
        CheckConstraint(TEXT_HASH_CHECK, name="text_hash"),
        CheckConstraint(PLAYBOOK_SHA256_CHECK, name="playbook_sha256"),
        CheckConstraint(INSTRUCTION_CHECK, name="instruction"),
        CheckConstraint(EVIDENCE_IDS_CHECK, name="evidence_ids"),
        CheckConstraint(FACT_VERSIONS_CHECK, name="fact_versions"),
    )

    id: Mapped[str] = ulid_pk()
    draft_id: Mapped[str] = mapped_column(Ulid, ForeignKey("drafts.id"))
    rev: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    footer_snapshot_id: Mapped[str] = mapped_column(Ulid, ForeignKey("footer_snapshots.id"))
    evidence_ids: Mapped[str] = mapped_column(Text)
    fact_versions: Mapped[str] = mapped_column(Text)
    cited_signal_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("signals.id"))
    origin: Mapped[str] = mapped_column(String(ORIGIN_LENGTH))
    route_tuple_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("ai_route_tuples.id"))
    playbook_sha256: Mapped[str | None] = mapped_column(String(SHA256_LENGTH))
    playbook_version: Mapped[str | None] = mapped_column(String(PLAYBOOK_VERSION_LENGTH))
    prompt_version: Mapped[str | None] = mapped_column(String(PROMPT_VERSION_LENGTH))
    instruction: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


# Story 4.13 ---------------------------------------------------------------------------------------

GROUNDING_STATUS_LENGTH: Final = 16
SERVED_MODEL_LENGTH: Final = 64
GROUNDING_STATUS_CHECK: Final = _one_of("status", GroundingStatus)
GROUNDING_FLAGS_CHECK: Final = _json_list("flags")
GROUNDING_FAIL_CHECK: Final = "(status = 'fail') = (json_array_length(flags) > 0)"
RULES_VERSION_CHECK: Final = "rules_version >= 1"
# Story 4.14: a pass names the tuple, the served model and the AI check's time (fail closed)
GROUNDING_PASS_CHECK: Final = (
    "status != 'pass' OR (route_tuple_id IS NOT NULL AND served_model IS NOT NULL AND "
    "ai_checked_at IS NOT NULL)"
)


class GroundingResultRecord(Base):
    """The grounding result of one Draft revision."""

    __tablename__ = "grounding_results"
    __table_args__ = (
        UniqueConstraint("draft_id", "rev"),
        ForeignKeyConstraint(
            ["draft_id", "rev"], ["draft_revisions.draft_id", "draft_revisions.rev"]
        ),
        CheckConstraint(GROUNDING_STATUS_CHECK, name="status"),
        CheckConstraint(GROUNDING_FLAGS_CHECK, name="flags"),
        CheckConstraint(GROUNDING_FAIL_CHECK, name="fail"),
        CheckConstraint(RULES_VERSION_CHECK, name="rules_version"),
        CheckConstraint(GROUNDING_PASS_CHECK, name="pass"),
    )

    id: Mapped[str] = ulid_pk()
    draft_id: Mapped[str] = mapped_column(Ulid)
    rev: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(GROUNDING_STATUS_LENGTH))
    flags: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[int] = mapped_column(Integer)
    route_tuple_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("ai_route_tuples.id"))
    served_model: Mapped[str | None] = mapped_column(String(SERVED_MODEL_LENGTH))
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ai_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
