"""Read API of the ``notify`` service (AD-3, AD-15, UX-DR5).

Import rule: ``nextron.domain`` and ``nextron.db.repos.notify`` only.

- ``alert(alert_id)``: the ``AlertRow`` of the alert (every column of its ``alerts`` row), or
  ``None``;
- ``subjects(kind)``: every subject that has an alert or a notification of ``kind``, so a caller
  can raise each subject's alert once (the ``job_dead`` sweep);
- ``open_notifications(limit)``: ``OpenNotifications(count, newest)``, the number of
  undismissed notifications and the newest ``limit`` of them, newest first (the dashboard's
  notification area);
- ``alerts_about(subject)`` (Story 3.14): every ``telegram`` alert whose subject is ``subject``
  (a Contact's ULID, for an erasure), of any kind and status, oldest first.

It reads through the current unit of work, read-only ones included.
"""

from dataclasses import dataclass

from nextron.db.repos.notify import alerts as alerts_repo
from nextron.db.repos.notify import notifications as notifications_repo
from nextron.db.repos.notify.alerts import AlertRow
from nextron.db.repos.notify.notifications import NotificationRow
from nextron.domain.alert import Channel


@dataclass(frozen=True, slots=True)
class OpenNotifications:
    """How many notifications are undismissed, and the newest of them, newest first."""

    count: int
    newest: tuple[NotificationRow, ...]


def alert(alert_id: str) -> AlertRow | None:
    """The alert ``alert_id``, or ``None``."""
    return alerts_repo.get(alert_id)


def alerts_about(subject: str) -> tuple[AlertRow, ...]:
    """Every Telegram alert about ``subject``, oldest first."""
    return alerts_repo.about(subject, str(Channel.TELEGRAM))


def subjects(kind: str) -> frozenset[str]:
    """The subjects with an alert or a notification of ``kind``."""
    return frozenset(alerts_repo.subjects(kind) | notifications_repo.subjects(kind))


def open_notifications(limit: int) -> OpenNotifications:
    """The undismissed notifications' count and the newest ``limit`` of them."""
    return OpenNotifications(
        notifications_repo.open_count(), tuple(notifications_repo.newest_open(limit))
    )
