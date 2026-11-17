"""Read API of the ``jobqueue`` service (AD-3, AD-10).

Import rule: ``nextron.domain`` and ``nextron.db.repos.jobqueue`` only.

- ``job(job_id)``: the ``JobRow`` of the job (every column of its ``jobs`` row), or ``None``;
- ``dead_jobs()``: the ``JobRow`` of every ``dead`` job, oldest change first (the ``ops``
  sweep raises a ``job_dead`` alert for each one not yet alerted).

It reads through the current unit of work, read-only ones included.
"""

from nextron.db.repos.jobqueue import jobs as jobs_repo
from nextron.db.repos.jobqueue.jobs import JobRow


def job(job_id: str) -> JobRow | None:
    """The job ``job_id``, or ``None``."""
    return jobs_repo.get(job_id)


def dead_jobs() -> tuple[JobRow, ...]:
    """Every ``dead`` job, oldest change first."""
    return tuple(jobs_repo.dead())
