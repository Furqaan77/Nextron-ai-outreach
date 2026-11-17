"""Thin job handlers (AD-10): each calls one use case and returns ``None``, or the domain's
``DEFERRED`` when the budget refused the use case's work (the runner re-queues the job an hour
later without using a retry).

Import rule: the domain and the standard library only. A handler gets what it calls from
``nextron.main``, which builds the job type registry (``build_job_types``), so this module
never imports ``nextron.app`` or anything below it; the runner opens the unit of work the
handler runs in.

- ``send_alert(alerts)`` (Story 1.12): the ``send_alert`` job's handler. ``alerts`` returns
  the ``notify`` service (``nextron.main`` passes ``lambda: notify``, which breaks the cycle
  registry, handler, ``Notify``, queue, registry); the handler calls
  ``alerts().send(job.payload["alert_id"])`` once and returns ``None``.
- ``backup_db(backups)`` (Story 1.16): the ``backup_db`` job's handler. ``backups`` returns the
  ``ops`` service's ``Backups`` (``nextron.main`` passes ``lambda: backups``, which breaks the
  cycle registry, handler, ``Backups``, queue, registry). The kind is
  ``job.payload.get("kind", "daily")`` (a trigger's tick payload has none); an unknown kind, or
  ``pre_migrate`` (only ``serve`` takes those), raises ``InvalidJob``. Otherwise it calls
  ``backups().back_up(kind)`` once and returns ``None``.
- ``enrich_lead(crawls)`` (Story 2.5): calls ``crawls().enrich_lead(lead_id, may_retry=
  job.attempts <= ENRICH_LEAD_RETRIES)`` once (the research service's ``WebsiteCrawls``), so the
  job's last allowed attempt completes instead of failing on a flaky site, and returns
  ``DEFERRED`` when it returns ``False`` (the budget refused the crawl).
- ``canonicalize_lead(discovery)`` (Story 2.5): calls ``discovery().canonicalize_lead(lead_id,
  run_id)`` once.
- ``purge_page_text(research)`` (Story 2.5): calls ``research().purge_page_text()`` once.

A payload field that is missing or isn't a ULID raises ``InvalidJob`` naming the field.
"""

from collections.abc import Callable
from typing import Final, Protocol

from nextron.domain.backup import JOB_KINDS, KIND_FIELD, BackupKind
from nextron.domain.crawl import LEAD_ID_FIELD, RUN_ID_FIELD
from nextron.domain.ids import is_ulid
from nextron.domain.job import DEFERRED, Handler, HandlerResult, InvalidJob, Job

ALERT_ID_FIELD: Final = "alert_id"
ENRICH_LEAD_RETRIES: Final = 3  # the enrich_lead job type's retry limit (nextron.main)


def _ulid_field(job: Job, name: str) -> str:
    value = job.payload.get(name)
    if not isinstance(value, str) or not is_ulid(value):
        raise InvalidJob(f"{job.type}: the {name} field must be a ULID")
    return value


class AlertSending(Protocol):
    """What ``send_alert`` needs of the ``notify`` service."""

    def send(self, alert_id: str) -> None: ...


def send_alert(alerts: Callable[[], AlertSending]) -> Handler:
    """The ``send_alert`` job's handler: send the payload's alert through ``alerts()``."""

    def handle_send_alert(job: Job) -> None:
        alerts().send(str(job.payload[ALERT_ID_FIELD]))

    return handle_send_alert


class BackingUp(Protocol):
    """What ``backup_db`` needs of the ``ops`` service's ``Backups``."""

    def back_up(self, kind: BackupKind) -> str: ...


def backup_db(backups: Callable[[], BackingUp]) -> Handler:
    """The ``backup_db`` job's handler: take the payload's daily or manual backup through
    ``backups()``."""

    def handle_backup_db(job: Job) -> None:
        value = job.payload.get(KIND_FIELD, str(BackupKind.DAILY))
        kind = next((kind for kind in JOB_KINDS if kind.value == value), None)
        if kind is None:
            raise InvalidJob("backup_db: the kind field must be daily or manual")
        backups().back_up(kind)

    return handle_backup_db


class Crawling(Protocol):
    """What ``enrich_lead`` needs of the ``research`` service's ``WebsiteCrawls``."""

    def enrich_lead(self, lead_id: str, *, may_retry: bool) -> bool: ...


def enrich_lead(crawls: Callable[[], Crawling]) -> Handler:
    """The ``enrich_lead`` job's handler: crawl the payload's Lead through ``crawls()``."""

    def handle_enrich_lead(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        if not crawls().enrich_lead(lead_id, may_retry=job.attempts <= ENRICH_LEAD_RETRIES):
            return DEFERRED
        return None

    return handle_enrich_lead


class Canonicalizing(Protocol):
    """What ``canonicalize_lead`` needs of the ``discovery`` service."""

    def canonicalize_lead(self, lead_id: str, run_id: str) -> str: ...


def canonicalize_lead(discovery: Callable[[], Canonicalizing]) -> Handler:
    """The ``canonicalize_lead`` job's handler: move the payload's Lead to its run's domain
    through ``discovery()``."""

    def handle_canonicalize_lead(job: Job) -> None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        run_id = _ulid_field(job, RUN_ID_FIELD)
        discovery().canonicalize_lead(lead_id, run_id)

    return handle_canonicalize_lead


class PurgingPageText(Protocol):
    """What ``purge_page_text`` needs of the ``research`` service."""

    def purge_page_text(self) -> int: ...


def purge_page_text(research: Callable[[], PurgingPageText]) -> Handler:
    """The ``purge_page_text`` job's handler: null old page text through ``research()``."""

    def handle_purge_page_text(job: Job) -> None:
        research().purge_page_text()

    return handle_purge_page_text
