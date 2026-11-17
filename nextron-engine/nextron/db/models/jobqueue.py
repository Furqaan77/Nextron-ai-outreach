"""The ``jobqueue`` service's tables (AD-10, AD-17).

``jobs`` holds one row per durable job (``nextron.domain.job``), written only by
``nextron.db.repos.jobqueue.jobs`` (migration ``0003_1_10_jobs``):

- ``type``, ``lane``, ``priority`` and ``status``: the registered job type, the lane it runs on,
  its priority (higher runs first) and its status in the Job machine;
- ``payload``: the canonical JSON ``encode_payload`` returned: IDs and scalars only;
- ``idempotency_key``: ``<type>:<scope>:<discriminator>``. ``uq_jobs_live_key`` makes it unique
  among live jobs (``queued`` or ``running``), so a ``done`` or ``dead`` row never blocks a new
  one;
- ``concurrency_key``: ``inbox:<ULID>`` or ``domain:<name>``, or ``NULL``. A claim skips a job
  whose key a ``running`` job holds (``ix_jobs_running_concurrency``);
- ``run_after``: the earliest time it may be claimed; ``attempts``: how many times it was
  claimed;
- ``lease_until``: set exactly while it is ``running`` (``ck_jobs_lease``);
- ``last_error``: the latest failure as ``"<code>: <message>"``, redacted and at most 500
  characters. It is free text, so it is ``redact`` in the PII registry;
- ``created_at`` and ``updated_at``.

The checks hold the statuses, the lanes, ``attempts >= 0`` and the priority range in the
database too.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

TYPE_LENGTH = 48  # nextron.domain.job.MAX_JOB_TYPE_NAME
CODE_LENGTH = 16  # statuses and lanes
KEY_LENGTH = 128  # the longest key: a 48-character type, "inbox:<ULID>" and a 9-digit revision
CONCURRENCY_KEY_LENGTH = 260  # "domain:" and a 253-character name

LIVE_WHERE = "status IN ('queued','running')"
RUNNING_WHERE = "status = 'running'"


class JobRecord(Base):
    """One durable job."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_live_key",
            "idempotency_key",
            unique=True,
            sqlite_where=text(LIVE_WHERE),
        ),
        Index("ix_jobs_claim", "status", "lane", "priority", "run_after"),
        Index("ix_jobs_lease", "status", "lease_until"),
        Index("ix_jobs_type_status", "type", "status", "updated_at"),
        Index(
            "ix_jobs_running_concurrency",
            "concurrency_key",
            sqlite_where=text(RUNNING_WHERE),
        ),
        CheckConstraint("status IN ('queued','running','done','dead')", name="status"),
        CheckConstraint("lane IN ('realtime','interactive','bulk')", name="lane"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("priority BETWEEN 0 AND 100", name="priority"),
        CheckConstraint("(status = 'running') = (lease_until IS NOT NULL)", name="lease"),
    )

    id: Mapped[str] = ulid_pk()
    type: Mapped[str] = mapped_column(String(TYPE_LENGTH))
    lane: Mapped[str] = mapped_column(String(CODE_LENGTH))
    status: Mapped[str] = mapped_column(String(CODE_LENGTH))
    priority: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(KEY_LENGTH))
    concurrency_key: Mapped[str | None] = mapped_column(String(CONCURRENCY_KEY_LENGTH))
    run_after: Mapped[datetime] = mapped_column(UtcDateTime)
    attempts: Mapped[int] = mapped_column(Integer)
    lease_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
