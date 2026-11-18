"""Read API of the ``sending`` service (AD-3, AD-21, FR-32, FR-64).

Import rule: ``nextron.domain`` and ``nextron.db.repos.sending`` only.

The inbox reads return ``SendingInboxView(id, address, label, sending_domain_id, domain,
from_name, status, created_at)`` tuples, oldest first (by ``created_at``, then ``id``):

- ``inboxes()``: every Sending Inbox that isn't ``removed`` (the Sending Inboxes panel);
- ``inboxes_for_binding()``: only the inboxes at a ``BINDABLE_STATUSES`` status (``connecting``
  and ``active``): the inboxes a new Sequence may bind to (AD-21). Enrollment (Story 4.8) picks
  from these.

**Sending Domain authentication (Story 6.5).** A *listed* Sending Domain is one with at least
one inbox at a ``LISTED_STATUSES`` status (any but ``removed``); only those are checked and
reported.

- ``domain_auths(now)``: one ``DomainAuthView(sending_domain_id, domain, latest, verdict)`` per
  listed Sending Domain, sorted by domain. ``latest`` is its newest check as a
  ``DomainCheckView(id, checked_at, spf_ok, dkim_ok, dmarc_ok, aligned_ok, passed, reasons)``
  (``reasons`` decoded from ``details``; ``failing`` the items whose flag is false, in
  ``AuthItem`` order), or ``None``; ``verdict`` is ``domain_auth.auth_verdict(latest, now)``.
- ``domain_auth(sending_domain_id, now)``: the same view for one domain, or ``None`` when it
  isn't a listed Sending Domain (unknown, not a ULID, or every inbox removed). It is the send
  gate's read (Story 6.9): ``None`` means cold sends are blocked, so the gate fails closed.
  "Newest" is the last check recorded (``repos.sending.checks``), whatever its stamp.
- ``health_issues(now)``: one ``HealthIssue("domain_auth_failing", <id>, "<domain>: <verdict
  text>", detail=RECHECK_HINT)`` per listed Sending Domain whose verdict is blocked, by domain.

``cold_daily_caps(local_date) -> Mapping[str, int]`` (Story 4.8) is each bindable inbox's cold
Daily Cap on ``local_date`` (a calendar day in the ``sender_timezone``), by inbox id. It is a
placeholder until Epic 6's warm-up ramp: every bindable inbox has cap 0, so
``domain.sending_policy.pick_inbox`` falls back to the fewest live Enrollments. ``local_date``
must be a ``date`` (a ``datetime`` is refused with ``TypeError``).

Every read goes through the current unit of work, read-only ones included.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final

from nextron.db.repos.sending import checks as checks_repo
from nextron.db.repos.sending import domains as domains_repo
from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.repos.sending.checks import StoredCheck
from nextron.db.repos.sending.domains import StoredDomain
from nextron.db.repos.sending.inboxes import StoredInbox
from nextron.domain.domain_auth import (
    DOMAIN_AUTH_FAILING,
    RECHECK_HINT,
    AuthItem,
    AuthReason,
    AuthVerdict,
    auth_verdict,
    decode_details,
)
from nextron.domain.health import HealthIssue
from nextron.domain.ids import is_ulid
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


# Sending Domain authentication (Story 6.5) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainCheckView:
    """One stored domain check, its reasons decoded."""

    id: str
    checked_at: datetime
    spf_ok: bool
    dkim_ok: bool
    dmarc_ok: bool
    aligned_ok: bool
    passed: bool
    reasons: dict[AuthItem, AuthReason]

    @property
    def failing(self) -> tuple[AuthItem, ...]:
        """The items whose flag is false, in ``AuthItem`` order."""
        flags = {
            AuthItem.SPF: self.spf_ok,
            AuthItem.DKIM: self.dkim_ok,
            AuthItem.DMARC: self.dmarc_ok,
            AuthItem.ALIGNMENT: self.aligned_ok,
        }
        return tuple(item for item in AuthItem if not flags[item])


@dataclass(frozen=True, slots=True)
class DomainAuthView:
    """A listed Sending Domain, its newest check and its cold-send verdict."""

    sending_domain_id: str
    domain: str
    latest: DomainCheckView | None
    verdict: AuthVerdict


def _check_view(row: StoredCheck) -> DomainCheckView:
    return DomainCheckView(
        id=row.id,
        checked_at=row.checked_at,
        spf_ok=row.spf_ok,
        dkim_ok=row.dkim_ok,
        dmarc_ok=row.dmarc_ok,
        aligned_ok=row.aligned_ok,
        passed=row.passed,
        reasons=decode_details(row.details),
    )


def _auth_view(domain: StoredDomain, now: datetime) -> DomainAuthView:
    newest = checks_repo.newest(domain.id)
    latest = None if newest is None else _check_view(newest)
    return DomainAuthView(domain.id, domain.domain, latest, auth_verdict(latest, now))


def domain_auths(now: datetime) -> tuple[DomainAuthView, ...]:
    """Each listed Sending Domain's newest check and verdict, by domain."""
    return tuple(_auth_view(domain, now) for domain in domains_repo.with_inboxes(LISTED_STATUSES))


def domain_auth(sending_domain_id: str, now: datetime) -> DomainAuthView | None:
    """One listed Sending Domain's newest check and verdict, or ``None`` (unknown, not a ULID,
    or no listed inbox), which means cold sends from it are blocked: the send gate fails
    closed."""
    if not isinstance(sending_domain_id, str) or not is_ulid(sending_domain_id):
        return None
    domain = domains_repo.by_id(sending_domain_id)
    if domain is None or not inboxes_repo.addresses_of(domain.id, LISTED_STATUSES):
        return None
    return _auth_view(domain, now)


def health_issues(now: datetime) -> tuple[HealthIssue, ...]:
    """One ``domain_auth_failing`` issue per listed Sending Domain whose cold sends are
    blocked, by domain."""
    return tuple(
        HealthIssue(
            DOMAIN_AUTH_FAILING,
            view.sending_domain_id,
            f"{view.domain}: {view.verdict.text}",
            detail=RECHECK_HINT,
        )
        for view in domain_auths(now)
        if view.verdict.blocked
    )


def cold_daily_caps(local_date: date) -> Mapping[str, int]:
    """Each bindable inbox's cold Daily Cap on ``local_date``: 0 until Epic 6's ramp."""
    if isinstance(local_date, datetime) or not isinstance(local_date, date):
        raise TypeError("cold_daily_caps takes a date")
    return MappingProxyType({inbox.id: 0 for inbox in inboxes_for_binding()})
