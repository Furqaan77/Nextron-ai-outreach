"""Sending Domain checks (FR-64, FR-38, FR-58, NFR-4, NFR-20, AD-3, AD-9, AD-10): the
``check_domains`` job's service, owned by ``sending``: DNS authentication (Story 6.5) and the
reputation guardrails, DNSBL listings and the Postmaster spam rate (Story 6.6).

``DomainChecks(clock, dns, queue, *, postmaster, notify)`` writes the ``domain_checks`` table
(append-only) through ``nextron.db.repos.sending.checks``, and pauses inboxes through
``repos.sending.inboxes``. ``dns`` is the ``DnsPort``; ``queue`` the ``JobQueue``;
``postmaster`` the ``PostmasterPort``; ``notify`` the ``notify`` service (``Alerts``). The rules
live in ``nextron.domain.domain_auth`` and ``nextron.domain.reputation``. A *listed* Sending Domain
has at least one inbox at ``queries.sending.LISTED_STATUSES`` (any but ``removed``).

- **``@use_case check_domain(sending_domain_id) -> DomainCheckResult | None``**:

  1. a transaction reads the domain, the domain parts of its listed inboxes' addresses (the
     From domains) and the ``dnsbl_zones`` setting (a stored value that no longer parses reads
     as ``reputation.DEFAULT_ZONES``, so the guard never switches off); an unknown id, or a
     domain with no listed inbox (every inbox removed, say, while its job waited), logs
     ``domain_check_skipped`` (``sending_domain_id``) and returns ``None``: no query, nothing
     recorded;
  2. ``checked_at`` is ``clock.now()``;
  3. outside any transaction: three ``dns.txt`` queries (the domain (SPF), ``dkim_name`` and
     ``dmarc_name``), one ``dns.a`` query of ``<domain>.<zone>`` per zone, then
     ``postmaster.spam_rate(domain, reputation.postmaster_day(checked_at))`` (the UTC date
     before the check's). A Postmaster token error coded ``google_not_granted``,
     ``google_needs_reconsent`` or ``google_client_not_imported`` reads as no data. Any other
     ``AdapterError`` propagates and nothing is recorded, so a check that couldn't run never
     counts (the job retries a retryable one);
  4. ``domain_auth.judge``, ``reputation.zone_outcome`` per zone and
     ``reputation.spam_status``; then one transaction inserts the row (each item's flag,
     ``passed``, ``encode_details`` of the reasons, ``encode_dnsbl`` of the outcomes, the spam
     rate in ppm and its status) and applies the guardrails:

     - a DNSBL ``listed`` outcome moves every ``active`` inbox of the domain to ``paused``
       (``transition(active, pause_blocklist)``, the update fenced on ``status='active'``),
       appending one ``events`` row per move (cause ``blocklist``, ``cause_ref`` the check's
       id, actor ``system:sending``), then raises a ``domain_blocklisted`` alert;
     - a spam status ``pause`` does the same with ``pause_spam_rate`` (cause ``spam_rate``;
       an inbox the blocklist just paused isn't active, so isn't paused again), then raises a
       ``spam_rate_pause`` alert; a status ``alert`` raises a ``spam_rate_high`` alert.

     Each alert is ``notify.alert(kind, reputation.alert_subject(domain_id, checked_at's UTC
     date), text, (telegram, dashboard))``: one per kind, domain and UTC day, its text IDs and
     counts only. ``query_refused``, ``unrecognised`` and ``no_data`` pause and alert nothing.
     Then it logs ``domain_check_recorded`` (``sending_domain_id``, ``passed``, ``failing``:
     the failed items joined with ``,``, ``dnsbl``: the overall outcome, ``spam_rate_status``
     and ``paused``: how many inboxes it paused).

- **``@command queue_check(sending_domain_id) -> bool``** runs in the caller's transaction
  (Story 6.2 calls it on connect). It enqueues ``check_domains`` on the ``interactive`` lane with
  ``scope=<id>``, ``discriminator="tick"`` and ``payload={"v": 1, "sending_domain_id": <id>}``,
  so its key is ``check_domains:<id>:tick``: while that domain's check is queued or running,
  another request is absorbed by it. It returns whether a new job was created.
- **``@use_case recheck(sending_domain_id) -> bool``**: the Operator's Recheck. In one
  transaction, ``False`` with nothing queued unless the domain is listed; otherwise it calls
  ``queue_check`` and returns ``True`` (a new job, or the one already queued or running). It
  logs ``domain_recheck_queued`` (``sending_domain_id``, ``job_created``).
- **``@use_case queue_daily() -> int``**: the daily tick. One transaction queues one check per
  listed Sending Domain (``queue_check``); it returns how many and logs
  ``domain_checks_queued`` (``count``).
- **``schedule() -> DomainCheckSchedule(at, timezone, missed)``**, a plain read for a read-only
  unit of work: ``at`` is ``DOMAIN_CHECK_TIME``, ``timezone`` the ``sender_timezone`` setting
  (``UTC`` while it is unset) and ``missed`` is ``check_missed`` over the listed domains' newest
  check times.
- **``auths()``**, a plain read too: ``queries.sending.domain_auths(clock.now())``, what the
  Sending Inboxes page's authentication section shows.

Authentication never pauses an inbox or alerts: it is a send-gate predicate (AD-4), and the
panels and the health issues show a failing domain. Only the reputation guardrails pause, and a
paused inbox fails ``inbox_ready`` and dispatch closed. Nothing here resumes an inbox: an
Operator resume while the condition persists is paused again by the next check, without a
second alert that day.
"""

import logging
from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Final, Protocol

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries.sending import LISTED_STATUSES, DomainAuthView
from nextron.db import eventlog
from nextron.db.repos.sending import checks as checks_repo
from nextron.db.repos.sending import domains as domains_repo
from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.domain_auth import (
    CHECK_DOMAINS_JOB,
    DOMAIN_CHECK_TIME,
    PAYLOAD_VERSION,
    SENDING_DOMAIN_ID_FIELD,
    AuthItem,
    AuthJudgement,
    DomainCheckSchedule,
    check_missed,
    dkim_name,
    dmarc_name,
    encode_details,
    judge,
)
from nextron.domain.alert import Channel
from nextron.domain.job import TICK, Lane
from nextron.domain.reputation import (
    DEFAULT_ZONES,
    DOMAIN_BLOCKLISTED,
    SPAM_RATE_HIGH,
    SPAM_RATE_PAUSE,
    SpamStatus,
    ZoneOutcome,
    alert_subject,
    blocklisted_text,
    dnsbl_name,
    encode_dnsbl,
    overall,
    postmaster_day,
    spam_high_text,
    spam_pause_text,
    spam_status,
    zone_outcome,
)
from nextron.domain.sending_inbox import PAUSE_EVENTS, InboxStatus, PauseCause, transition
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.ports.clock import Clock
from nextron.ports.dns import DnsPort
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    GOOGLE_CLIENT_NOT_IMPORTED,
    GOOGLE_NEEDS_RECONSENT,
    GOOGLE_NOT_GRANTED,
)
from nextron.ports.postmaster import PostmasterPort

UTC_TIMEZONE: Final = "UTC"  # the daily check's zone while sender_timezone is unset
CHECK_SKIPPED: Final = "domain_check_skipped"
CHECK_RECORDED: Final = "domain_check_recorded"
RECHECK_QUEUED: Final = "domain_recheck_queued"
CHECKS_QUEUED: Final = "domain_checks_queued"
INBOX_ENTITY: Final = "sending_inboxes"
SYSTEM_ACTOR: Final = "system:sending"
ALERT_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)
# A Postmaster token error that reads as no data.
NO_GRANT_CODES: Final = frozenset(
    {GOOGLE_NOT_GRANTED, GOOGLE_NEEDS_RECONSENT, GOOGLE_CLIENT_NOT_IMPORTED}
)

log = logging.getLogger(__name__)


class Alerts(Protocol):
    """What the checks need of the ``notify`` service."""

    def alert(self, kind: str, subject: str, text: str, channels: Iterable[Channel]) -> object: ...


@dataclass(frozen=True, slots=True)
class DomainCheckResult:
    """A recorded check: its row's id, the domain and when it was checked, the judgement, the
    DNSBL outcomes, the spam rate and its status, and the inboxes it paused (Story 6.6)."""

    check_id: str
    sending_domain_id: str
    domain: str
    checked_at: datetime
    judgement: AuthJudgement
    dnsbl: Mapping[str, ZoneOutcome]
    spam_rate: int | None
    spam_status: SpamStatus
    paused: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.judgement.passed

    @property
    def failing(self) -> tuple[AuthItem, ...]:
        return self.judgement.failing


def _zones() -> tuple[str, ...]:
    """The ``dnsbl_zones`` setting; a stored value that no longer parses reads as the
    default, so the guard never switches off."""
    try:
        return tuple(setup_queries.get(Key.DNSBL_ZONES))
    except InvalidSettingValue:
        return DEFAULT_ZONES


def _from_domain(address: str) -> str:
    """The domain part of a stored (normalised) inbox address."""
    return address.rpartition("@")[2]


class DomainChecks:
    """Checks Sending Domains' DNS authentication; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        dns: DnsPort,
        queue: JobQueue,
        *,
        postmaster: PostmasterPort,
        notify: Alerts,
    ) -> None:
        self._clock = clock
        self._dns = dns
        self._queue = queue
        self._postmaster = postmaster
        self._notify = notify

    @use_case
    def check_domain(self, sending_domain_id: str) -> DomainCheckResult | None:
        """Check one Sending Domain and record the result; see the module docstring."""
        uow = current()
        with uow.transaction():  # step 1
            found = domains_repo.by_id(sending_domain_id)
            addresses = (
                [] if found is None else inboxes_repo.addresses_of(found.id, LISTED_STATUSES)
            )
            zones = _zones()
        if found is None or not addresses:  # unknown, or every inbox removed since it was queued
            log.info(CHECK_SKIPPED, extra={"sending_domain_id": sending_domain_id})
            return None
        checked_at = self._clock.now()  # step 2
        domain = found.domain
        spf_txt = self._dns.txt(domain)  # step 3: no transaction is open
        dkim_txt = self._dns.txt(dkim_name(domain))
        dmarc_txt = self._dns.txt(dmarc_name(domain))
        answers = {zone: self._dns.a(dnsbl_name(domain, zone)) for zone in zones}
        ppm = self._spam_rate(domain, postmaster_day(checked_at))
        judgement = judge(  # step 4
            domain, spf_txt, dkim_txt, dmarc_txt, [_from_domain(a) for a in addresses]
        )
        reasons = judgement.reasons
        failing = judgement.failing
        outcomes = {zone: zone_outcome(zone, got) for zone, got in answers.items()}
        dnsbl = overall(outcomes.values())
        status = spam_status(ppm)
        paused: list[str] = []
        with uow.transaction():
            check_id = checks_repo.insert(
                sending_domain_id=found.id,
                checked_at=checked_at,
                spf_ok=AuthItem.SPF not in failing,
                dkim_ok=AuthItem.DKIM not in failing,
                dmarc_ok=AuthItem.DMARC not in failing,
                aligned_ok=AuthItem.ALIGNMENT not in failing,
                passed=judgement.passed,
                details=encode_details(reasons),
                dnsbl_result=encode_dnsbl(outcomes),
                spam_rate=ppm,
                spam_rate_status=str(status),
            )
            subject = alert_subject(found.id, checked_at.astimezone(UTC).date())
            if dnsbl is ZoneOutcome.LISTED:
                blocked = self._pause(found.id, PauseCause.BLOCKLIST, check_id)
                paused.extend(blocked)
                self._notify.alert(
                    DOMAIN_BLOCKLISTED,
                    subject,
                    blocklisted_text(found.id, len(blocked)),
                    ALERT_CHANNELS,
                )
            if status is SpamStatus.PAUSE and ppm is not None:
                spam_paused = self._pause(found.id, PauseCause.SPAM_RATE, check_id)
                paused.extend(spam_paused)
                self._notify.alert(
                    SPAM_RATE_PAUSE,
                    subject,
                    spam_pause_text(found.id, ppm, len(spam_paused)),
                    ALERT_CHANNELS,
                )
            elif status is SpamStatus.ALERT and ppm is not None:
                self._notify.alert(
                    SPAM_RATE_HIGH, subject, spam_high_text(found.id, ppm), ALERT_CHANNELS
                )
        log.info(
            CHECK_RECORDED,
            extra={
                "sending_domain_id": found.id,
                "passed": judgement.passed,
                "failing": ",".join(str(item) for item in failing),
                "dnsbl": str(dnsbl),
                "spam_rate_status": str(status),
                "paused": len(paused),
            },
        )
        return DomainCheckResult(
            check_id,
            found.id,
            domain,
            checked_at,
            judgement,
            outcomes,
            ppm,
            status,
            tuple(paused),
        )

    def _spam_rate(self, domain: str, day: date) -> int | None:
        """Step 3's Postmaster read: the day's rate in ppm, ``None`` for no data or no grant."""
        try:
            return self._postmaster.spam_rate(domain, day).ppm
        except AdapterError as failed:
            if failed.code in NO_GRANT_CODES:
                return None
            raise

    def _pause(self, sending_domain_id: str, cause: PauseCause, check_id: str) -> list[str]:
        """In the caller's transaction: move every ``active`` inbox of the domain to
        ``paused`` for ``cause``, one event each; the ids moved."""
        moved = transition(InboxStatus.ACTIVE, PAUSE_EVENTS[cause])
        now = self._clock.now()
        paused = []
        for inbox_id in inboxes_repo.active_ids_of(sending_domain_id):
            if not inboxes_repo.set_status(
                inbox_id, from_status=InboxStatus.ACTIVE, to_status=moved.to
            ):
                continue  # no longer active: nothing to pause
            eventlog.append(
                entity=INBOX_ENTITY,
                entity_id=inbox_id,
                from_status=str(InboxStatus.ACTIVE),
                to_status=str(moved.to),
                cause=str(moved.cause),
                cause_ref=check_id,
                actor=SYSTEM_ACTOR,
                at=now,
            )
            paused.append(inbox_id)
        return paused

    @command
    def queue_check(self, sending_domain_id: str) -> bool:
        """Queue ``sending_domain_id``'s check in the caller's transaction; whether a new job
        was created."""
        enqueued = self._queue.enqueue(
            CHECK_DOMAINS_JOB,
            scope=sending_domain_id,
            discriminator=TICK,
            payload={"v": PAYLOAD_VERSION, SENDING_DOMAIN_ID_FIELD: sending_domain_id},
            lane=Lane.INTERACTIVE,
        )
        return enqueued.created

    @use_case
    def recheck(self, sending_domain_id: str) -> bool:
        """The Operator's Recheck: whether a check of ``sending_domain_id`` is now queued."""
        with current().transaction():
            if not inboxes_repo.addresses_of(sending_domain_id, LISTED_STATUSES):
                return False
            created = self.queue_check(sending_domain_id)
        log.info(
            RECHECK_QUEUED,
            extra={"sending_domain_id": sending_domain_id, "job_created": created},
        )
        return True

    @use_case
    def queue_daily(self) -> int:
        """Queue one check per listed Sending Domain; how many."""
        with current().transaction():
            domains = domains_repo.with_inboxes(LISTED_STATUSES)
            for domain in domains:
                self.queue_check(domain.id)
        log.info(CHECKS_QUEUED, extra={"count": len(domains)})
        return len(domains)

    def schedule(self) -> DomainCheckSchedule:
        """When the daily checks run and whether a listed domain missed the latest one, read
        in the current unit of work (a read-only one included)."""
        now = self._clock.now()
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except SettingNotSet:
            timezone = UTC_TIMEZONE
        listed = [domain.id for domain in domains_repo.with_inboxes(LISTED_STATUSES)]
        newest = checks_repo.newest_times(listed)
        missed = check_missed(newest.values(), now, DOMAIN_CHECK_TIME, timezone)
        return DomainCheckSchedule(DOMAIN_CHECK_TIME, timezone, missed)

    def auths(self) -> tuple[DomainAuthView, ...]:
        """Each listed Sending Domain's newest check and verdict at the clock's now, read in
        the current unit of work (a read-only one included)."""
        return sending_queries.domain_auths(self._clock.now())
