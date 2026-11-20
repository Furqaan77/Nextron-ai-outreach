"""The Message Batch tables (AD-8, INT-7, FR-57; Story 4.17, migration ``0065_4_17_ai_batches``),
shared by row owner and written only by ``nextron.db.ai_batches``. Neither holds personal data:
every column is an id, a code, a hash, a count or a time.

``ai_batches`` holds one row per submitted Message Batch: ``owner`` (``ck_ai_batches_owner``:
``drafting`` or ``research``), ``provider_batch_id`` (the API's id, unique), ``route`` and
``route_tuple_key`` (the tuple every item ran), ``status`` (``ck_ai_batches_status``:
``submitted``, ``ended`` or ``deleted``), ``submitted_at``, ``ended_at`` and ``deleted_at``
(``ck_ai_batches_times``: ``ended_at`` set once ended or deleted, ``deleted_at`` exactly when
deleted).

``ai_batch_items`` holds one row per queued request: ``owner`` (``ck_ai_batch_items_owner``),
``batch_row_id`` (the batch it went out in; NULL while ``pending`` and for an item settled
before any submit, never NULL once ``submitted``: ``ck_ai_batch_items_batch``), ``route``,
``custom_id`` (``<route>_<target>_<rev>``), ``target_id`` (the ULID the result is for),
``rev`` (``ck_ai_batch_items_rev``: 0 or more), ``context_id`` (a ULID the owner keeps, such as
the crawl run, or NULL), ``fingerprint`` (``nextron.domain.ai_batch.Fingerprint``'s canonical
JSON, NULL until submitted), ``status`` (``ck_ai_batch_items_status``: ``pending``,
``submitted``, ``applied``, ``stale``, ``rerun`` or ``failed``), ``created_at`` and
``updated_at``. ``(owner, route, target_id, rev)`` is unique, and so is ``(batch_row_id,
custom_id)``.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

OWNER_LENGTH: Final = 16
STATUS_LENGTH: Final = 16
CODE_LENGTH: Final = 64
KEY_LENGTH: Final = 128
PROVIDER_ID_LENGTH: Final = 128
CUSTOM_ID_LENGTH: Final = 64
OWNERS: Final = ("drafting", "research")
BATCH_STATUSES: Final = ("submitted", "ended", "deleted")
ITEM_STATUSES: Final = ("pending", "submitted", "applied", "stale", "rerun", "failed")


def one_of(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


BATCH_TIMES_CHECK: Final = (
    "(status = 'submitted' OR ended_at IS NOT NULL) "
    "AND ((status = 'deleted') = (deleted_at IS NOT NULL))"
)
ITEM_BATCH_CHECK: Final = (
    "(status <> 'pending' OR batch_row_id IS NULL) "
    "AND (status <> 'submitted' OR (batch_row_id IS NOT NULL AND fingerprint IS NOT NULL))"
)


class AiBatchRecord(Base):
    """One submitted Message Batch."""

    __tablename__ = "ai_batches"
    __table_args__ = (
        UniqueConstraint("provider_batch_id"),
        CheckConstraint(one_of("owner", OWNERS), name="owner"),
        CheckConstraint(one_of("status", BATCH_STATUSES), name="status"),
        CheckConstraint(BATCH_TIMES_CHECK, name="times"),
        Index("ix_ai_batches_owner_status", "owner", "status"),
    )

    id: Mapped[str] = ulid_pk()
    owner: Mapped[str] = mapped_column(String(OWNER_LENGTH))
    provider_batch_id: Mapped[str] = mapped_column(String(PROVIDER_ID_LENGTH))
    route: Mapped[str] = mapped_column(String(CODE_LENGTH))
    route_tuple_key: Mapped[str] = mapped_column(String(KEY_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    submitted_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class AiBatchItemRecord(Base):
    """One request queued for, or sent in, a Message Batch."""

    __tablename__ = "ai_batch_items"
    __table_args__ = (
        UniqueConstraint("owner", "route", "target_id", "rev"),
        UniqueConstraint("batch_row_id", "custom_id"),
        CheckConstraint(one_of("owner", OWNERS), name="owner"),
        CheckConstraint(one_of("status", ITEM_STATUSES), name="status"),
        CheckConstraint("rev >= 0", name="rev"),
        CheckConstraint(ITEM_BATCH_CHECK, name="batch"),
        Index("ix_ai_batch_items_owner_status", "owner", "status"),
        Index("ix_ai_batch_items_batch_row_id", "batch_row_id"),
    )

    id: Mapped[str] = ulid_pk()
    owner: Mapped[str] = mapped_column(String(OWNER_LENGTH))
    batch_row_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("ai_batches.id"))
    route: Mapped[str] = mapped_column(String(CODE_LENGTH))
    custom_id: Mapped[str] = mapped_column(String(CUSTOM_ID_LENGTH))
    target_id: Mapped[str] = mapped_column(Ulid)
    rev: Mapped[int] = mapped_column(Integer)
    context_id: Mapped[str | None] = mapped_column(Ulid)
    fingerprint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
