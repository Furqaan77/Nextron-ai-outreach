"""Sending Domain authentication checks (FR-64, NFR-4, NFR-20, AD-3, AD-9, AD-10): the
``check_domains`` job's service, owned by ``sending``.

``DomainChecks(clock, dns, queue)`` writes the ``domain_checks`` table (append-only) through
``nextron.db.repos.sending.checks``. ``dns`` is the ``DnsPort``; ``queue`` the ``JobQueue``. The
rules live in ``nextron.domain.domain_auth``. A *listed* Sending Domain has at least one inbox at
``queries.sending.LISTED_STATUSES`` (any but ``removed``).

- **``@use_case check_domain(sending_domain_id) -> DomainCheckResult | None``**:

  1. a transaction reads the domain and the domain parts of its listed inboxes' addresses (the
     From domains); an unknown id, or a domain with no listed inbox (every inbox removed, say,
     while its job waited), logs ``domain_check_skipped`` (``sending_domain_id``) and returns
     ``None``: no DNS query, nothing recorded;
  2. ``checked_at`` is ``clock.now()``;
  3. outside any transaction, three ``dns.txt`` queries: the domain (SPF), ``dkim_name`` and
     ``dmarc_name``. An ``AdapterError`` propagates and nothing is recorded, so a check that
     couldn't run never counts (the job retries a retryable one);
  4. ``domain_auth.judge``, then one transaction inserts the row (each item's flag, ``passed``
     and ``encode_details`` of the reasons), then it logs ``domain_check_recorded``
     (``sending_domain_id``, ``passed``, ``failing``: the failed items joined with ``,``).

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

Nothing here changes an inbox's status, pauses one or raises an ops alert: authentication is a
send-gate predicate (AD-4); the panels and the health issues show a failing domain.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries.sending import LISTED_STATUSES, DomainAuthView
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
from nextron.domain.job import TICK, Lane
from nextron.domain.settings_schema import Key, SettingNotSet
from nextron.ports.clock import Clock
from nextron.ports.dns import DnsPort

UTC_TIMEZONE: Final = "UTC"  # the daily check's zone while sender_timezone is unset
CHECK_SKIPPED: Final = "domain_check_skipped"
CHECK_RECORDED: Final = "domain_check_recorded"
RECHECK_QUEUED: Final = "domain_recheck_queued"
CHECKS_QUEUED: Final = "domain_checks_queued"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DomainCheckResult:
    """A recorded check: its row's id, the domain and when it was checked, and the
    judgement."""

    check_id: str
    sending_domain_id: str
    domain: str
    checked_at: datetime
    judgement: AuthJudgement

    @property
    def passed(self) -> bool:
        return self.judgement.passed

    @property
    def failing(self) -> tuple[AuthItem, ...]:
        return self.judgement.failing


def _from_domain(address: str) -> str:
    """The domain part of a stored (normalised) inbox address."""
    return address.rpartition("@")[2]


class DomainChecks:
    """Checks Sending Domains' DNS authentication; see the module docstring."""

    def __init__(self, clock: Clock, dns: DnsPort, queue: JobQueue) -> None:
        self._clock = clock
        self._dns = dns
        self._queue = queue

    @use_case
    def check_domain(self, sending_domain_id: str) -> DomainCheckResult | None:
        """Check one Sending Domain and record the result; see the module docstring."""
        uow = current()
        with uow.transaction():  # step 1
            found = domains_repo.by_id(sending_domain_id)
            addresses = (
                [] if found is None else inboxes_repo.addresses_of(found.id, LISTED_STATUSES)
            )
        if found is None or not addresses:  # unknown, or every inbox removed since it was queued
            log.info(CHECK_SKIPPED, extra={"sending_domain_id": sending_domain_id})
            return None
        checked_at = self._clock.now()  # step 2
        domain = found.domain
        spf_txt = self._dns.txt(domain)  # step 3: no transaction is open
        dkim_txt = self._dns.txt(dkim_name(domain))
        dmarc_txt = self._dns.txt(dmarc_name(domain))
        judgement = judge(  # step 4
            domain, spf_txt, dkim_txt, dmarc_txt, [_from_domain(a) for a in addresses]
        )
        reasons = judgement.reasons
        failing = judgement.failing
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
            )
        log.info(
            CHECK_RECORDED,
            extra={
                "sending_domain_id": found.id,
                "passed": judgement.passed,
                "failing": ",".join(str(item) for item in failing),
            },
        )
        return DomainCheckResult(check_id, found.id, domain, checked_at, judgement)

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
