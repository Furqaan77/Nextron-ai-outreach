"""Re-checking Leads on a schedule (FR-11, AD-7, AD-10, AD-16; Story 8.7): part of the
``research`` service, which owns ``crawl_runs`` and is the sole writer of Evidence and Signals.
``nextron.app.research.api``'s docstring points here.

The rules (which re-check a Lead is due, which Eligibility still allows one) are pure and live in
``nextron.domain.recheck``. This module reads, enqueues and runs the research chain inline. No
log line, ``events`` row, error or job payload holds a domain, a URL or a quote: IDs, codes and
counts only.

**``Rechecks(clock, meter, crawls, job_boards, queue, compliance)``** (``crawls`` the
``WebsiteCrawls``, ``job_boards`` the ``JobBoards`` and ``compliance`` the ``Compliance`` that
evaluates a Primary Contact's Eligibility).

**Who may be re-checked** (``_skip_reason``, the same rules at selection and again when the job
runs): a Lead that is known, isn't merged and has a ``web_domain``; whose stage is in neither
``CLOSED_STAGES`` (Lost, Disqualified) nor ``PARKED_STAGES`` (Not Now); that has no ``active`` or
``paused`` Enrollment (``queries.sequencing.live_enrollment``); whose domain
``compliance_api.assert_contactable(NewLeadSubject(web_domain), Channel.EMAIL,
Purpose.DISCOVERY)`` answers ``Allowed``; and whose Primary Contact, when it has one (not
erased), has a live Eligibility (``Compliance.evaluate_eligibility``) that
``domain.recheck.eligibility_allows``. A Lead with no Primary Contact is re-checked too: research
may find one. Each failing rule has its reason: ``unknown_lead``, ``merged_lead``,
``no_website``, ``lead_closed``, ``lead_parked``, ``live_enrollment``, ``not_contactable`` or
``primary_ineligible``.

``@use_case enqueue_due() -> int`` (the daily ``recheck_leads`` tick), in one transaction:

1. reads ``recheck_full_days``, ``recheck_jobs_days`` and ``recheck_daily_limit``, and today's
   date in ``sender_timezone`` (UTC while it is unset or unreadable);
2. takes the pool (``queries.discovery.recheck_pool``: not merged, with a ``web_domain``, stage
   neither closed nor parked) and, for each Lead, its mode from ``domain.recheck.mode_due`` on
   its last full crawl and last hiring check (``crawl.recheck_candidates``); a Lead with no mode
   isn't due;
3. orders the due Leads oldest first, by when their last crawl run of any mode started (a Lead
   with none first; a ``recheck_jobs`` run whose boards weren't checked yet doesn't count), then
   by id, and walks them, skipping each that already has a live (queued, deferred or running)
   ``recheck_lead`` job under any date or a live ``enrich_lead`` job
   (``queries.jobqueue.has_live_job``: its first crawl is still to come) and each that
   ``_skip_reason`` refuses, until ``recheck_daily_limit`` are picked; every gate call comes
   before the first write;
4. enqueues one ``recheck_lead`` per picked Lead: scope the Lead, discriminator the local date
   (``recheck_lead:<lead>:<YYYY-MM-DD>``; the queue's key also dedups a live job of that day),
   payload ``{v, lead_id, mode}``, on the bulk lane, concurrency key ``domain:<web_domain>`` (one
   site is crawled by one job at a time).

It logs ``rechecks_enqueued`` (``due``, ``picked``, ``enqueued``: the jobs it created) and
returns how many Leads it picked.

``@use_case refresh(lead_id, mode, *, may_retry) -> bool`` (the ``recheck_lead`` job; the
"``research.refresh``" of the story):

1. **First transaction.** When ``domain.budget.allow(RECHECK_LEAD_BUDGET_CLASS,
   meter.snapshot(now), queries.setup.budget_limits())`` refuses, it returns ``False`` (the job is
   deferred) and nothing is crawled, checked or written. A Lead ``_skip_reason`` refuses now logs
   ``recheck_skipped`` (``lead_id``, ``mode``, ``reason``) and returns ``True``. For mode ``jobs``
   it reuses the Lead's ``recheck_jobs`` run whose boards weren't checked yet
   (``crawl.unchecked_jobs_run``: a deferred or failed attempt left it), else inserts the
   ``recheck_jobs`` run: outcome ``ok``, ``site_domain`` the Lead's domain, every page count 0,
   started and ended now.
2. **Mode ``full``** runs ``WebsiteCrawls.crawl_lead(lead_id, may_retry=may_retry,
   lane=Lane.BULK, mode=CrawlMode.RECHECK_FULL)``: the Epic 2 chain, unchanged, from the crawl
   on. Its apply confirms the Evidence of each unchanged page; the extraction sends only changed
   pages (none: no AI call), supersedes or retracts on changed ones and publishes
   ``EvidenceChanged``; the chain goes on through the job boards, the legal form, the Primary
   Contact and ``score_lead``.
3. **Mode ``jobs``** runs ``JobBoards.check_boards(lead_id, run_id, lane=Lane.BULK)`` on that run:
   the boards' counts land on it, postings no longer listed close (``EventClosed``), and the
   chain goes on to ``check_legal_form``, the Primary Contact and ``score_lead``.

It returns what the stage it ran returns (``False``: a later budget check refused it).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import WebsiteCrawls
from nextron.app.research.job_boards import JobBoards
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.uow import current, use_case
from nextron.domain.budget import allow
from nextron.domain.contactability import Blocked, Channel, NewLeadSubject, Purpose
from nextron.domain.crawl import (
    DOMAIN_KEY_PREFIX,
    ENRICH_LEAD_JOB,
    LEAD_ID_FIELD,
    PAYLOAD_VERSION,
    CrawlMode,
    CrawlOutcome,
)
from nextron.domain.job import VERSION_FIELD, Lane
from nextron.domain.pipeline_stage import CLOSED_STAGES, PARKED_STAGES
from nextron.domain.recheck import (
    MODE_FIELD,
    RECHECK_LEAD_BUDGET_CLASS,
    RECHECK_LEAD_JOB,
    RecheckMode,
    eligibility_allows,
    mode_due,
)
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.ports.clock import Clock
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

RECHECKS_ENQUEUED: Final = "rechecks_enqueued"
RECHECK_SKIPPED: Final = "recheck_skipped"
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
NO_WEBSITE: Final = "no_website"
LEAD_CLOSED: Final = "lead_closed"
LEAD_PARKED: Final = "lead_parked"
LIVE_ENROLLMENT: Final = "live_enrollment"
NOT_CONTACTABLE: Final = "not_contactable"
PRIMARY_INELIGIBLE: Final = "primary_ineligible"
_EXCLUDED_STAGES: Final = (*CLOSED_STAGES, *PARKED_STAGES)
_NEVER: Final = datetime.min.replace(tzinfo=UTC)  # sorts a Lead never crawled first


@dataclass(frozen=True, slots=True)
class _Due:
    """A Lead due a re-check, its mode and when its last crawl run of any mode started."""

    lead_id: str
    web_domain: str
    mode: RecheckMode
    last_run_at: datetime | None

    def order(self) -> tuple[bool, datetime, str]:
        """Oldest first (never crawled first), then by id."""
        return (self.last_run_at is not None, self.last_run_at or _NEVER, self.lead_id)


def _sender_zone() -> tzinfo:
    """``sender_timezone``, or UTC while it is unset or unreadable."""
    try:
        return ZoneInfo(str(setup_queries.get(Key.SENDER_TIMEZONE)))
    except (SettingNotSet, InvalidSettingValue, ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def _primary(lead_id: str) -> research_queries.StoredContact | None:
    """The Lead's Primary Contact who isn't erased, or ``None``."""
    return next(
        (
            contact
            for contact in research_queries.contacts(lead_id)
            if contact.is_primary and contact.erased_at is None
        ),
        None,
    )


def _insert_jobs_run(lead_id: str, web_domain: str, now: datetime) -> str:
    """Insert the Lead's ``recheck_jobs`` run (outcome ``ok``, no page) and return its id."""
    return crawl_repo.insert_run(
        lead_id=lead_id,
        site_domain=web_domain,
        final_domain=None,
        started_at=now,
        ended_at=now,
        outcome=str(CrawlOutcome.OK),
        error_code=None,
        pages_fetched=0,
        pages_skipped_robots=0,
        renders=0,
        errors=0,
        mode=str(CrawlMode.RECHECK_JOBS),
    )


class Rechecks:
    """The ``recheck_leads`` and ``recheck_lead`` jobs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        crawls: WebsiteCrawls,
        job_boards: JobBoards,
        queue: JobQueue,
        compliance: Compliance,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._crawls = crawls
        self._job_boards = job_boards
        self._queue = queue
        self._compliance = compliance

    # Who may be re-checked ----------------------------------------------------------------------

    def _skip_reason(self, lead_id: str) -> str | None:
        """Why the Lead may not be re-checked now, or ``None``; in the caller's transaction."""
        lead = discovery_queries.lead(lead_id)
        if lead is None:
            return UNKNOWN_LEAD
        if lead.merged_into is not None:
            return MERGED_LEAD
        if lead.web_domain is None:
            return NO_WEBSITE
        stage = pipeline_queries.stage(lead.id)
        if stage in CLOSED_STAGES:
            return LEAD_CLOSED
        if stage in PARKED_STAGES:
            return LEAD_PARKED
        if sequencing_queries.live_enrollment(lead.id) is not None:
            return LIVE_ENROLLMENT
        answer = compliance_api.assert_contactable(
            NewLeadSubject(lead.web_domain), Channel.EMAIL, Purpose.DISCOVERY
        )
        if isinstance(answer, Blocked):
            return NOT_CONTACTABLE
        primary = _primary(lead.id)
        if primary is not None and not eligibility_allows(
            self._compliance.evaluate_eligibility(primary.id)
        ):
            return PRIMARY_INELIGIBLE
        return None

    # recheck_leads ------------------------------------------------------------------------------

    @use_case
    def enqueue_due(self) -> int:
        """Enqueue today's ``recheck_lead`` jobs; see the module docstring."""
        now = self._clock.now()
        created = 0
        with current().transaction():
            full_days = int(setup_queries.get(Key.RECHECK_FULL_DAYS))
            jobs_days = int(setup_queries.get(Key.RECHECK_JOBS_DAYS))
            limit = int(setup_queries.get(Key.RECHECK_DAILY_LIMIT))
            today = now.astimezone(_sender_zone()).date().isoformat()
            facts = crawl_repo.recheck_candidates()
            due: list[_Due] = []
            for lead_id, web_domain in discovery_queries.recheck_pool(_EXCLUDED_STAGES):
                found = facts.get(lead_id)
                mode = mode_due(
                    None if found is None else found.last_full_at,
                    None if found is None else found.last_hiring_at,
                    now,
                    full_days,
                    jobs_days,
                )
                if mode is not None:
                    last_run = None if found is None else found.last_run_at
                    due.append(_Due(lead_id, web_domain, mode, last_run))
            due.sort(key=_Due.order)
            picked: list[_Due] = []
            for candidate in due:  # every gate call before the first write
                if len(picked) >= limit:
                    break
                if any(
                    jobqueue_queries.has_live_job(job_type, candidate.lead_id)
                    for job_type in (RECHECK_LEAD_JOB, ENRICH_LEAD_JOB)
                ):
                    continue
                if self._skip_reason(candidate.lead_id) is None:
                    picked.append(candidate)
            for chosen in picked:
                enqueued = self._queue.enqueue(
                    RECHECK_LEAD_JOB,
                    scope=chosen.lead_id,
                    discriminator=today,
                    payload={
                        VERSION_FIELD: PAYLOAD_VERSION,
                        LEAD_ID_FIELD: chosen.lead_id,
                        MODE_FIELD: str(chosen.mode),
                    },
                    lane=Lane.BULK,
                    concurrency_key=f"{DOMAIN_KEY_PREFIX}{chosen.web_domain}",
                )
                created += enqueued.created
        log.info(
            RECHECKS_ENQUEUED, extra={"due": len(due), "picked": len(picked), "enqueued": created}
        )
        return len(picked)

    # recheck_lead -------------------------------------------------------------------------------

    @use_case
    def refresh(self, lead_id: str, mode: RecheckMode, *, may_retry: bool) -> bool:
        """Re-check the Lead in ``mode``; see the module docstring. ``False`` when the budget
        refused the work."""
        found_mode = RecheckMode(mode)
        uow = current()
        now = self._clock.now()
        run_id: str | None = None
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(RECHECK_LEAD_BUDGET_CLASS, self._meter.snapshot(now), limits):
                return False
            skipped = self._skip_reason(lead_id)
            if skipped is None and found_mode is RecheckMode.JOBS:
                lead = discovery_queries.lead(lead_id)
                if lead is None or lead.web_domain is None:  # _skip_reason just read it
                    raise RuntimeError(f"Lead {lead_id} has no website")
                unchecked = crawl_repo.unchecked_jobs_run(lead.id)  # a retry reuses its run
                if unchecked is None:
                    run_id = _insert_jobs_run(lead.id, lead.web_domain, now)
                else:
                    run_id = unchecked.id
        if skipped is not None:
            log.info(
                RECHECK_SKIPPED,
                extra={"lead_id": lead_id, "mode": str(found_mode), "reason": skipped},
            )
            return True
        if run_id is None:  # mode full
            return self._crawls.crawl_lead(
                lead_id, may_retry=may_retry, lane=Lane.BULK, mode=CrawlMode.RECHECK_FULL
            )
        return self._job_boards.check_boards(lead_id, run_id, lane=Lane.BULK)
