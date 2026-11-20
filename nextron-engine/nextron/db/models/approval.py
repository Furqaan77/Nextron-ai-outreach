"""The ``approval`` service's tables (AD-3, AD-4, AD-6; Story 5.4; migration
``0049_5_4_approvals``), written only by ``nextron.db.repos.approval``.

- ``approvals``: one append-only row per approval (FR-26): a ULID ``id``, the ``draft_id`` (a
  foreign key to ``drafts.id``, no cascade), the ``rev`` approved (``ck_approvals_rev``: at
  least 1), the ``text_hash`` it binds to (``domain.render.text_hash``;
  ``ck_approvals_text_hash``: 64 lowercase hex), ``approved_by`` (``ck_approvals_approved_by``:
  ``operator``), ``approved_at``, ``review_ms`` (``ck_approvals_review_ms``: NULL or at least 0)
  and ``edit_ratio`` against revision 1 (``ck_approvals_edit_ratio``: 0 to 1). Indexes
  ``ix_approvals_draft_id`` and ``ix_approvals_approved_at`` (a session's newest approval).
  Triggers (in the migration) refuse any ``UPDATE``, ``DELETE`` or
  replacing ``INSERT``.
- ``approval_sessions``: one row per Approval Queue session (AD-4 analytics): a ULID ``id``,
  ``started_at``, ``ended_at`` (NULL while open; ``ck_approval_sessions_ended``: not before
  ``started_at``), ``decisions`` and ``active_ms`` (both at least 0). At most one is open: the
  partial unique expression index ``uq_approval_sessions_open`` (on ``(ended_at IS NULL)`` where
  ``ended_at IS NULL``; a unique index on the NULL column itself wouldn't hold, since SQLite
  treats NULLs as distinct). SQLAlchemy can't reflect an expression index, so Alembic's drift
  comparison skips it (with a warning); the schema check compares its SQL.

No column holds personal data: ids, a hash of the message (the message itself lives in
``draft_revisions`` and ``outbound_messages``, which erasure redacts), numbers and times. Every
column is PII class ``none`` (``text_hash`` like ``outbound_messages.approved_hash``).
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

SHA256_LENGTH: Final = 64
ACTOR_LENGTH: Final = 16
OPERATOR: Final = "operator"

REV_CHECK: Final = "rev >= 1"
TEXT_HASH_CHECK: Final = "length(text_hash) = 64 AND text_hash NOT GLOB '*[^0-9a-f]*'"
APPROVED_BY_CHECK: Final = f"approved_by = '{OPERATOR}'"
REVIEW_MS_CHECK: Final = "review_ms IS NULL OR review_ms >= 0"
EDIT_RATIO_CHECK: Final = "edit_ratio >= 0 AND edit_ratio <= 1"
ENDED_CHECK: Final = "ended_at IS NULL OR ended_at >= started_at"
DECISIONS_CHECK: Final = "decisions >= 0"
ACTIVE_MS_CHECK: Final = "active_ms >= 0"
ONE_OPEN: Final = "uq_approval_sessions_open"
OPEN_EXPRESSION: Final = "(ended_at IS NULL)"  # the indexed expression: one value for every open row
OPEN_WHERE: Final = "ended_at IS NULL"


class ApprovalRecord(Base):
    """One approval of a Draft revision, bound to the hash of the message shown."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_draft_id", "draft_id"),
        Index("ix_approvals_approved_at", "approved_at"),
        CheckConstraint(REV_CHECK, name="rev"),
        CheckConstraint(TEXT_HASH_CHECK, name="text_hash"),
        CheckConstraint(APPROVED_BY_CHECK, name="approved_by"),
        CheckConstraint(REVIEW_MS_CHECK, name="review_ms"),
        CheckConstraint(EDIT_RATIO_CHECK, name="edit_ratio"),
    )

    id: Mapped[str] = ulid_pk()
    draft_id: Mapped[str] = mapped_column(Ulid, ForeignKey("drafts.id"))
    rev: Mapped[int] = mapped_column(Integer)
    text_hash: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    approved_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    approved_at: Mapped[datetime] = mapped_column(UtcDateTime)
    review_ms: Mapped[int | None] = mapped_column(Integer)
    edit_ratio: Mapped[float] = mapped_column(Float)


class ApprovalSessionRecord(Base):
    """One Approval Queue session: when it ran, how many decisions and their review time."""

    __tablename__ = "approval_sessions"
    __table_args__ = (
        Index(ONE_OPEN, text(OPEN_EXPRESSION), unique=True, sqlite_where=text(OPEN_WHERE)),
        CheckConstraint(ENDED_CHECK, name="ended"),
        CheckConstraint(DECISIONS_CHECK, name="decisions"),
        CheckConstraint(ACTIVE_MS_CHECK, name="active_ms"),
    )

    id: Mapped[str] = ulid_pk()
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    decisions: Mapped[int] = mapped_column(Integer)
    active_ms: Mapped[int] = mapped_column(Integer)
