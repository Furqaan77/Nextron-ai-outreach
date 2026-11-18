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
column of ``drafts`` holds no personal data: IDs, codes, numbers, flags and times.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.draft import DraftKind, DraftStatus

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
    # the status group (approval)
    status: Mapped[str] = mapped_column(
        String(STATUS_LENGTH), server_default=text(f"'{DraftStatus.READY}'")
    )
    reapproval_required: Mapped[bool] = _flag()
    event_expired: Mapped[bool] = _flag()
    skipped_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
