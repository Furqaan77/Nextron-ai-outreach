"""Read API of the ``sending`` service (AD-3, AD-21, FR-32, FR-64).

Import rule: ``nextron.domain`` and ``nextron.db.repos.sending``, plus one read of
``nextron.app.queries.setup`` (the ``total_daily_ceiling`` setting, Story 6.4).

The inbox reads return ``SendingInboxView(id, address, label, sending_domain_id, domain,
from_name, status, created_at, warmup_started_on, warmup_method, warm_level, warmup_tool_daily,
ramp_step, ramp_target, next_send_at, last_sent_at, status_changed_at, connected_at,
pause_cause)`` tuples, oldest first (by ``created_at``, then ``id``); ``.plan`` is the inbox's
``sending_policy.WarmupPlan``, and ``next_send_at`` and ``last_sent_at`` its cold-send pacing
(Story 6.11, each ``None`` until set). Story 6.2 adds ``status_changed_at``, ``connected_at``
(``None`` until connected) and ``pause_cause``: for a ``paused`` inbox the cause of its newest
``events`` row into ``paused`` (``repos.sending.inboxes.pause_causes``), else ``None``. Also
``address_registered(address)``: whether an inbox at any status holds it.

- ``inboxes()``: every Sending Inbox that isn't ``removed`` (the Sending Inboxes panel);
- ``inboxes_for_binding()``: only the inboxes at a ``BINDABLE_STATUSES`` status (``connecting``
  and ``active``): the inboxes a new Sequence may bind to (AD-21). Enrollment (Story 4.8) picks
  from these.

**Daily Caps (Story 6.4, FR-33, FR-34, AD-11).** ``local_date`` is the calendar date in
``sender_timezone`` (``sending_policy.local_date``); the caller passes it.

- ``ceiling()``: the ``total_daily_ceiling`` setting through ``queries.setup.get``; a stored
  text that no longer parses reads as 0, so cold sends stop rather than exceed it.
- ``cold_daily_caps(local_date) -> dict[str, int]``: every listed (non-``removed``) inbox id,
  oldest first, to ``sending_policy.cold_cap(plan, local_date, ceiling())`` for an ``active``
  inbox and 0 for any other status. The send gate (6.9) and Epic 4's capacity (4.8, 4.10) read
  it (Story 4.8 read a placeholder of 0 for every bindable inbox until this story).
  ``local_date`` must be a ``date`` (a ``datetime`` is refused with ``TypeError``).
- ``inbox_caps(local_date)``: one ``InboxCapView(inbox_id, warmup_days, daily_cap, tool_sends,
  cold_cap, cap_used, ceiling)`` per listed inbox, oldest first, for the Sending Inboxes panel:
  ``warmup_days`` (``None`` when unrecorded), the ramped ``daily_cap``, the warm-up tool sends
  counted toward the ceiling, the ``cold_cap`` (as ``cold_daily_caps``), ``cap_used`` (0 until
  Story 6.10 stores sent messages to count) and the ceiling.

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
- ``health_issues(now, postmaster_grant_missing=None)``: first ``inbox_health_issues()`` (Story
  6.2): one ``HealthIssue("inbox_paused", <inbox id>, "Sending Inbox <id> is paused
  (<cause>)", detail=RESUME_HINT)`` per ``paused`` inbox and one ``inbox_auth_failed`` (detail
  ``RECONNECT_HINT``) per ``auth_failed`` inbox, oldest first; then one
  ``HealthIssue("domain_auth_failing",
  <id>, "<domain>: <verdict text>", detail=RECHECK_HINT)`` per listed Sending Domain whose
  verdict is blocked, by domain; then the reputation issues (Story 6.6), below.

**Reputation (Story 6.6, FR-38, FR-58).** ``DomainCheckView`` also carries ``dnsbl`` (each
zone's ``ZoneOutcome``, decoded from ``dnsbl_result``), ``spam_rate`` (ppm) and ``spam_status``
(a ``SpamStatus``); all three are ``None`` for a check recorded before migration
``0047_6_6_reputation``. Its ``dnsbl_outcome`` is the worst outcome (``None`` without a result).
From each listed domain's newest check, ``health_issues`` adds, by domain:

- ``domain_blocklisted`` (``<domain>: blocklisted on <zones>; each check pauses its active
  inboxes``) for a ``listed`` outcome, or else ``dnsbl_query_refused`` (a warning: nothing was paused) for
  ``query_refused`` or ``unrecognised``;
- ``spam_rate_pause`` or ``spam_rate_high`` for a spam status ``pause`` or ``alert``;
- then one ``postmaster_grant_missing`` (``Postmaster grant missing``, subject ``postmaster``)
  when a Sending Domain is listed and ``postmaster_grant_missing()`` (``nextron.main``: whether
  ``GoogleAccess.check_stored_grant("postmaster")`` raises) is true. It is called only then.

**Sent counts (Story 6.9's fail-closed seams).** Both answer ``None`` (unknown) until Story 6.10
stores sent messages (and Story 6.8 syncs ``other_sent``), so the send gate's
``under_daily_cap`` and ``under_total_ceiling`` fail for every cold message:

- ``cold_sent_on(inbox_id, local_date) -> int | None``: the cold messages (``step`` and
  ``recontact``) the inbox sent on ``local_date`` (in ``sender_timezone``);
- ``sent_total_on(inbox_id, local_date) -> int | None``: every message the inbox sent on
  ``local_date``, any kind, without the warm-up tool sends (the gate adds
  ``sending_policy.tool_send_figure`` for those). The ceiling is per inbox (the
  ``total_daily_ceiling`` setting, "Total daily ceiling per inbox", Story 6.4).

Every read goes through the current unit of work, read-only ones included.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from nextron.app.queries import setup as setup_queries
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
from nextron.domain.reputation import (
    DELIST_HINT,
    DNSBL_QUERY_REFUSED,
    DOMAIN_BLOCKLISTED,
    POSTMASTER_GRANT_HINT,
    POSTMASTER_GRANT_MISSING,
    POSTMASTER_GRANT_TEXT,
    POSTMASTER_SUBJECT,
    REFUSED_HINT,
    SPAM_HINT,
    SPAM_RATE_HIGH,
    SPAM_RATE_PAUSE,
    SpamStatus,
    ZoneOutcome,
    decode_dnsbl,
    overall,
    spam_rate_text,
    zones_with,
)
from nextron.domain.sending_inbox import (
    BINDABLE_STATUSES,
    INBOX_AUTH_FAILED,
    INBOX_PAUSED,
    RECONNECT_HINT,
    RESUME_HINT,
    TERMINAL_STATUSES,
    InboxStatus,
    PauseCause,
)
from nextron.domain.sending_policy import WarmupPlan, cold_cap, daily_cap, tool_send_figure
from nextron.domain.sending_policy import warmup_days as count_warmup_days
from nextron.domain.settings_schema import InvalidSettingValue, Key

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
    warmup_started_on: date | None
    warmup_method: str | None
    warm_level: int | None
    warmup_tool_daily: int
    ramp_step: int
    ramp_target: int
    next_send_at: datetime | None = None
    last_sent_at: datetime | None = None
    status_changed_at: datetime | None = None
    connected_at: datetime | None = None
    pause_cause: PauseCause | None = None

    @property
    def plan(self) -> WarmupPlan:
        """The inbox's warm-up record and ramp."""
        return WarmupPlan(
            self.warmup_started_on,
            self.warm_level,
            self.warmup_tool_daily,
            self.ramp_step,
            self.ramp_target,
        )


def _pause_cause(text: str | None) -> PauseCause | None:
    try:
        return None if text is None else PauseCause(text)
    except ValueError:
        return None


def _views(rows: list[StoredInbox]) -> tuple[SendingInboxView, ...]:
    causes = inboxes_repo.pause_causes(row.id for row in rows if row.status == InboxStatus.PAUSED)
    return tuple(_view(row, _pause_cause(causes.get(row.id))) for row in rows)


def _view(row: StoredInbox, pause_cause: PauseCause | None = None) -> SendingInboxView:
    return SendingInboxView(
        id=row.id,
        address=row.address,
        label=row.label,
        sending_domain_id=row.sending_domain_id,
        domain=row.domain,
        from_name=row.from_name,
        status=InboxStatus(row.status),
        created_at=row.created_at,
        warmup_started_on=row.warmup_started_on,
        warmup_method=row.warmup_method,
        warm_level=row.warm_level,
        warmup_tool_daily=row.warmup_tool_daily,
        ramp_step=row.ramp_step,
        ramp_target=row.ramp_target,
        next_send_at=row.next_send_at,
        last_sent_at=row.last_sent_at,
        status_changed_at=row.status_changed_at,
        connected_at=row.connected_at,
        pause_cause=pause_cause,
    )


def inboxes() -> tuple[SendingInboxView, ...]:
    """Every Sending Inbox that isn't ``removed``, oldest first."""
    return _views(inboxes_repo.listed(LISTED_STATUSES))


def inboxes_for_binding() -> tuple[SendingInboxView, ...]:
    """The Sending Inboxes a new Sequence may bind to (``BINDABLE_STATUSES``), oldest first."""
    return _views(inboxes_repo.listed(BINDABLE_STATUSES))


def address_registered(address: str) -> bool:
    """Whether an inbox at any status, ``removed`` included, holds ``address`` (Story 6.2)."""
    return inboxes_repo.id_by_address(address) is not None


def inbox_health_issues() -> tuple[HealthIssue, ...]:
    """One ``inbox_paused`` issue (with its pause cause) per ``paused`` inbox and one
    ``inbox_auth_failed`` issue per ``auth_failed`` inbox, oldest first; inbox IDs only
    (Story 6.2)."""
    issues: list[HealthIssue] = []
    for inbox in inboxes():
        if inbox.status is InboxStatus.PAUSED:
            cause = "unknown cause" if inbox.pause_cause is None else str(inbox.pause_cause)
            issues.append(
                HealthIssue(
                    INBOX_PAUSED,
                    inbox.id,
                    f"Sending Inbox {inbox.id} is paused ({cause})",
                    detail=RESUME_HINT,
                )
            )
        elif inbox.status is InboxStatus.AUTH_FAILED:
            issues.append(
                HealthIssue(
                    INBOX_AUTH_FAILED,
                    inbox.id,
                    f"Sending Inbox {inbox.id} lost its Gmail grant (invalid_grant)",
                    detail=RECONNECT_HINT,
                )
            )
    return tuple(issues)


# Daily Caps (Story 6.4) -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InboxCapView:
    """One listed inbox's warm-up days and caps on a local date."""

    inbox_id: str
    warmup_days: int | None
    daily_cap: int
    tool_sends: int
    cold_cap: int
    cap_used: int
    ceiling: int


def ceiling() -> int:
    """The ``total_daily_ceiling`` setting; 0 when its stored text no longer parses."""
    try:
        return int(setup_queries.get(Key.TOTAL_DAILY_CEILING))
    except InvalidSettingValue:
        return 0


def _cold_cap(inbox: SendingInboxView, local_date: date, limit: int) -> int:
    if inbox.status is not InboxStatus.ACTIVE:
        return 0
    return cold_cap(inbox.plan, local_date, limit)


def cold_daily_caps(local_date: date) -> dict[str, int]:
    """Every listed inbox id, oldest first, to its cold Daily Cap on ``local_date`` under the
    ceiling; 0 for an inbox that isn't ``active``."""
    if isinstance(local_date, datetime) or not isinstance(local_date, date):
        raise TypeError("cold_daily_caps takes a date")
    limit = ceiling()
    return {inbox.id: _cold_cap(inbox, local_date, limit) for inbox in inboxes()}


def inbox_caps(local_date: date) -> tuple[InboxCapView, ...]:
    """Each listed inbox's warm-up days and caps on ``local_date``, oldest first."""
    limit = ceiling()
    return tuple(
        InboxCapView(
            inbox_id=inbox.id,
            warmup_days=count_warmup_days(inbox.plan, local_date),
            daily_cap=daily_cap(inbox.plan, local_date),
            tool_sends=tool_send_figure(inbox.plan),
            cold_cap=_cold_cap(inbox, local_date, limit),
            cap_used=0,
            ceiling=limit,
        )
        for inbox in inboxes()
    )


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
    dnsbl: dict[str, ZoneOutcome] | None = None
    spam_rate: int | None = None
    spam_status: SpamStatus | None = None

    @property
    def dnsbl_outcome(self) -> ZoneOutcome | None:
        """The worst DNSBL outcome, or ``None`` for a check recorded before Story 6.6."""
        return None if self.dnsbl is None else overall(self.dnsbl.values())

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
        dnsbl=None if row.dnsbl_result is None else decode_dnsbl(row.dnsbl_result),
        spam_rate=row.spam_rate,
        spam_status=None if row.spam_rate_status is None else SpamStatus(row.spam_rate_status),
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


def _reputation_issues(view: DomainAuthView) -> list[HealthIssue]:
    """The reputation issues of one listed domain's newest check."""
    latest = view.latest
    if latest is None:
        return []
    found: list[HealthIssue] = []
    outcome = latest.dnsbl_outcome
    if latest.dnsbl is not None and outcome is ZoneOutcome.LISTED:
        zones = ", ".join(zones_with(latest.dnsbl, ZoneOutcome.LISTED))
        found.append(
            HealthIssue(
                DOMAIN_BLOCKLISTED,
                view.sending_domain_id,
                f"{view.domain}: blocklisted on {zones}; each check pauses its active inboxes",
                detail=DELIST_HINT,
            )
        )
    elif latest.dnsbl is not None and outcome in (
        ZoneOutcome.QUERY_REFUSED,
        ZoneOutcome.UNRECOGNISED,
    ):
        zones = ", ".join(zones_with(latest.dnsbl, outcome))
        found.append(
            HealthIssue(
                DNSBL_QUERY_REFUSED,
                view.sending_domain_id,
                f"{view.domain}: the blocklist query on {zones} got no usable answer "
                "(warning only; nothing was paused)",
                detail=REFUSED_HINT,
            )
        )
    if latest.spam_status is SpamStatus.PAUSE:
        found.append(
            HealthIssue(
                SPAM_RATE_PAUSE,
                view.sending_domain_id,
                f"{view.domain}: spam rate {spam_rate_text(latest.spam_rate)}, at or above "
                "0.30%; each check pauses its active inboxes",
                detail=SPAM_HINT,
            )
        )
    elif latest.spam_status is SpamStatus.ALERT:
        found.append(
            HealthIssue(
                SPAM_RATE_HIGH,
                view.sending_domain_id,
                f"{view.domain}: spam rate {spam_rate_text(latest.spam_rate)}, at or above "
                "0.10%",
                detail=SPAM_HINT,
            )
        )
    return found


def health_issues(
    now: datetime, postmaster_grant_missing: Callable[[], bool] | None = None
) -> tuple[HealthIssue, ...]:
    """The inbox issues (``inbox_health_issues``), then one ``domain_auth_failing`` issue per
    listed Sending Domain whose cold sends are blocked, by domain, then the reputation issues
    (see the module docstring)."""
    views = domain_auths(now)
    issues = [*inbox_health_issues()]
    issues += [
        HealthIssue(
            DOMAIN_AUTH_FAILING,
            view.sending_domain_id,
            f"{view.domain}: {view.verdict.text}",
            detail=RECHECK_HINT,
        )
        for view in views
        if view.verdict.blocked
    ]
    for view in views:
        issues.extend(_reputation_issues(view))
    if views and postmaster_grant_missing is not None and postmaster_grant_missing():
        issues.append(
            HealthIssue(
                POSTMASTER_GRANT_MISSING,
                POSTMASTER_SUBJECT,
                POSTMASTER_GRANT_TEXT,
                detail=POSTMASTER_GRANT_HINT,
            )
        )
    return tuple(issues)



# Sent counts (Story 6.9 seams) ----------------------------------------------------------------


def cold_sent_on(inbox_id: str, local_date: date) -> int | None:
    """The inbox's cold sends on ``local_date``; ``None`` (unknown) always until Story 6.10."""
    return None


def sent_total_on(inbox_id: str, local_date: date) -> int | None:
    """Every send of the inbox on ``local_date``, warm-up tool sends aside; ``None`` (unknown)
    always until Story 6.10."""
    return None
