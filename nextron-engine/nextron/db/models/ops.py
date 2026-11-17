"""The ``ops`` service's tables (AD-2, AD-20).

``runner_heartbeat`` holds exactly one row, the engine's heartbeat, written only by
``nextron.db.repos.ops.heartbeat`` (migration ``0004_1_11_heartbeat``):

- ``id``: always ``HEARTBEAT_ID``, the all-zero ULID; ``ck_runner_heartbeat_singleton`` refuses
  any other, so the table can never hold a second row;
- ``pid``: the engine process that wrote the last beat (``ck_runner_heartbeat_pid``: above 0);
- ``started_at``: when that process first beat, which is the engine's start within a second;
- ``beat_at``: the last beat.

The supervisor reads the heartbeat file, not this row; the health panel (Story 1.15) reads the
row. It holds no personal data.

``backups`` holds one row per backup taken (Story 1.16), written once, after the copy, only by
``nextron.db.repos.ops.backups`` (migration ``0007_1_16_backups``):

- ``kind``: ``daily``, ``manual`` or ``pre_migrate`` (``ck_backups_kind``), and ``status``:
  ``verified`` or ``failed`` (``ck_backups_status``);
- ``started_at`` and ``finished_at``; ``ix_backups_started_at`` serves the newest-first reads;
- ``path``: the copy's absolute path;
- ``size_bytes``, ``sha256`` (64 hex characters, ``ck_backups_sha256``), ``db_revision`` (the
  copy's Alembic revision), ``suppression_count`` and ``provenance_count``: what the
  verification read. A count is ``NULL`` on a verified row when the table was absent from the
  copy; the size and the counts are never negative (``ck_backups_counts``);
- ``error``: why a failed backup failed, ``"<code>: <message>"``, redacted and at most 500
  characters; free text, so ``redact`` in the PII registry;
- ``local_deleted_at``: when rotation deleted the local copy.

``ck_backups_outcome`` keeps the outcome whole: a ``verified`` row has a size, a SHA-256 and a
revision and no error; a ``failed`` row has an error, no size, no SHA-256 and no
``local_deleted_at``.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

HEARTBEAT_ID: Final = "00000000000000000000000000"  # the one row's fixed id
CODE_LENGTH: Final = 16
SHA256_LENGTH: Final = 64
REVISION_LENGTH: Final = 64
ERROR_LENGTH: Final = 500  # nextron.domain.job.MAX_ERROR_LENGTH

BACKUP_OUTCOME_CHECK: Final = (
    "(status = 'verified' AND size_bytes IS NOT NULL AND sha256 IS NOT NULL "
    "AND db_revision IS NOT NULL AND error IS NULL) "
    "OR (status = 'failed' AND error IS NOT NULL AND size_bytes IS NULL "
    "AND sha256 IS NULL AND local_deleted_at IS NULL)"
)


class HeartbeatRecord(Base):
    """The engine's one heartbeat row."""

    __tablename__ = "runner_heartbeat"
    __table_args__ = (
        CheckConstraint(f"id = '{HEARTBEAT_ID}'", name="singleton"),
        CheckConstraint("pid > 0", name="pid"),
    )

    id: Mapped[str] = ulid_pk()
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    beat_at: Mapped[datetime] = mapped_column(UtcDateTime)


class BackupRecord(Base):
    """One backup: a verified copy, or a failed attempt."""

    __tablename__ = "backups"
    __table_args__ = (
        CheckConstraint("kind IN ('daily','manual','pre_migrate')", name="kind"),
        CheckConstraint("status IN ('verified','failed')", name="status"),
        CheckConstraint(BACKUP_OUTCOME_CHECK, name="outcome"),
        CheckConstraint(
            "size_bytes >= 0 AND suppression_count >= 0 AND provenance_count >= 0",
            name="counts",
        ),
        CheckConstraint("length(sha256) = 64", name="sha256"),
        Index("ix_backups_started_at", "started_at"),
    )

    id: Mapped[str] = ulid_pk()
    kind: Mapped[str] = mapped_column(String(CODE_LENGTH))
    status: Mapped[str] = mapped_column(String(CODE_LENGTH))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime] = mapped_column(UtcDateTime)
    path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(SHA256_LENGTH))
    db_revision: Mapped[str | None] = mapped_column(String(REVISION_LENGTH))
    suppression_count: Mapped[int | None] = mapped_column(Integer)
    provenance_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(ERROR_LENGTH))
    local_deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
