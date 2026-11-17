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

The off-PC columns (Story 1.17, migration ``0008_1_17_offpc``, all ``NULL`` until the off-PC step
of a daily or manual backup records its outcome, once; a ``pre_migrate`` row keeps them
``NULL``):

- ``offpc_status``: ``copied``, ``failed`` or ``not_configured`` (``ck_backups_offpc_status``);
- ``offpc_name``: the encrypted copy's file name in the off-PC folder (never the folder);
- ``offpc_ledger``: ``copied`` or ``absent``, whether the erasure ledger went with it
  (``ck_backups_offpc_ledger``);
- ``offpc_at``: when the outcome was recorded; ``offpc_deleted_at``: when rotation deleted the
  off-PC copy;
- ``offpc_error``: why the copy failed, ``"<code>: <message>"``, redacted and at most 500
  characters; free text, so ``redact`` in the PII registry.

``ck_backups_offpc`` keeps that outcome whole: ``NULL`` with every off-PC column ``NULL``;
``copied`` on a verified row with a name, a ledger status and a time, and no error; ``failed``
on a verified row with an error and a time, and no name, ledger status or ``offpc_deleted_at``;
``not_configured`` on a verified row with a time only. The migration adds the three checks as
column constraints of ``offpc_error``; their text here is the same, character for character.

``restore_tests`` holds one row per restore test of an off-PC copy (Story 1.18), written once,
after the test, only by ``nextron.db.repos.ops.restore_tests`` (migration
``0009_1_18_restore_tests``):

- ``kind``: ``monthly`` or ``manual`` (``ck_restore_tests_kind``), and ``result``: ``pass`` or
  ``fail`` (``ck_restore_tests_result``);
- ``started_at`` and ``finished_at``; ``ix_restore_tests_started_at`` serves the newest-first
  reads;
- ``backup_id``: the ``backups`` row whose off-PC copy was tested, with no foreign key; ``NULL``
  when there was no copy to test;
- ``check_fetch``, ``check_integrity``, ``check_revision``, ``check_suppression`` and
  ``check_provenance``: each check's ``pass``, ``fail`` or ``skipped``
  (``ck_restore_tests_check_<name>``);
- ``reason``: the first failing check's reason code; ``copy_revision``: the copy's Alembic
  revision; ``suppression_count`` and ``provenance_count``: the copy's row counts, never negative
  (``ck_restore_tests_counts``);
- ``detail``: what failed, at most 500 characters; free text built from codes, revisions and
  counts, so ``redact`` in the PII registry.

``ck_restore_tests_outcome`` keeps the outcome whole: a ``pass`` has no reason and no detail, a
``backup_id``, ``pass`` for fetch, integrity and revision, and ``pass`` or ``skipped`` for both
counts; a ``fail`` has a reason and at least one check that is ``fail``.

``recovery_state`` holds one row per restore of a backup (Story 1.19), written only by
``nextron.db.repos.ops.recovery`` (migration ``0010_1_19_recovery``). ``nextron restore`` inserts
it into the restored copy before swapping the copy in; the engine then works through its steps:

- ``mode``: ``on`` (sending is held), ``off`` (every step settled) or ``superseded`` (a later
  restore replaced it) (``ck_recovery_state_mode``); the partial unique index
  ``uq_recovery_state_on`` allows at most one ``on`` row;
- ``restored_at``: when the restore ran (``ix_recovery_state_restored_at`` serves the
  newest-first read); ``backup_at``: when the restored copy was taken, at the latest (the steps
  re-ingest and re-poll from there); ``ended_at``: when recovery mode ended or was superseded;
- ``source_name``: the restored file's name, never a path; ``pre_restore_name``: the name of the
  safety copy of the database the restore replaced, ``NULL`` when there was none;
- one column per recovery step, named as the step (``reapply_erasure_ledger``,
  ``mark_oms_unknown``, ``reingest_mailboxes`` and ``repoll_unsubscribes``): ``pending``,
  ``done`` or ``not_applicable`` (``ck_recovery_state_<step>``).

``ck_recovery_state_outcome`` keeps the outcome whole: ``on`` has no ``ended_at``,
``superseded`` has one, and ``off`` has one and every step ``done`` or ``not_applicable``. It
holds no personal data.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

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
OFFPC_STATUS_CHECK: Final = (
    "offpc_status IS NULL OR offpc_status IN ('copied','failed','not_configured')"
)
OFFPC_LEDGER_CHECK: Final = "offpc_ledger IS NULL OR offpc_ledger IN ('copied','absent')"
OFFPC_CHECK: Final = (
    "(offpc_status IS NULL AND offpc_name IS NULL AND offpc_ledger IS NULL "
    "AND offpc_at IS NULL AND offpc_deleted_at IS NULL AND offpc_error IS NULL) "
    "OR (offpc_status IS 'copied' AND status = 'verified' AND offpc_name IS NOT NULL "
    "AND offpc_ledger IS NOT NULL AND offpc_at IS NOT NULL AND offpc_error IS NULL) "
    "OR (offpc_status IS 'failed' AND status = 'verified' AND offpc_error IS NOT NULL "
    "AND offpc_at IS NOT NULL AND offpc_name IS NULL AND offpc_ledger IS NULL "
    "AND offpc_deleted_at IS NULL) "
    "OR (offpc_status IS 'not_configured' AND status = 'verified' AND offpc_at IS NOT NULL "
    "AND offpc_name IS NULL AND offpc_ledger IS NULL AND offpc_deleted_at IS NULL "
    "AND offpc_error IS NULL)"
)
CHECK_VALUES: Final = "IN ('pass','fail','skipped')"
REASON_LENGTH: Final = 32
RESTORE_OUTCOME_CHECK: Final = (
    "(result = 'pass' AND reason IS NULL AND detail IS NULL AND backup_id IS NOT NULL "
    "AND check_fetch = 'pass' AND check_integrity = 'pass' AND check_revision = 'pass' "
    "AND check_suppression IN ('pass','skipped') AND check_provenance IN ('pass','skipped')) "
    "OR (result = 'fail' AND reason IS NOT NULL AND 'fail' IN (check_fetch, check_integrity, "
    "check_revision, check_suppression, check_provenance))"
)
NAME_LENGTH: Final = 255  # a file name, never a path
STEP_VALUES: Final = "IN ('pending','done','not_applicable')"
RECOVERY_ON_WHERE: Final = "mode = 'on'"
RECOVERY_OUTCOME_CHECK: Final = (
    "(mode = 'on' AND ended_at IS NULL) "
    "OR (mode = 'superseded' AND ended_at IS NOT NULL) "
    "OR (mode = 'off' AND ended_at IS NOT NULL "
    "AND reapply_erasure_ledger IN ('done','not_applicable') "
    "AND mark_oms_unknown IN ('done','not_applicable') "
    "AND reingest_mailboxes IN ('done','not_applicable') "
    "AND repoll_unsubscribes IN ('done','not_applicable'))"
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
        CheckConstraint(OFFPC_STATUS_CHECK, name="offpc_status"),
        CheckConstraint(OFFPC_LEDGER_CHECK, name="offpc_ledger"),
        CheckConstraint(OFFPC_CHECK, name="offpc"),
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
    offpc_status: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    offpc_name: Mapped[str | None] = mapped_column(Text)
    offpc_ledger: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    offpc_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    offpc_deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    offpc_error: Mapped[str | None] = mapped_column(String(ERROR_LENGTH))


class RestoreTestRecord(Base):
    """One restore test of an off-PC copy, and each check's outcome."""

    __tablename__ = "restore_tests"
    __table_args__ = (
        CheckConstraint("kind IN ('monthly','manual')", name="kind"),
        CheckConstraint("result IN ('pass','fail')", name="result"),
        CheckConstraint(f"check_fetch {CHECK_VALUES}", name="check_fetch"),
        CheckConstraint(f"check_integrity {CHECK_VALUES}", name="check_integrity"),
        CheckConstraint(f"check_revision {CHECK_VALUES}", name="check_revision"),
        CheckConstraint(f"check_suppression {CHECK_VALUES}", name="check_suppression"),
        CheckConstraint(f"check_provenance {CHECK_VALUES}", name="check_provenance"),
        CheckConstraint(RESTORE_OUTCOME_CHECK, name="outcome"),
        CheckConstraint("suppression_count >= 0 AND provenance_count >= 0", name="counts"),
        Index("ix_restore_tests_started_at", "started_at"),
    )

    id: Mapped[str] = ulid_pk()
    kind: Mapped[str] = mapped_column(String(CODE_LENGTH))
    result: Mapped[str] = mapped_column(String(CODE_LENGTH))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime] = mapped_column(UtcDateTime)
    backup_id: Mapped[str | None] = mapped_column(Ulid)
    check_fetch: Mapped[str] = mapped_column(String(CODE_LENGTH))
    check_integrity: Mapped[str] = mapped_column(String(CODE_LENGTH))
    check_revision: Mapped[str] = mapped_column(String(CODE_LENGTH))
    check_suppression: Mapped[str] = mapped_column(String(CODE_LENGTH))
    check_provenance: Mapped[str] = mapped_column(String(CODE_LENGTH))
    reason: Mapped[str | None] = mapped_column(String(REASON_LENGTH))
    copy_revision: Mapped[str | None] = mapped_column(String(REVISION_LENGTH))
    suppression_count: Mapped[int | None] = mapped_column(Integer)
    provenance_count: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(String(ERROR_LENGTH))


class RecoveryStateRecord(Base):
    """One restore of a backup and its recovery mode."""

    __tablename__ = "recovery_state"
    __table_args__ = (
        CheckConstraint("mode IN ('on','off','superseded')", name="mode"),
        CheckConstraint(f"reapply_erasure_ledger {STEP_VALUES}", name="reapply_erasure_ledger"),
        CheckConstraint(f"mark_oms_unknown {STEP_VALUES}", name="mark_oms_unknown"),
        CheckConstraint(f"reingest_mailboxes {STEP_VALUES}", name="reingest_mailboxes"),
        CheckConstraint(f"repoll_unsubscribes {STEP_VALUES}", name="repoll_unsubscribes"),
        CheckConstraint(RECOVERY_OUTCOME_CHECK, name="outcome"),
        Index("uq_recovery_state_on", "mode", unique=True, sqlite_where=text(RECOVERY_ON_WHERE)),
        Index("ix_recovery_state_restored_at", "restored_at"),
    )

    id: Mapped[str] = ulid_pk()
    mode: Mapped[str] = mapped_column(String(CODE_LENGTH))
    restored_at: Mapped[datetime] = mapped_column(UtcDateTime)
    backup_at: Mapped[datetime] = mapped_column(UtcDateTime)
    source_name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    pre_restore_name: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    reapply_erasure_ledger: Mapped[str] = mapped_column(String(CODE_LENGTH))
    mark_oms_unknown: Mapped[str] = mapped_column(String(CODE_LENGTH))
    reingest_mailboxes: Mapped[str] = mapped_column(String(CODE_LENGTH))
    repoll_unsubscribes: Mapped[str] = mapped_column(String(CODE_LENGTH))
