"""Read API of the ``sending`` service (AD-3, AD-21, FR-32).

Import rule: ``nextron.domain`` and ``nextron.db.repos.sending`` only.

Both reads return ``SendingInboxView(id, address, label, sending_domain_id, domain, from_name,
status, created_at)`` tuples, oldest first (by ``created_at``, then ``id``):

- ``inboxes()``: every Sending Inbox that isn't ``removed`` (the Sending Inboxes panel);
- ``inboxes_for_binding()``: only the inboxes at a ``BINDABLE_STATUSES`` status (``connecting``
  and ``active``): the inboxes a new Sequence may bind to (AD-21). Enrollment (Story 4.8) picks
  from these.

Both read through the current unit of work, read-only ones included.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.repos.sending.inboxes import StoredInbox
from nextron.domain.sending_inbox import BINDABLE_STATUSES, TERMINAL_STATUSES, InboxStatus

LISTED_STATUSES: Final = tuple(
    status for status in InboxStatus if status not in TERMINAL_STATUSES
)


@dataclass(frozen=True, slots=True)
class SendingInboxView:
    """One Sending Inbox and its Sending Domain."""

    id: str
    address: str
    label: str
    sending_domain_id: str
    domain: str
    from_name: str
    status: InboxStatus
    created_at: datetime


def _view(row: StoredInbox) -> SendingInboxView:
    return SendingInboxView(
        id=row.id,
        address=row.address,
        label=row.label,
        sending_domain_id=row.sending_domain_id,
        domain=row.domain,
        from_name=row.from_name,
        status=InboxStatus(row.status),
        created_at=row.created_at,
    )


def inboxes() -> tuple[SendingInboxView, ...]:
    """Every Sending Inbox that isn't ``removed``, oldest first."""
    return tuple(_view(row) for row in inboxes_repo.listed(LISTED_STATUSES))


def inboxes_for_binding() -> tuple[SendingInboxView, ...]:
    """The Sending Inboxes a new Sequence may bind to (``BINDABLE_STATUSES``), oldest first."""
    return tuple(_view(row) for row in inboxes_repo.listed(BINDABLE_STATUSES))
