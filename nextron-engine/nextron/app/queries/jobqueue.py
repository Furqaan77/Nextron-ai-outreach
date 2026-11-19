"""Read API of the ``jobqueue`` service (AD-3, AD-10).

Import rule: ``nextron.domain`` and ``nextron.db.repos.jobqueue`` only.

- ``job(job_id)``: the ``JobRow`` of the job (every column of its ``jobs`` row), or ``None``;
- ``has_live_job(job_type, scope)`` (Story 8.7): whether a ``queued`` or ``running`` job of
  that type has that scope, under any discriminator (a deferred job is ``queued`` again);
- ``dead_jobs()``: the ``JobRow`` of every ``dead`` job, oldest change first (the ``ops``
  sweep raises a ``job_dead`` alert for each one not yet alerted);
- ``last_successes()``: each job type's last success (its latest ``done`` change), by type, as
  a read-only mapping; a type that never succeeded (or whose ``done`` rows were all purged) is
  absent. The health panel shows it;
- ``health_issues()``: jobqueue's health issues (``nextron.main.build_health_issues`` registers
  it as the ``jobqueue`` source): for each ``dead`` job, oldest change first,
  ``HealthIssue("job_dead", <job id>, job_dead_text(type, id, attempts), detail="Last error: "
  + redact(last_error))``, with ``detail`` ``None`` when the job has no ``last_error``. The
  error text was redacted when it was stored; it is redacted again here, because it is free
  text (``redact``-class in ``pii_registry``).

It reads through the current unit of work, read-only ones included.
"""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from nextron.db.repos.jobqueue import jobs as jobs_repo
from nextron.db.repos.jobqueue.jobs import JobRow
from nextron.domain.alert import JOB_DEAD, job_dead_text
from nextron.domain.health import HealthIssue
from nextron.domain.redaction import redact

LAST_ERROR_LEAD: Final = "Last error: "


def job(job_id: str) -> JobRow | None:
    """The job ``job_id``, or ``None``."""
    return jobs_repo.get(job_id)


def has_live_job(job_type: str, scope: str) -> bool:
    """Whether a live job of ``job_type`` has scope ``scope``, under any discriminator."""
    return jobs_repo.live_in_scope(job_type, scope)


def dead_jobs() -> tuple[JobRow, ...]:
    """Every ``dead`` job, oldest change first."""
    return tuple(jobs_repo.dead())


def last_successes() -> Mapping[str, datetime]:
    """Each job type's last success, by type; types that never succeeded are absent."""
    return MappingProxyType(jobs_repo.last_done())


def health_issues() -> tuple[HealthIssue, ...]:
    """One ``job_dead`` issue per ``dead`` job, oldest change first."""
    return tuple(
        HealthIssue(
            JOB_DEAD,
            row.id,
            job_dead_text(row.type, row.id, row.attempts),
            detail=None if row.last_error is None else LAST_ERROR_LEAD + redact(row.last_error),
        )
        for row in jobs_repo.dead()
    )
