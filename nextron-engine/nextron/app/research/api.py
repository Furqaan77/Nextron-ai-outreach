"""Public command API of the ``research`` service (AD-3, AD-7, AD-9, AD-18, FR-10, FR-49, NFR-6).

Other services and the delivery layers change ``research`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

**``WebsiteCrawls(clock, meter, crawler, queue)``** crawls Leads' websites (Story 2.5) and owns
``crawl_runs``, ``crawled_pages`` and ``ats_links``.

``@use_case enrich_lead(lead_id, *, may_retry) -> bool`` (the ``enrich_lead`` job):

1. **First transaction.** When ``nextron.domain.budget.allow(BLOCKABLE, meter.snapshot(now),
   queries.setup.budget_limits())`` is false it returns ``False`` (the job is deferred) with
   nothing fetched or written. A Lead that is unknown (a text that isn't a ULID included),
   merged or without a ``web_domain`` logs ``crawl_skipped`` (``lead_id``, ``reason``) and
   returns ``True``. Otherwise it reads ``crawl_max_pages`` and ``ats_hosts``.
2. **Robots** (no transaction open). ``https://<site>/robots.txt``; after a
   ``fetch_dns_failed`` or ``fetch_connect_failed``, ``https://www.<site>/robots.txt``. The
   origin that answered (``https://<site>`` or ``https://www.<site>``) is the run's origin. A
   2xx applies its rules (``Robots.allows``) and its crawl delay
   (``nextron.domain.crawl.crawl_interval``); a 4xx other than 429 allows everything; any other
   status (429 included) or error disallows everything for the run, and its code
   (``http_<status>`` or the adapter's) is the run's ``error_code``. A DNS or connect failure on
   both ends the run ``unreachable`` with that code. robots.txt is fetched at the 1 s minimum
   interval, every page at the run's. When ``RUN_DEADLINE`` has passed by then, the run ends
   ``unreachable`` with ``fetch_timeout``.
3. **Homepage** ``<origin>/``, with no ``site`` pin: robots-disallowed ends the run
   ``robots_disallowed``; an ``AdapterError`` (its code), a non-2xx status (``http_<status>``)
   or a page outside ``HTML_MEDIA_TYPES`` (``fetch_unsupported_type``) ends it ``unreachable``
   and adds one to ``errors``. When its final URL's registrable domain isn't the site and is a
   shared platform (``nextron.domain.leads.is_shared_platform``: a profile page, site builder,
   directory or shortener), the run ends ``unreachable`` with ``shared_platform``, storing
   nothing and enqueuing nothing. Otherwise the homepage is stored (kind ``home``); when its
   final URL's registrable domain isn't the site, that domain is the run's ``final_domain``, the
   rest of the run is pinned to it (``site=`` on every fetch), and that origin's robots.txt
   (the final URL's scheme and host) is read once and read as in step 2: its rule and interval
   apply to every later page, and a failure's code becomes the run's ``error_code`` when it has
   none.
4. **Other pages**, breadth-first to ``MAX_DEPTH``. At each depth the candidates are the ``a``
   links of the pages stored at the depth before, in order, that ``fetch_policy.check_url``
   accepts, whose host is on the run's registrable domain, that ``classify_link(path, text)``
   matches and whose URL (as ``check_url`` writes it) isn't seen yet; they are taken by
   ``PageKind`` order, then in the order found. A robots-disallowed candidate adds to
   ``pages_skipped_robots``; an ``AdapterError`` or a non-2xx status adds to ``errors``, and a
   429 or 503 also stops the run's fetching (the site asked to slow down); a 2xx page outside
   ``HTML_MEDIA_TYPES`` is dropped; a page whose final URL is already stored is skipped.
   The run stops when ``crawl_max_pages`` pages (the homepage included) are stored, or once
   ``RUN_DEADLINE`` (10 minutes on the clock) has passed since it started.
   ``pages_fetched`` counts the 2xx answers, the homepage included; ``renders`` is 0 (Story 2.6
   renders).
5. **Apply transaction.** When the Lead was merged meanwhile, the run is inserted as
   ``lead_merged`` and nothing else is written. Otherwise the run is inserted (outcome ``ok``
   unless step 2 or 3 ended it); each stored page is upserted on ``(lead_id, url)`` with its
   ``content_hash`` (``text_hash`` of its text), ``fetched_at``, ``text_purge_after``
   (``fetched_at`` + 30 days) and ``changed_at`` (``fetched_at`` for a new page or a changed
   hash, else kept); and every ``a``, ``iframe`` and ``script`` link of a stored page that
   ``check_url`` accepts and whose host is in ``ats_hosts`` is upserted on ``(lead_id, url)``
   (``first_seen_at`` kept, ``last_seen_at``, ``run_id`` and ``page_id`` updated; the first page
   that holds it in the run). With a ``final_domain``, ``canonicalize_lead`` is enqueued
   (payload ``{v, lead_id, run_id}``).
6. **Afterwards** it logs ``crawl_finished`` (``lead_id``, ``run_id``, ``outcome``,
   ``error_code``, ``pages_fetched``, ``pages_stored``, ``pages_skipped_robots``, ``errors``).
   Then, when ``may_retry`` holds, the run isn't ``lead_merged`` and its ``error_code``
   ``is_retryable`` (a timeout or a 5xx from robots.txt or the homepage), it raises
   ``AdapterError(True, "crawl_retry", ...)`` so the job runs again; without ``may_retry`` (the
   job's last attempt) the job completes and no dead-job alert follows. It returns ``True``.

No page text, HTML or URL is logged; the run's row holds codes and counts only.

**``Research(clock)``**:

- ``@command reparent_lead(n: LeadsMerged)``: the ``LeadsMerged`` subscriber
  (``nextron.main.build_subscribers``, after ``pipeline``'s). Every ``crawl_runs`` row of the
  merged Lead moves to the survivor, and so does each ``crawled_pages`` and ``ats_links`` row
  whose ``url`` the survivor doesn't already hold (the rest stay on the merged Lead).
- ``@use_case purge_page_text() -> int`` (the daily ``purge_page_text`` job): in one
  transaction, nulls ``text`` on every page whose ``text_purge_after`` is now or earlier, keeping
  ``content_hash``; logs ``page_text_purged`` (``count``) and returns the count.
"""

import logging
import urllib.parse
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.crawl import (
    CANONICALIZE_LEAD_JOB,
    HTML_MEDIA_TYPES,
    LEAD_ID_FIELD,
    MAX_DEPTH,
    MIN_INTERVAL_S,
    PAYLOAD_VERSION,
    RUN_DEADLINE,
    RUN_ID_FIELD,
    TEXT_RETENTION,
    CrawlOutcome,
    PageKind,
    classify_link,
    crawl_interval,
    http_code,
    is_retryable,
    lead_job_keys,
    text_hash,
)
from nextron.domain.fetch_policy import FetchTarget, check_url, url_refusal
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.domain.job import VERSION_FIELD
from nextron.domain.leads import is_shared_platform
from nextron.domain.notifications import LeadsMerged
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock
from nextron.ports.crawler import (
    FETCH_CONNECT_FAILED,
    FETCH_DNS_FAILED,
    FETCH_TIMEOUT,
    FETCH_UNSUPPORTED_TYPE,
    CrawlerPort,
    FetchedPage,
    PageLink,
    Robots,
)
from nextron.ports.errors import AdapterError
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

CRAWL_RETRY: Final = "crawl_retry"
CRAWL_SKIPPED: Final = "crawl_skipped"
CRAWL_FINISHED: Final = "crawl_finished"
PAGE_TEXT_PURGED: Final = "page_text_purged"
# Why a crawl was skipped, as logged
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
NO_WEBSITE: Final = "no_website"
# A robots.txt fetch failure that means the origin isn't there: try www., then give up
UNREACHABLE_CODES: Final = frozenset({FETCH_DNS_FAILED, FETCH_CONNECT_FAILED})
TOO_MANY_REQUESTS: Final = 429  # a robots.txt 429 is read like a 5xx, not like a 4xx
THROTTLE_STATUSES: Final = frozenset({429, 503})  # a page answering one stops the run
SHARED_PLATFORM: Final = "shared_platform"  # the homepage landed on a shared platform
KIND_ORDER: Final = {kind: index for index, kind in enumerate(PageKind)}

type RobotsRule = Callable[[str], bool]


def _allow_all(url: str) -> bool:
    return True


def _disallow_all(url: str) -> bool:
    return False


def _target(url: str) -> FetchTarget | None:
    """``url`` as the egress rules write it, or ``None`` when they refuse it."""
    if not isinstance(url, str) or url_refusal(url) is not None:
        return None
    return check_url(url)


def _domain_of(host: str) -> str | None:
    try:
        return registrable_domain(host)
    except IdentityError:
        return None


@dataclass(frozen=True, slots=True)
class _Page:
    """A page the run stores."""

    url: str
    final_url: str
    kind: PageKind
    status: int
    fetched_at: datetime
    text: str = field(repr=False)
    links: tuple[PageLink, ...] = field(repr=False)


@dataclass(slots=True)
class _Run:
    """What one crawl found, before it is applied."""

    lead_id: str
    site: str
    started_at: datetime
    outcome: CrawlOutcome = CrawlOutcome.OK
    error_code: str | None = None
    final_domain: str | None = None
    pages: list[_Page] = field(default_factory=list)
    pages_fetched: int = 0
    pages_skipped_robots: int = 0
    errors: int = 0
    throttled: bool = False


@dataclass(frozen=True, slots=True)
class _Origin:
    """The origin robots.txt answered from, its rule and the run's interval."""

    origin: str
    allows: RobotsRule
    interval: float


class WebsiteCrawls:
    """Polite crawls of Leads' websites; see the module docstring."""

    def __init__(
        self, clock: Clock, meter: UsageMeterPort, crawler: CrawlerPort, queue: JobQueue
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._crawler = crawler
        self._queue = queue

    @use_case
    def enrich_lead(self, lead_id: str, *, may_retry: bool) -> bool:
        """Crawl the Lead's website and store what it shows; see the module docstring."""
        uow = current()
        started_at = self._clock.now()
        skipped: str | None = None
        site = ""
        max_pages = 0
        ats_hosts: frozenset[str] = frozenset()
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(BudgetClass.BLOCKABLE, self._meter.snapshot(started_at), limits):
                return False
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                skipped = UNKNOWN_LEAD
            elif lead.merged_into is not None:
                skipped = MERGED_LEAD
            elif lead.web_domain is None:
                skipped = NO_WEBSITE
            else:
                site = lead.web_domain
                max_pages = int(setup_queries.get(Key.CRAWL_MAX_PAGES))
                ats_hosts = frozenset(setup_queries.get(Key.ATS_HOSTS))
        if skipped is not None:
            log.info(CRAWL_SKIPPED, extra={"lead_id": lead_id, "reason": skipped})
            return True
        run = _Run(lead_id=lead_id, site=site, started_at=started_at)
        self._crawl(run, max_pages)
        with uow.transaction():
            run_id = self._apply(run, ats_hosts)
        log.info(
            CRAWL_FINISHED,
            extra={
                "lead_id": lead_id,
                "run_id": run_id,
                "outcome": str(run.outcome),
                "error_code": run.error_code,
                "pages_fetched": run.pages_fetched,
                "pages_stored": len(run.pages),
                "pages_skipped_robots": run.pages_skipped_robots,
                "errors": run.errors,
            },
        )
        if may_retry and run.outcome is not CrawlOutcome.LEAD_MERGED and is_retryable(
            run.error_code
        ):
            raise AdapterError(
                True, CRAWL_RETRY, f"the website failed with {run.error_code}; retrying the crawl"
            )
        return True

    # The crawl (no transaction open) -----------------------------------------------------------

    def _crawl(self, run: _Run, max_pages: int) -> None:
        origin = self._robots(run)
        if origin is None:
            return
        if self._past_deadline(run):  # robots.txt took the whole run
            run.outcome = CrawlOutcome.UNREACHABLE
            run.error_code = FETCH_TIMEOUT
            return
        home = self._home(run, origin)
        if home is None:
            return
        domain = run.site
        if run.final_domain is not None:  # the rest of the run is on the new domain
            domain = run.final_domain
            origin = self._final_robots(run, home)
        seen = {home.url, home.final_url}
        stored = {home.final_url}
        frontier = [home]
        for _ in range(MAX_DEPTH):
            candidates = self._candidates(frontier, domain, seen)
            frontier = []
            for url, kind in candidates:
                if len(run.pages) >= max_pages or self._past_deadline(run):
                    return
                if not origin.allows(url):
                    run.pages_skipped_robots += 1
                    continue
                page = self._page(run, url, kind, domain, origin.interval)
                if run.throttled:  # the site asked us to slow down: stop for this run
                    return
                if page is None or page.final_url in stored:
                    continue
                seen.add(page.final_url)
                stored.add(page.final_url)
                run.pages.append(page)
                frontier.append(page)

    def _past_deadline(self, run: _Run) -> bool:
        return self._clock.now() >= run.started_at + RUN_DEADLINE

    def _read_robots(self, url: str) -> Robots | AdapterError:
        try:
            return self._crawler.fetch_robots(url, min_interval_s=MIN_INTERVAL_S)
        except AdapterError as error:
            return error

    @staticmethod
    def _rule(answer: Robots | AdapterError) -> tuple[RobotsRule, float, str | None]:
        """Step 2's reading of one robots.txt answer: the rule, the interval and the code of a
        failure (``None`` for a 2xx or a 4xx other than 429)."""
        if isinstance(answer, AdapterError):
            return _disallow_all, MIN_INTERVAL_S, answer.code
        status = answer.status
        if 200 <= status < 300:
            if answer.allows is None:  # a 2xx always carries its rules; fail closed
                return _disallow_all, MIN_INTERVAL_S, None
            return answer.allows, crawl_interval(answer.crawl_delay_s), None
        if 400 <= status < 500 and status != TOO_MANY_REQUESTS:
            return _allow_all, MIN_INTERVAL_S, None
        return _disallow_all, MIN_INTERVAL_S, http_code(status)

    def _robots(self, run: _Run) -> _Origin | None:
        """Step 2: the run's origin, robots rule and interval; ``None`` when unreachable."""
        unreachable: str | None = None
        for origin in (f"https://{run.site}", f"https://www.{run.site}"):
            answer = self._read_robots(f"{origin}/robots.txt")
            if isinstance(answer, AdapterError) and answer.code in UNREACHABLE_CODES:
                unreachable = answer.code
                continue
            allows, interval, code = self._rule(answer)
            run.error_code = code
            return _Origin(origin, allows, interval)
        run.outcome = CrawlOutcome.UNREACHABLE
        run.error_code = unreachable
        return None

    def _final_robots(self, run: _Run, home: _Page) -> _Origin:
        """The robots.txt of the domain the homepage landed on, read as step 2 reads one; a
        failure's code becomes the run's ``error_code`` when it has none."""
        landed = check_url(home.final_url)  # _home read the final domain from it
        origin = f"{landed.scheme}://{urllib.parse.urlsplit(landed.url).netloc}"
        allows, interval, code = self._rule(self._read_robots(f"{origin}/robots.txt"))
        if code is not None and run.error_code is None:
            run.error_code = code
        return _Origin(origin, allows, interval)

    def _home(self, run: _Run, origin: _Origin) -> _Page | None:
        """Step 3: the stored homepage, or ``None`` when the run ends here."""
        url = f"{origin.origin}/"
        if not origin.allows(url):
            run.outcome = CrawlOutcome.ROBOTS_DISALLOWED
            return None
        try:
            fetched = self._crawler.fetch_page(url, site=None, min_interval_s=origin.interval)
        except AdapterError as error:
            return self._unreachable(run, error.code)
        if not fetched.ok:
            return self._unreachable(run, http_code(fetched.status))
        run.pages_fetched += 1
        if fetched.media_type not in HTML_MEDIA_TYPES:
            run.outcome = CrawlOutcome.UNREACHABLE
            run.error_code = FETCH_UNSUPPORTED_TYPE
            return None
        page = self._stored(url, fetched, PageKind.HOME)
        landed = _target(page.final_url)
        final_domain = None if landed is None else _domain_of(landed.host)
        if final_domain is not None and final_domain != run.site:
            if is_shared_platform(final_domain):  # a profile, builder or directory page
                run.outcome = CrawlOutcome.UNREACHABLE
                run.error_code = SHARED_PLATFORM
                return None
            run.final_domain = final_domain
        run.pages.append(page)
        return page

    @staticmethod
    def _unreachable(run: _Run, code: str) -> None:
        run.errors += 1
        run.outcome = CrawlOutcome.UNREACHABLE
        run.error_code = code

    @staticmethod
    def _candidates(
        frontier: Iterable[_Page], domain: str, seen: set[str]
    ) -> list[tuple[str, PageKind]]:
        """The links of ``frontier`` to fetch next, in the order step 4 takes them; each is
        added to ``seen``."""
        found: list[tuple[int, int, str, PageKind]] = []
        for page in frontier:
            for link in page.links:
                if link.tag != "a":
                    continue
                target = _target(link.url)
                if target is None or target.url in seen or _domain_of(target.host) != domain:
                    continue
                kind = classify_link(target.path, link.text)
                if kind is None or kind is PageKind.HOME:
                    continue
                seen.add(target.url)
                found.append((KIND_ORDER[kind], len(found), target.url, kind))
        found.sort()
        return [(url, kind) for _, _, url, kind in found]

    def _page(
        self, run: _Run, url: str, kind: PageKind, domain: str, interval: float
    ) -> _Page | None:
        """One page of step 4, or ``None`` when it isn't stored."""
        try:
            fetched = self._crawler.fetch_page(url, site=domain, min_interval_s=interval)
        except AdapterError:
            run.errors += 1
            return None
        if not fetched.ok:
            run.errors += 1
            run.throttled = fetched.status in THROTTLE_STATUSES
            return None
        run.pages_fetched += 1
        if fetched.media_type not in HTML_MEDIA_TYPES:
            return None
        return self._stored(url, fetched, kind)

    def _stored(self, url: str, fetched: FetchedPage, kind: PageKind) -> _Page:
        landed = _target(fetched.final_url)
        return _Page(
            url=url,
            final_url=fetched.final_url if landed is None else landed.url,
            kind=kind,
            status=fetched.status,
            fetched_at=self._clock.now(),
            text=fetched.text,
            links=fetched.links,
        )

    # The apply transaction ---------------------------------------------------------------------

    def _apply(self, run: _Run, ats_hosts: Collection[str]) -> str:
        """Step 5, in the caller's transaction: the run's id."""
        now = self._clock.now()
        lead = discovery_queries.lead(run.lead_id)
        if lead is None:  # Leads are never deleted
            raise RuntimeError(f"Lead {run.lead_id} has no row")
        merged = lead.merged_into is not None
        run_id = crawl_repo.insert_run(
            lead_id=run.lead_id,
            site_domain=run.site,
            final_domain=run.final_domain,
            started_at=run.started_at,
            ended_at=now,
            outcome=str(CrawlOutcome.LEAD_MERGED if merged else run.outcome),
            error_code=run.error_code,
            pages_fetched=run.pages_fetched,
            pages_skipped_robots=run.pages_skipped_robots,
            renders=0,
            errors=run.errors,
        )
        if merged:
            run.outcome = CrawlOutcome.LEAD_MERGED
            return run_id
        page_ids = [self._upsert_page(run.lead_id, run_id, page) for page in run.pages]
        seen: set[str] = set()
        for page_id, page in zip(page_ids, run.pages, strict=True):
            for link in page.links:
                target = _target(link.url)
                if target is None or target.host not in ats_hosts or target.url in seen:
                    continue
                seen.add(target.url)
                self._upsert_ats_link(run.lead_id, run_id, page_id, target, link.tag, now)
        if run.final_domain is not None:
            discriminator, concurrency_key = lead_job_keys(run.site, now)
            self._queue.enqueue(
                CANONICALIZE_LEAD_JOB,
                scope=run.lead_id,
                discriminator=discriminator,
                payload={
                    VERSION_FIELD: PAYLOAD_VERSION,
                    LEAD_ID_FIELD: run.lead_id,
                    RUN_ID_FIELD: run_id,
                },
                concurrency_key=concurrency_key,
            )
        return run_id

    @staticmethod
    def _upsert_page(lead_id: str, run_id: str, page: _Page) -> str:
        digest = text_hash(page.text)
        purge_after = page.fetched_at + TEXT_RETENTION
        stored = crawl_repo.page(lead_id, page.url)
        if stored is None:
            return crawl_repo.insert_page(
                lead_id=lead_id,
                run_id=run_id,
                url=page.url,
                final_url=page.final_url,
                kind=str(page.kind),
                fetched_at=page.fetched_at,
                http_status=page.status,
                content_hash=digest,
                text=page.text,
                text_purge_after=purge_after,
                changed_at=page.fetched_at,
            )
        crawl_repo.update_page(
            stored.id,
            run_id=run_id,
            final_url=page.final_url,
            kind=str(page.kind),
            fetched_at=page.fetched_at,
            http_status=page.status,
            content_hash=digest,
            text=page.text,
            text_purge_after=purge_after,
            changed_at=stored.changed_at if stored.content_hash == digest else page.fetched_at,
        )
        return stored.id

    @staticmethod
    def _upsert_ats_link(
        lead_id: str, run_id: str, page_id: str, target: FetchTarget, tag: str, now: datetime
    ) -> None:
        stored = crawl_repo.ats_link(lead_id, target.url)
        if stored is None:
            crawl_repo.insert_ats_link(
                lead_id=lead_id,
                run_id=run_id,
                page_id=page_id,
                url=target.url,
                host=target.host,
                tag=tag,
                at=now,
            )
        else:
            crawl_repo.touch_ats_link(stored.id, run_id=run_id, page_id=page_id, at=now)


class Research:
    """The ``research`` service's Lead-scoped crawl rows; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def reparent_lead(self, n: LeadsMerged) -> None:
        """Move the merged Lead's crawl rows to the survivor; see the module docstring."""
        crawl_repo.reparent(n.merged_lead_id, n.survivor_lead_id)

    @use_case
    def purge_page_text(self) -> int:
        """Null every page text past its purge time; the number of pages purged."""
        with current().transaction():
            count = crawl_repo.purge_text(self._clock.now())
        log.info(PAGE_TEXT_PURGED, extra={"count": count})
        return count
