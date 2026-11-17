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
- ``restore_test(tests)`` (Story 1.18): the ``restore_test`` job's handler. ``tests`` returns
  the ``ops`` service's ``RestoreTests`` (``nextron.main`` passes ``lambda: restore_tests``,
  which breaks the cycle registry, handler, ``RestoreTests``, queue, registry). The kind is
  ``job.payload.get("kind", "monthly")`` (a trigger's tick payload has none: the daily check of
  the monthly test); an unknown kind raises ``InvalidJob``. Otherwise it calls
  ``tests().run(kind)`` once and returns ``None`` (a monthly test that isn't due returns
  without a row).
- ``resolve_contact_markets(markets)`` (Story 3.2): the one-off ``resolve_contact_markets``
  job's handler. ``markets`` returns the ``research`` service's ``ContactMarkets``
  (``nextron.main`` passes ``lambda: contact_markets``, which breaks the cycle registry,
  handler, ``ContactMarkets``, queue, registry); the handler calls ``markets().backfill()``
  once and returns ``None``. The payload (``{"v": 1}``) holds nothing else.
"""

from collections.abc import Callable
from typing import Final, Protocol

from nextron.domain.backup import JOB_KINDS, KIND_FIELD, BackupKind
from nextron.domain.job import Handler, InvalidJob, Job
from nextron.domain.restore_test import RestoreTestKind

ALERT_ID_FIELD: Final = "alert_id"


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


class RestoreTesting(Protocol):
    """What ``restore_test`` needs of the ``ops`` service's ``RestoreTests``."""

    def run(self, kind: RestoreTestKind) -> str | None: ...


def restore_test(tests: Callable[[], RestoreTesting]) -> Handler:
    """The ``restore_test`` job's handler: run the payload's monthly or manual restore test
    through ``tests()``."""

    def handle_restore_test(job: Job) -> None:
        value = job.payload.get(KIND_FIELD, str(RestoreTestKind.MONTHLY))
        kind = next((kind for kind in RestoreTestKind if kind.value == value), None)
        if kind is None:
            raise InvalidJob("restore_test: the kind field must be monthly or manual")
        tests().run(kind)

    return handle_restore_test


class MarketBackfilling(Protocol):
    """What ``resolve_contact_markets`` needs of the ``research`` service's ``ContactMarkets``."""

    def backfill(self) -> int: ...


def resolve_contact_markets(markets: Callable[[], MarketBackfilling]) -> Handler:
    """The ``resolve_contact_markets`` job's handler: resolve the Contacts stored before Story
    3.2 through ``markets()``."""

    def handle_resolve_contact_markets(job: Job) -> None:
        del job  # the payload holds only its version
        markets().backfill()

    return handle_resolve_contact_markets
