"""Hiring Signals from a Lead's ATS boards, and the re-check of one posting (FR-7, FR-11, FR-49,
AD-5, AD-7, AD-9, AD-10, INT-10, NFR-6; Story 2.11): part of the ``research`` service, the sole
writer of ``job_postings``, Evidence and Signals. ``nextron.app.research.api``'s docstring
points here.

The rules (which boards a Lead's links name, which postings match a hiring Signal type, the
excerpts) are pure and live in ``nextron.domain.postings`` and ``nextron.domain.signals``;
storing and closing postings in ``nextron.app.research.postings``. This module reads, writes,
and calls the ``JobBoardPort`` and the ``CrawlerPort``. No log line, ``events`` row, error or
job payload holds a title, a description, a URL, a token or a quote: IDs, codes and counts
only. No job site is ever queried: board tokens come only from the Lead's own ``ats_links``,
and the endpoints are the adapter's, in code.

**``JobBoards(clock, meter, boards, crawler, queue)``.**

``@use_case check_job_boards(lead_id, run_id) -> bool`` (the ``check_job_boards`` job that
``extract_lead``'s people step enqueues; ``run_id`` is the crawl run of the chain):

1. **First transaction.** When ``nextron.domain.budget.allow(BudgetClass.BLOCKABLE,
   meter.snapshot(now), queries.setup.budget_limits())`` refuses, it returns ``False`` (the job
   is deferred) and nothing else happens. An unknown Lead (a text that isn't a ULID included) or
   a merged one logs ``job_boards_skipped`` (``lead_id``, ``reason``), enqueues nothing and
   returns ``True``. Otherwise it reads the ``ats_hosts`` setting and the boards to check
   (Decision 11): the board of each of the Lead's current ATS links (``board_from_link`` on the
   ``ats_links`` rows last seen by the Lead's latest crawl run, ``crawl.latest_run``; with no
   run, none), and the board of each of its live ATS postings whose source is still
   ``source_enabled``, each once, sorted (by source, then token). A link an older run saw names
   no board unless a live posting holds it.
2. **The calls**, with no transaction open: ``boards.postings(board)`` for each board, in order.
   An ``AdapterError`` is kept for its board (no retry inside the job: Decision 12); each answer
   or error is stamped with the clock's time.
3. **Apply transaction.** A Lead merged meanwhile stores nothing and enqueues nothing (it logs
   ``job_boards_skipped`` with ``merged_lead``). Otherwise, per board in order: an answer goes
   through ``postings.apply_board`` (checked at its time); ``board_not_found`` closes every live
   posting of the board (``postings.close_board``); any other error changes nothing and adds
   one to ``board_errors``. Then the Signals left without live Evidence are retracted and
   ``EventClosed`` is published for each closed posting's Event. When the crawl run ``run_id``
   exists, its ``greenhouse_*``, ``lever_*`` and ``ashby_*`` counts (``_postings``: listed;
   ``_events``: created; 0 for a source with no board) and ``board_errors`` are written. Last,
   it enqueues the chain's next stage, ``check_legal_form`` (Story 2.12, which enqueues
   ``select_primary_contact``): scope the Lead, discriminator ``tick``, payload ``{v, lead_id,
   run_id}``, concurrency key ``domain:<web_domain>`` when the Lead has one.

   After the commit it logs ``job_boards_checked`` (``lead_id``, ``run_id``, ``boards``,
   ``board_errors``, ``listed``, ``events``, ``reopened``, ``closed``) and returns ``True``.

``@use_case recheck_event(signal_id)`` (the ``recheck_event`` job, run before a send: AD-5) re-
checks the one posting whose Event is ``signal_id``. It is not budget-checked (Decision 13).

- **Skips** (logged ``recheck_skipped`` with ``signal_id`` and ``reason``, nothing written):
  ``no_posting`` (no posting has that Event; a text that isn't a ULID included),
  ``posting_closed``, ``unknown_lead`` or ``merged_lead`` (the posting's Lead), and
  ``source_disabled`` (an ATS posting whose source no host of ``ats_hosts`` enables).
- **An ATS posting** re-checks its board through ``boards.postings`` and the same apply
  (``apply_board``, or ``close_board`` on ``board_not_found``), with no counts and no selection.
- **A careers posting** reads ``<origin>/robots.txt`` (``crawler.fetch_robots`` at
  ``MIN_INTERVAL_S``) and reads it as the crawl does (``api.robots_rule``). When that reading
  carries a failure code (a robots.txt 429 or other non-4xx status, or a non-retryable
  ``AdapterError``), the page isn't fetched: a code ``domain.crawl.is_retryable`` accepts (a
  5xx, ``fetch_timeout``) raises ``AdapterError(True, code)``, any other logs
  ``recheck_failed`` with it. Otherwise a disallowed page is skipped (``robots_disallowed``);
  an allowed one is fetched with ``crawler.fetch_page(url, site=<the URL's registrable
  domain>, min_interval_s=<the rule's interval>)``, robots.txt's crawl delay included. A 2xx
  HTML page applies the page rule to that page's live careers postings (``postings.apply_page``
  with nothing proposed, checked at the fetch's time; the page text is never stored), unless
  its fetched text ``needs_render`` (a page built by JavaScript: its titles were checked against
  the rendered text the crawl stored), which logs ``recheck_failed`` with ``needs_render`` and
  changes nothing; a 404 or 410 closes them; a 429 or a 5xx raises ``AdapterError(True,
  http_<status>)``.
- **Failures.** A retryable ``AdapterError`` is raised again (the runner retries it). Any other
  failure (a non-retryable ``AdapterError``, another status, a page that isn't HTML or needs a
  render) logs ``recheck_failed`` (``signal_id``, ``lead_id``, ``error_code``) and changes
  nothing.
- A Lead merged during the call stores nothing (``recheck_skipped``, ``merged_lead``).

After an apply it logs ``event_rechecked`` (``signal_id``, ``lead_id``, ``listed``, ``closed``,
``reopened``).
"""

import logging
import urllib.parse
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research import postings
from nextron.app.research.api import robots_rule
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.postings import StoredPosting
from nextron.db.uow import current, use_case
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.crawl import (
    DOMAIN_KEY_PREFIX,
    HTML_MEDIA_TYPES,
    LEAD_ID_FIELD,
    MIN_INTERVAL_S,
    PAYLOAD_VERSION,
    RUN_ID_FIELD,
    http_code,
    is_retryable,
    needs_render,
)
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.domain.ids import is_ulid
from nextron.domain.job import TICK, VERSION_FIELD
from nextron.domain.legal_form import CHECK_LEGAL_FORM_JOB
from nextron.domain.postings import (
    ATS_SOURCES,
    Board,
    PostingSource,
    board_from_link,
    source_enabled,
)
from nextron.domain.segments import SegmentDef
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock
from nextron.ports.crawler import CrawlerPort
from nextron.ports.errors import AdapterError
from nextron.ports.job_board import BOARD_NOT_FOUND, BoardListing, JobBoardPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

JOB_BOARDS_SKIPPED: Final = "job_boards_skipped"
JOB_BOARDS_CHECKED: Final = "job_boards_checked"
RECHECK_SKIPPED: Final = "recheck_skipped"
RECHECK_FAILED: Final = "recheck_failed"
EVENT_RECHECKED: Final = "event_rechecked"
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
NO_POSTING: Final = "no_posting"
POSTING_CLOSED: Final = "posting_closed"
SOURCE_DISABLED: Final = "source_disabled"
ROBOTS_DISALLOWED: Final = "robots_disallowed"
PAGE_GONE: Final = frozenset({404, 410})  # a careers page answering one closes its postings
TOO_MANY_REQUESTS: Final = 429  # with a 5xx, a careers page's answer worth a retry
NEEDS_RENDER: Final = "needs_render"  # a recheck's page text is a JavaScript page's raw text
ROBOTS_RETRY: Final = "the careers page's robots.txt failed for now; retrying the recheck"
PAGE_RETRY: Final = "the careers page failed for now; retrying the recheck"
BOARD_ERRORS: Final = "board_errors"

type _Apply = Callable[[datetime, postings.Tally], None]  # a recheck's writes, in its transaction


@dataclass(frozen=True, slots=True)
class _Check:
    """What ``check_job_boards``' first transaction read."""

    lead_id: str
    web_domain: str | None
    segment: SegmentDef
    boards: tuple[Board, ...]


@dataclass(frozen=True, slots=True)
class _Answer:
    """One board's answer, or the error it failed with, and when."""

    board: Board
    at: datetime
    listing: BoardListing | None = None
    error: AdapterError | None = None


@dataclass(frozen=True, slots=True)
class _Recheck:
    """What ``recheck_event``'s first transaction read."""

    posting: StoredPosting
    segment: SegmentDef


def _site_of(url: str) -> str | None:
    try:
        host = urllib.parse.urlsplit(url).hostname
        return None if host is None else registrable_domain(host)
    except (ValueError, IdentityError):
        return None


def _origin(url: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _boards(lead_id: str, ats_hosts: Collection[str]) -> tuple[Board, ...]:
    """The boards to check: the Lead's current links' (last seen by its latest crawl run),
    then its live ATS postings' still enabled."""
    found: set[Board] = set()
    latest = crawl_repo.latest_run(lead_id)
    links = () if latest is None else crawl_repo.ats_links(lead_id)
    for link in links:
        if latest is None or link.run_id != latest.id:
            continue
        board = board_from_link(link.url, ats_hosts)
        if board is not None:
            found.add(board)
    for row in postings_repo.for_lead(lead_id):
        if not row.live or row.board_token is None:
            continue
        source = PostingSource(row.source)
        if source in ATS_SOURCES and source_enabled(source, ats_hosts):
            found.add(Board(source, row.board_token))
    return tuple(sorted(found))


class JobBoards:
    """The ``check_job_boards`` and ``recheck_event`` jobs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        boards: JobBoardPort,
        crawler: CrawlerPort,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._boards = boards
        self._crawler = crawler
        self._queue = queue

    # check_job_boards ---------------------------------------------------------------------------

    @use_case
    def check_job_boards(self, lead_id: str, run_id: str) -> bool:
        """Check the Lead's ATS boards; see the module docstring. ``False`` when the budget
        refused the work."""
        uow = current()
        now = self._clock.now()
        skipped: str | None = None
        check: _Check | None = None
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(BudgetClass.BLOCKABLE, self._meter.snapshot(now), limits):
                return False
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                skipped = UNKNOWN_LEAD
            elif lead.merged_into is not None:
                skipped = MERGED_LEAD
            else:
                seeded = setup_queries.segment_by_id(lead.segment_id)
                if seeded is None:  # leads.segment_id is a foreign key
                    raise RuntimeError(f"Lead {lead.id}'s Segment has no row")
                ats_hosts = tuple(setup_queries.get(Key.ATS_HOSTS))
                boards = _boards(lead.id, ats_hosts)
                check = _Check(lead.id, lead.web_domain, seeded.definition, boards)
        if check is None:
            log.info(JOB_BOARDS_SKIPPED, extra={"lead_id": lead_id, "reason": skipped})
            return True
        answers = [self._ask(board) for board in check.boards]
        with uow.transaction():
            tally, errors, merged = self._apply(check, run_id, answers)
        if merged:
            log.info(JOB_BOARDS_SKIPPED, extra={"lead_id": lead_id, "reason": MERGED_LEAD})
            return True
        log.info(
            JOB_BOARDS_CHECKED,
            extra={
                "lead_id": check.lead_id,
                "run_id": run_id,
                "boards": len(check.boards),
                BOARD_ERRORS: errors,
                "listed": sum(tally.listed.values()),
                "events": sum(tally.events.values()),
                "reopened": tally.reopened,
                "closed": tally.closed,
            },
        )
        return True

    def _ask(self, board: Board) -> _Answer:
        try:
            listing = self._boards.postings(board)
        except AdapterError as error:
            return _Answer(board, self._clock.now(), error=error)
        return _Answer(board, self._clock.now(), listing=listing)

    def _apply(
        self, check: _Check, run_id: str, answers: Sequence[_Answer]
    ) -> tuple[postings.Tally, int, bool]:
        """Step 3, in the caller's transaction: the tally, the board errors and whether the
        Lead was merged meanwhile."""
        now = self._clock.now()
        tally = postings.Tally()
        lead = discovery_queries.lead(check.lead_id)
        if lead is None or lead.merged_into is not None:
            return tally, 0, True
        errors = self._apply_answers(check.lead_id, check.segment, answers, now, tally)
        signals_repo.retract_unsupported(check.lead_id, now)
        postings.publish_closed(check.lead_id, tally)
        if is_ulid(run_id) and crawl_repo.run(run_id) is not None:
            counts = dict(postings.tally_counts(tally, ATS_SOURCES))
            counts[BOARD_ERRORS] = errors
            crawl_repo.set_posting_counts(run_id, counts)
        self._queue.enqueue(  # the chain's next stage (Story 2.12), then the Primary Contact's
            CHECK_LEGAL_FORM_JOB,
            scope=check.lead_id,
            discriminator=TICK,
            payload={
                VERSION_FIELD: PAYLOAD_VERSION,
                LEAD_ID_FIELD: check.lead_id,
                RUN_ID_FIELD: run_id,
            },
            concurrency_key=(
                None if lead.web_domain is None else f"{DOMAIN_KEY_PREFIX}{lead.web_domain}"
            ),
        )
        return tally, errors, False

    @staticmethod
    def _apply_answers(
        lead_id: str,
        segment: SegmentDef,
        answers: Sequence[_Answer],
        now: datetime,
        tally: postings.Tally,
    ) -> int:
        """Apply each board's answer or error; the boards that failed otherwise than
        ``board_not_found``."""
        errors = 0
        for answer in answers:
            if answer.listing is not None:
                postings.apply_board(
                    lead_id, segment, answer.board, answer.listing, answer.at, now, tally
                )
            elif answer.error is not None and answer.error.code == BOARD_NOT_FOUND:
                postings.close_board(lead_id, answer.board, now, tally)
            else:
                errors += 1
        return errors

    # recheck_event ------------------------------------------------------------------------------

    @use_case
    def recheck_event(self, signal_id: str) -> None:
        """Re-check the one posting whose Event is ``signal_id``; see the module docstring."""
        uow = current()
        skipped: str | None = None
        recheck: _Recheck | None = None
        with uow.transaction():
            found = postings_repo.by_signal(signal_id) if is_ulid(signal_id) else None
            lead = None if found is None else discovery_queries.lead(found.lead_id)
            if found is None:
                skipped = NO_POSTING
            elif not found.live:
                skipped = POSTING_CLOSED
            elif lead is None:
                skipped = UNKNOWN_LEAD
            elif lead.merged_into is not None:
                skipped = MERGED_LEAD
            else:
                source = PostingSource(found.source)
                ats_hosts = tuple(setup_queries.get(Key.ATS_HOSTS))
                if source in ATS_SOURCES and not source_enabled(source, ats_hosts):
                    skipped = SOURCE_DISABLED
                else:
                    seeded = setup_queries.segment_by_id(lead.segment_id)
                    if seeded is None:  # leads.segment_id is a foreign key
                        raise RuntimeError(f"Lead {lead.id}'s Segment has no row")
                    recheck = _Recheck(found, seeded.definition)
        if recheck is None:
            log.info(RECHECK_SKIPPED, extra={"signal_id": signal_id, "reason": skipped})
            return
        posting = recheck.posting
        if posting.source == PostingSource.CAREERS_PAGE:
            self._recheck_page(signal_id, recheck)
        else:
            self._recheck_board(signal_id, recheck)

    def _failed(self, signal_id: str, lead_id: str, error_code: str | None) -> None:
        log.info(
            RECHECK_FAILED,
            extra={"signal_id": signal_id, "lead_id": lead_id, "error_code": error_code},
        )

    def _finish(self, signal_id: str, lead_id: str, apply: _Apply) -> None:
        """The recheck's apply transaction; logs what it did."""
        with current().transaction():
            now = self._clock.now()
            tally = postings.Tally()
            lead = discovery_queries.lead(lead_id)
            if lead is None or lead.merged_into is not None:
                merged = True
            else:
                merged = False
                apply(now, tally)
                signals_repo.retract_unsupported(lead_id, now)
                postings.publish_closed(lead_id, tally)
        if merged:
            log.info(RECHECK_SKIPPED, extra={"signal_id": signal_id, "reason": MERGED_LEAD})
            return
        log.info(
            EVENT_RECHECKED,
            extra={
                "signal_id": signal_id,
                "lead_id": lead_id,
                "listed": sum(tally.listed.values()),
                "closed": tally.closed,
                "reopened": tally.reopened,
            },
        )

    def _recheck_board(self, signal_id: str, recheck: _Recheck) -> None:
        posting = recheck.posting
        token = posting.board_token
        if token is None:  # ck_job_postings_board_token: an ATS posting has one
            raise RuntimeError("an ATS posting has no board token")
        board = Board(PostingSource(posting.source), token)
        try:
            listing = self._boards.postings(board)
        except AdapterError as error:
            if error.retryable:
                raise
            if error.code != BOARD_NOT_FOUND:
                self._failed(signal_id, posting.lead_id, error.code)
                return

            def gone(now: datetime, tally: postings.Tally) -> None:
                postings.close_board(posting.lead_id, board, now, tally)

            self._finish(signal_id, posting.lead_id, gone)
            return
        at = self._clock.now()

        def listed(now: datetime, tally: postings.Tally) -> None:
            postings.apply_board(posting.lead_id, recheck.segment, board, listing, at, now, tally)

        self._finish(signal_id, posting.lead_id, listed)

    def _recheck_page(self, signal_id: str, recheck: _Recheck) -> None:
        posting = recheck.posting
        origin, site = _origin(posting.url), _site_of(posting.url)
        if origin is None or site is None:
            self._failed(signal_id, posting.lead_id, None)
            return
        try:
            answer = self._crawler.fetch_robots(
                f"{origin}/robots.txt", min_interval_s=MIN_INTERVAL_S
            )
        except AdapterError as error:
            if error.retryable:
                raise
            answer = error
        allows, interval, code = robots_rule(answer)
        if code is not None:  # robots.txt couldn't be read: the page isn't fetched
            if is_retryable(code):
                raise AdapterError(True, code, ROBOTS_RETRY)
            self._failed(signal_id, posting.lead_id, code)
            return
        if not allows(posting.url):
            log.info(
                RECHECK_SKIPPED, extra={"signal_id": signal_id, "reason": ROBOTS_DISALLOWED}
            )
            return
        try:
            fetched = self._crawler.fetch_page(posting.url, site=site, min_interval_s=interval)
        except AdapterError as error:
            if error.retryable:
                raise
            self._failed(signal_id, posting.lead_id, error.code)
            return
        at = self._clock.now()
        status = fetched.status
        if status in PAGE_GONE:
            text = ""  # every title has left the page
        elif status == TOO_MANY_REQUESTS or 500 <= status < 600:
            raise AdapterError(True, http_code(status), PAGE_RETRY)
        elif not fetched.ok or fetched.media_type not in HTML_MEDIA_TYPES:
            self._failed(signal_id, posting.lead_id, http_code(status))
            return
        elif needs_render(fetched.text):  # its titles were read from the rendered text
            self._failed(signal_id, posting.lead_id, NEEDS_RENDER)
            return
        else:
            text = fetched.text

        def checked(now: datetime, tally: postings.Tally) -> None:
            postings.apply_page(
                posting.lead_id, recheck.segment, posting.url, text, (), at, now, tally
            )

        self._finish(signal_id, posting.lead_id, checked)

