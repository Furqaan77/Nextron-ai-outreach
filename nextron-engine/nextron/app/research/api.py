"""Public command API of the ``research`` service (AD-3, AD-4, AD-7, AD-9, AD-11, AD-18, FR-2,
FR-10, FR-14, FR-15, FR-49, FR-51, NFR-6).

Other services and the delivery layers change ``research`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Research(clock)`` owns ``contacts`` and ``provenance_records`` and is the sole writer of
Provenance. Every Address Class assignment appends one ``events`` row (entity ``contacts``,
actor ``system:research``, at the clock's now) with IDs and codes only.

- ``@command add_contact(lead_id, candidate, *, source, detail, retrieved_at) ->
  AddedContact(contact_id, created, email_dropped, blocked)`` stores a person on a Lead,
  deduplicated within it:

  1. **Checks, before any write.** ``candidate`` not a ``ContactCandidate``, ``source`` not a
     ``ProvenanceSource``, or ``retrieved_at`` not an aware ``datetime`` raises ``TypeError``;
     a ``detail`` that isn't a ``str`` of 1 to 2,048 characters equal to its own strip raises
     ``ValueError``. The Lead is read with ``queries.discovery.lead``: an
     unknown Lead raises ``LookupError`` and a merged one ``ValueError``. With an email,
     ``assign(source)`` (``nextron.domain.address_class``) runs first, so a source that never
     yields an address (``IllegalTransition``) writes nothing.
  2. **Matching.** Among the Lead's Contacts (oldest first), the match is the first with an
     equal ``email``, else, when the candidate has a name, the first whose name has an equal
     ``normalize_name`` key.
  3. **The contactability gate (AD-16, Story 3.4)**, before any write, for every source:
     ``compliance_api.assert_contactable(NewContactSubject(lead.id, email, name,
     linkedin_url), email, discovery)``, each value the candidate's or, when the candidate
     lacks it, its match's; then, with a match, ``assert_contactable(ContactSubject(match.id),
     email, discovery)``, so the match's own stored values are checked too. Either ``Blocked``
     answer blocks, with the reasons of both combined in ``BlockReason`` order; an answer that
     is neither ``Allowed`` nor ``Blocked`` raises ``TypeError``. A block writes nothing (no
     Contact, fill, Provenance Record or address) but one ``events`` row: entity ``leads``, the
     Lead's id, ``None`` to ``contact_blocked``, cause the first reason, no ``cause_ref``,
     actor ``system:research``, now. It returns ``AddedContact(contact_id=None, created=False,
     email_dropped=False, blocked=<the combined answer>)``. A suppressed address, address
     domain, Lead domain, person or profile blocks, and so does a Lead at ``lost`` or
     ``disqualified``.
  4. **No match.** A Contact is inserted (its Address Class ``assign(source).to`` when it has an
     email, its office's ``office_country`` and ``office_region`` when the candidate has an
     ``office``, ``is_primary`` false, created and updated now), then a ``contact`` Provenance
     Record. With an email, an ``address`` record (its ``address`` and ``address_class``) and
     one ``events`` row (``None`` to the class, cause ``assign(source).cause``, no
     ``cause_ref``) follow. ``created=True``.
  5. **A match.** Each of ``name``, ``role``, ``linkedin_url``, the office and ``email`` the
     Contact lacks is filled; nothing is ever overwritten. A filled name, role, LinkedIn URL or
     office adds one ``contact`` record; a filled email sets its class and adds the ``address``
     record and the ``events`` row as in step 4. A candidate email that differs from the
     Contact's own gives ``email_dropped=True`` and is not stored. When nothing is filled,
     nothing is written. ``created=False``.
  6. **Provenance Records** carry ``source_kind`` = ``source``, ``source_detail`` = ``detail``
     and ``retrieved_at``; ``address_class`` is set only on ``address`` records, ``notes`` hold
     the Market notes (below) and the eligibility-basis fields are NULL.
  7. **The Market (Story 3.2).** After the writes, the Contact is resolved (below) whenever it
     was created or anything was filled, in the same transaction (a filled name, role or
     LinkedIn URL included: it resolves a Contact stored before Story 3.2 and keeps the notes on
     its first ``contact`` record).

- ``@command reparent_lead(n: LeadsMerged)``: the ``LeadsMerged`` subscriber
  (``nextron.main.build_subscribers``, after ``pipeline``'s). Every Contact of the merged Lead
  moves to the survivor (``updated_at`` now); when the survivor already has a Primary Contact,
  the moved Contacts' Primary flags are cleared. Provenance Records follow their Contact. It
  appends no ``events`` row. Duplicate Contacts on the survivor are kept (Story 2.10 folds
  them). Each moved Contact is then resolved, since the survivor's country may differ. Every
  ``crawl_runs`` row of the merged Lead moves to the survivor too (Story 2.5), and so does each
  ``crawled_pages`` and ``ats_links`` row whose ``url`` the survivor doesn't already hold (the
  rest stay on the merged Lead). Every ``evidence`` and ``signals`` row of the merged Lead
  moves to the survivor (Story 2.9); its ``extraction_runs`` and its ``hq_tz`` and
  ``hq_evidence_id`` stay.

- ``@command resolve_market(contact_id)`` resolves one Contact, for the later paths that change
  its email, office location or Lead outside ``add_contact`` (Stories 2.10, 2.13, 2.14, 2.16);
  an unknown Contact (an id that isn't a ULID included) raises ``LookupError``.

- ``@use_case purge_page_text() -> int`` (the daily ``purge_page_text`` job, Story 2.5): in one
  transaction, nulls ``text`` on every page whose ``text_purge_after`` is now or earlier, keeping
  ``content_hash``; logs ``page_text_purged`` (``count``) and returns the count.

**Resolving a Contact's Market and timezone (Story 3.2, AD-11).** One routine, ``_resolve``,
serves ``add_contact``, ``reparent_lead`` and ``resolve_market``. It reads the Contact, its Lead
(``queries.discovery.lead``) and the stored Markets (``queries.setup.markets()``), and calls
``nextron.domain.contact_market.resolve_contact_market`` with the stored email, the stored
office (a ``Location``, or ``None`` without an ``office_country``), the Lead's ``country_code``
and ``lead_hq_tz``, the Lead's stored headquarters zone (``hq_tz``, Story 2.9). It writes
``market_id`` (the chosen Market's row id, or NULL), ``market_basis``, ``tz`` and ``tz_basis``
through ``contacts.fill`` (``updated_at`` now) only when one of them differs, and sets the notes
(joined with a newline, or NULL when there are none) on the Contact's first ``contact``
Provenance Record (by ``retrieved_at``, then ``id``) only when they differ; when it does, it also
clears the notes of the Contact's other ``contact`` records, so only the first holds them (a
later source may supply an earlier ``retrieved_at``). No log line or ``events`` row holds a note
or a place.

**``ContactMarkets(clock, research, queue)``** resolves the Contacts stored before Story 3.2,
through the one-off ``resolve_contact_markets`` job (bulk lane):

- ``@use_case queue_backfill() -> bool``: in one transaction, when any Contact is unresolved
  (``market_basis`` NULL), enqueues ``resolve_contact_markets`` (scope ``global``,
  discriminator ``1``, payload ``{"v": 1}``; a live job with that key absorbs the repeat) and
  logs ``contact_markets_queued`` (with ``new_job``: whether this call created the job). It
  returns whether a job is now queued.
  ``nextron.main`` runs it at each engine start, before the runner starts.
- ``@use_case backfill() -> int``: with no Market stored, it returns 0 and writes nothing, so
  the next start queues the job again. Otherwise it walks the unresolved Contacts by id in
  batches of ``BACKFILL_BATCH`` (200), one transaction per batch, resolving each, and returns
  how many it resolved, logged as ``contact_markets_resolved`` (``count`` only).

**``WebsiteCrawls(clock, meter, crawler, renderer, queue)``** crawls Leads' websites (Story
2.5), renders their near-empty pages (Story 2.6) and owns ``crawl_runs``, ``crawled_pages``,
``ats_links``, ``page_contact_methods`` and the profile group's ``leads.contact_methods``
(written only through ``nextron.db.repos.research.profile``, as ``hq_tz`` and
``hq_evidence_id`` are by ``SignalExtractions``).

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
   ``pages_fetched`` counts the 2xx answers, the homepage included; ``renders`` is 0 here
   (``render_pages`` adds to it). Each stored page carries ``nextron.domain.widgets.detect``
   over its raw HTML, its links (``(tag, url)``) and its visible text, before any stripping
   matters, and ``nextron.domain.widgets.tag_managers`` over its raw HTML and links (Story
   2.9). The renderer is never called here.
5. **Apply transaction.** When the Lead was merged meanwhile, the run is inserted as
   ``lead_merged`` and nothing else is written or enqueued. Otherwise the run is inserted
   (outcome ``ok`` unless step 2 or 3 ended it); each stored page is upserted on
   ``(lead_id, url)`` with its
   ``content_hash`` (``text_hash`` of its text), ``fetched_at``, ``text_purge_after``
   (``fetched_at`` + 30 days) and ``changed_at`` (``fetched_at`` for a new page or a changed
   hash, else kept); and every ``a``, ``iframe`` and ``script`` link of a stored page that
   ``check_url`` accepts and whose host is in ``ats_hosts`` is upserted on ``(lead_id, url)``
   (``first_seen_at`` kept, ``last_seen_at``, ``run_id`` and ``page_id`` updated; the first page
   that holds it in the run). Each upserted page's ``page_contact_methods`` rows are replaced
   by its detections (``detected_at`` now), and its ``tag_managers`` by the keys it loads
   (``set_tag_managers``). With a ``final_domain``, ``canonicalize_lead`` is
   enqueued (payload ``{v, lead_id, run_id}``). When any stored page ``needs_render`` (under
   500 characters of text), ``render_pages`` is enqueued (the same payload, discriminator and
   concurrency key, from ``lead_job_keys``) and the Signal extraction waits for it; otherwise,
   when the run stored at least one page, the Lead's ``contact_methods`` is set (below) and
   ``extract_lead`` is enqueued (below). A run that stored nothing leaves ``contact_methods``
   as it was and enqueues no extraction.
6. **Afterwards** it logs ``crawl_finished`` (``lead_id``, ``run_id``, ``outcome``,
   ``error_code``, ``pages_fetched``, ``pages_stored``, ``pages_skipped_robots``, ``errors``,
   ``render_queued``), then ``absence_checked`` (below) when the apply set ``contact_methods``.
   Then, when ``may_retry`` holds, the run isn't ``lead_merged`` and its ``error_code``
   ``is_retryable`` (a timeout or a 5xx from robots.txt or the homepage), it raises
   ``AdapterError(True, "crawl_retry", ...)`` so the job runs again; without ``may_retry`` (the
   job's last attempt) the job completes and no dead-job alert follows. It returns ``True``.

``@use_case render_pages(lead_id, run_id) -> bool`` (the ``render_pages`` job, Story 2.6):

1. **First transaction.** The budget check as in ``enrich_lead`` returns ``False`` when it
   fails. A run that is unknown (``unknown_run``; a text that isn't a ULID included), belongs to
   another Lead (``other_lead``), whose Lead is merged (``merged_lead``) or that isn't the
   Lead's latest (``stale_run``) logs ``render_skipped`` (``lead_id``, ``run_id``, ``reason``)
   and returns ``True``. The candidates are the run's pages that still have text and
   ``needs_render``, by ``PageKind`` order then URL, the first ``MAX_RENDERS`` (3).
2. **Rendering**, with no transaction open: each candidate's ``final_url`` through the
   ``RendererPort``, ``site`` the run's ``final_domain`` (else its ``site_domain``), at
   ``MIN_INTERVAL_S``. A rendered page is detected (``widgets.detect`` over the rendered HTML,
   links and text, and ``widgets.tag_managers`` over the rendered HTML and links); an
   ``AdapterError`` counts one error, keeps the crawled text and is never retried.
3. **Apply transaction.** When the Lead was merged meanwhile, or the run is no longer the
   Lead's latest (a crawl finished during the renders), nothing is written or enqueued (the
   newer run queues its own extraction). Otherwise a non-empty rendered text replaces the
   page's ``text`` and ``content_hash`` (``changed_at`` moves to the render's time when the
   hash changed); the render's detections the page doesn't hold yet are added (``detected_at``
   the render's time), and so are the tag managers it loads (``add_tag_managers``); the run's
   ``renders`` (successes) and ``errors`` grow; the Lead's ``contact_methods`` is set; and
   ``extract_lead`` is enqueued (below), so the Signals are read from the rendered text.
4. It logs ``render_finished`` (``lead_id``, ``run_id``, ``pages``, ``renders``, ``errors``,
   ``error_codes``, ``applied``), then ``absence_checked`` (below) when the apply wrote, and
   returns ``True``.

**The Lead's ``contact_methods``** is ``nextron.domain.widgets.encode(summarize(run_id, now,
pages))`` over the run's pages (``crawled_pages`` whose ``run_id`` is the run), each with the
methods of its ``page_contact_methods`` rows: every page checked, and each method's pages.

**The "contact form only" Absence (Story 2.9).** In the same transaction, each time it sets
``contact_methods``, it calls ``nextron.app.research.absence.apply_contact_form_only`` with the
Lead's Segment (``queries.setup.segment_by_id``), that value and a
``nextron.domain.absence.CheckedPage`` for each of the run's pages (its URL, stored text and
``tag_managers``), so the Absence Evidence and its score-only Signal follow every complete
detection, whatever the AI extraction then does. After the transaction commits, the caller
logs ``absence_checked`` (``lead_id``, ``run_id``, ``reason``, ``confirmed``, ``inserted``,
``superseded``, ``retracted``, ``signals_created``, ``signals_retracted``). A run that sets no
``contact_methods`` (nothing stored, a merged Lead, a stale or merged render) doesn't run the
rule and leaves the Absence as it was.

**The ``extract_lead`` job** (Story 2.9) is enqueued once for each crawl run that stored a page:
by ``enrich_lead``'s apply when no page needs rendering, else by the ``render_pages`` apply that
writes the run's renders. Its scope is the Lead, its discriminator the Lead's number of crawl
runs (a revision, so each run gets its own job: a job key's discriminator can't be the run's
id), its payload ``{v, lead_id, run_id}`` and its concurrency key ``domain:<site>``.

No page text, HTML, URL or matched text is logged; the run's row holds codes and counts only.

**Signals and Evidence (Story 2.9)** live in ``nextron.app.research.extraction``:
``SignalExtractions`` runs the ``extract_lead`` jobs (the Lead's changed pages through Claude's
``extract_signals`` route, validated and reconciled into ``evidence``, ``signals`` and
``signal_evidence``, and the Lead's ``hq_tz``), and ``LeadSignals`` serves the Lead page's
Signals and Evidence sections.
"""

import logging
import urllib.parse
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.absence import AbsenceApplied, apply_contact_form_only
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import profile as profile_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.uow import command, current, use_case
from nextron.domain.absence import CheckedPage
from nextron.domain.address_class import AddressCause, AddressClass, assign
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.contact_market import RESOLVE_CONTACT_MARKETS_JOB, resolve_contact_market
from nextron.domain.contactability import (
    Allowed,
    Blocked,
    BlockReason,
    Channel,
    ContactSubject,
    NewContactSubject,
    Purpose,
)
from nextron.domain.contacts import ContactCandidate, ProvenanceSource, ProvenanceSubject
from nextron.domain.crawl import (
    CANONICALIZE_LEAD_JOB,
    HTML_MEDIA_TYPES,
    LEAD_ID_FIELD,
    MAX_DEPTH,
    MAX_RENDERS,
    MIN_INTERVAL_S,
    PAYLOAD_VERSION,
    RENDER_PAGES_JOB,
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
    needs_render,
    text_hash,
)
from nextron.domain.extraction import EXTRACT_LEAD_JOB
from nextron.domain.fetch_policy import FetchTarget, check_url, url_refusal
from nextron.domain.geo import Location
from nextron.domain.identity import IdentityError, normalize_name, registrable_domain
from nextron.domain.ids import is_ulid
from nextron.domain.job import VERSION_FIELD
from nextron.domain.leads import is_shared_platform
from nextron.domain.machines import Transition
from nextron.domain.notifications import LeadsMerged
from nextron.domain.settings_schema import Key
from nextron.domain.widgets import Detection, detect, encode, summarize, tag_managers
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
from nextron.ports.renderer import RendererPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

ENTITY: Final = "contacts"
ACTOR: Final = "system:research"
# The events row of a Contact the contactability gate blocked (Story 3.4), on its Lead.
BLOCKED_ENTITY: Final = "leads"
CONTACT_BLOCKED: Final = "contact_blocked"
MAX_DETAIL_LENGTH: Final = 2048
# The Contact fields a later source fills when the Contact lacks them, besides the email and
# the office.
TEXT_FIELDS: Final = ("name", "role", "linkedin_url")
BACKFILL_BATCH: Final = 200  # Contacts resolved per transaction by ContactMarkets.backfill

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
RENDER_SKIPPED: Final = "render_skipped"
RENDER_FINISHED: Final = "render_finished"
ABSENCE_CHECKED: Final = "absence_checked"  # Story 2.9: the "contact form only" rule ran
# Why render_pages rendered nothing, as logged
UNKNOWN_RUN: Final = "unknown_run"
OTHER_LEAD: Final = "other_lead"
STALE_RUN: Final = "stale_run"

type Assignment = Transition[AddressClass, AddressCause]
type RobotsRule = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class AddedContact:
    """The Contact a candidate became: its id, whether it was created, and whether the
    candidate's email was dropped because the Contact already has another. When the
    contactability gate blocked it, ``contact_id`` is ``None`` and ``blocked`` holds the gate's
    answer."""

    contact_id: str | None
    created: bool
    email_dropped: bool
    blocked: Blocked | None = None


@dataclass(frozen=True, slots=True)
class _Provenance:
    """What every Provenance Record of one ``add_contact`` call carries."""

    source: ProvenanceSource
    detail: str
    retrieved_at: datetime


def _check_arguments(
    candidate: object, source: object, detail: object, retrieved_at: object
) -> None:
    if not isinstance(candidate, ContactCandidate):
        raise TypeError(f"add_contact takes a ContactCandidate, not {type(candidate).__name__}")
    if not isinstance(source, ProvenanceSource):
        raise TypeError(f"add_contact takes a ProvenanceSource, not {type(source).__name__}")
    if not (isinstance(retrieved_at, datetime) and retrieved_at.utcoffset() is not None):
        raise TypeError("add_contact takes retrieved_at as an aware datetime")
    if (
        not isinstance(detail, str)
        or not 1 <= len(detail) <= MAX_DETAIL_LENGTH
        or detail != detail.strip()
    ):
        raise ValueError(
            f"add_contact takes a stripped detail of 1 to {MAX_DETAIL_LENGTH:,} characters"
        )


def _name_key(contact: StoredContact) -> str | None:
    return None if contact.name is None else normalize_name(contact.name)


def _match(
    contacts: tuple[StoredContact, ...], candidate: ContactCandidate
) -> StoredContact | None:
    """The first Contact with the candidate's email, else the first with its name key."""
    if candidate.email is not None:
        for contact in contacts:
            if contact.email == candidate.email:
                return contact
    key = candidate.name_key
    if key is not None:
        for contact in contacts:
            if _name_key(contact) == key:
                return contact
    return None


def _new_contact(
    lead_id: str, candidate: ContactCandidate, match: StoredContact | None
) -> NewContactSubject:
    """The gate's first subject: each value the candidate's or, when the candidate lacks it,
    its match's. A match's own stored values are checked by a second call
    (``ContactSubject(match.id)``)."""

    def value(name: str) -> str | None:
        found: str | None = getattr(candidate, name)
        if found is None and match is not None:
            found = getattr(match, name)
        return found

    return NewContactSubject(
        lead_id, email=value("email"), name=value("name"), linkedin_url=value("linkedin_url")
    )


def _blocked(answers: Iterable[object]) -> Blocked | None:
    """The gate's answers combined: ``None`` when every one is ``Allowed``, else one
    ``Blocked`` with every reason in ``BlockReason`` order. Any other answer raises
    ``TypeError`` (naming its type only), so nothing proceeds on it."""
    reasons: set[BlockReason] = set()
    for answer in answers:
        if isinstance(answer, Blocked):
            reasons.update(answer.reasons)
        elif not isinstance(answer, Allowed):
            raise TypeError(f"assert_contactable answered a {type(answer).__name__}")
    ordered = tuple(reason for reason in BlockReason if reason in reasons)
    return Blocked(ordered) if ordered else None


class Research:
    """Contacts and their Provenance, and the Lead-scoped crawl rows; see the module
    docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def add_contact(
        self,
        lead_id: str,
        candidate: ContactCandidate,
        *,
        source: ProvenanceSource,
        detail: str,
        retrieved_at: datetime,
    ) -> AddedContact:
        """Store ``candidate`` on the Lead ``lead_id``, or fill its matching Contact; see the
        module docstring."""
        _check_arguments(candidate, source, detail, retrieved_at)
        lead = discovery_queries.lead(lead_id)
        if lead is None:
            raise LookupError("add_contact: the Lead is unknown")
        if lead.merged_into is not None:
            raise ValueError("add_contact: the Lead was merged into another Lead")
        email = candidate.email
        assignment = assign(source) if email is not None else None
        provenance = _Provenance(source, detail, retrieved_at)
        now = self._clock.now()
        match = _match(contacts_repo.for_lead(lead.id), candidate)
        answers = [
            compliance_api.assert_contactable(
                _new_contact(lead.id, candidate, match), Channel.EMAIL, Purpose.DISCOVERY
            )
        ]
        if match is not None:  # the match's own stored values too (decision 13)
            answers.append(
                compliance_api.assert_contactable(
                    ContactSubject(match.id), Channel.EMAIL, Purpose.DISCOVERY
                )
            )
        blocked = _blocked(answers)
        if blocked is not None:
            eventlog.append(
                entity=BLOCKED_ENTITY,
                entity_id=lead.id,
                from_status=None,
                to_status=CONTACT_BLOCKED,
                cause=str(blocked.reasons[0]),
                cause_ref=None,
                actor=ACTOR,
                at=now,
            )
            return AddedContact(None, created=False, email_dropped=False, blocked=blocked)
        office = candidate.office
        if match is None:
            contact_id = contacts_repo.insert(
                lead_id=lead.id,
                name=candidate.name,
                role=candidate.role,
                email=email,
                address_class=None if assignment is None else str(assignment.to),
                linkedin_url=candidate.linkedin_url,
                office_country=None if office is None else office.country_code,
                office_region=None if office is None else office.region,
                at=now,
            )
            self._record(contact_id, ProvenanceSubject.CONTACT, provenance)
            if email is not None and assignment is not None:
                self._address(contact_id, email, assignment, provenance)
            self._resolve(contact_id)
            return AddedContact(contact_id, created=True, email_dropped=False)
        fills: dict[str, str | None] = {
            name: value
            for name in TEXT_FIELDS
            if getattr(match, name) is None and (value := getattr(candidate, name)) is not None
        }
        new_office = office if match.office_country is None else None
        if new_office is not None:
            fills["office_country"] = new_office.country_code
            fills["office_region"] = new_office.region
        filled_text = bool(fills)
        new_email = email if match.email is None else None
        if new_email is not None and assignment is not None:
            fills["email"] = new_email
            fills["address_class"] = str(assignment.to)
        contacts_repo.fill(match.id, now, **fills)
        if filled_text:
            self._record(match.id, ProvenanceSubject.CONTACT, provenance)
        if new_email is not None and assignment is not None:
            self._address(match.id, new_email, assignment, provenance)
        if fills:
            self._resolve(match.id)
        dropped = email is not None and match.email is not None and match.email != email
        return AddedContact(match.id, created=False, email_dropped=dropped)

    @command
    def resolve_market(self, contact_id: str) -> None:
        """Resolve the Contact ``contact_id``'s Market and timezone again; see the module
        docstring. An unknown Contact raises ``LookupError``."""
        self._resolve(contact_id)

    def _resolve(self, contact_id: str) -> None:
        """Resolve the Contact's Market and timezone and write what changed; see the module
        docstring."""
        contact = contacts_repo.get(contact_id) if is_ulid(contact_id) else None
        if contact is None:
            raise LookupError("resolve_market: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        stored = setup_queries.markets()
        office = (
            None
            if contact.office_country is None
            else Location(contact.office_country, contact.office_region)
        )
        held = None if lead is None else profile_repo.headquarters(lead.id)
        resolved = resolve_contact_market(
            email=contact.email,
            office=office,
            lead_country=None if lead is None else lead.country_code,
            lead_hq_tz=None if held is None else held.hq_tz,
            markets=[market.definition for market in stored],
        )
        chosen = resolved.market
        market_id = (
            None
            if chosen is None
            else next(market.id for market in stored if market.code == chosen.code)
        )
        values: dict[str, str | None] = {
            "market_id": market_id,
            "market_basis": str(resolved.market_basis),
            "tz": resolved.tz,
            "tz_basis": str(resolved.tz_basis),
        }
        if any(getattr(contact, name) != value for name, value in values.items()):
            contacts_repo.fill(contact.id, self._clock.now(), **values)
        notes = "\n".join(resolved.notes) or None
        record = provenance_repo.first_contact_record(contact.id)
        if record is not None and record.notes != notes:
            provenance_repo.set_notes(record.id, notes)
            provenance_repo.clear_other_contact_notes(contact.id, record.id)

    def _record(
        self,
        contact_id: str,
        subject: ProvenanceSubject,
        provenance: _Provenance,
        *,
        address: str | None = None,
        address_class: AddressClass | None = None,
    ) -> None:
        provenance_repo.insert(
            contact_id=contact_id,
            subject=str(subject),
            source_kind=str(provenance.source),
            source_detail=provenance.detail,
            address=address,
            retrieved_at=provenance.retrieved_at,
            address_class=None if address_class is None else str(address_class),
        )

    def _address(
        self, contact_id: str, email: str, assignment: Assignment, provenance: _Provenance
    ) -> None:
        """The ``address`` record and the Address Class ``events`` row of a new address."""
        self._record(
            contact_id,
            ProvenanceSubject.ADDRESS,
            provenance,
            address=email,
            address_class=assignment.to,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=contact_id,
            from_status=None,
            to_status=str(assignment.to),
            cause=str(assignment.cause),
            cause_ref=None,
            actor=ACTOR,
            at=self._clock.now(),
        )

    @command
    def reparent_lead(self, n: LeadsMerged) -> None:
        """Move the merged Lead's Contacts, crawl rows, Evidence and Signals to the survivor;
        see the module docstring."""
        survivor_has_primary = contacts_repo.has_primary(n.survivor_lead_id)
        moved = contacts_repo.for_lead(n.merged_lead_id)
        contacts_repo.move_to_lead(
            n.merged_lead_id,
            n.survivor_lead_id,
            self._clock.now(),
            clear_primary=survivor_has_primary,
        )
        for contact in moved:
            self._resolve(contact.id)
        crawl_repo.reparent(n.merged_lead_id, n.survivor_lead_id)
        signals_repo.reparent(n.merged_lead_id, n.survivor_lead_id)

    @use_case
    def purge_page_text(self) -> int:
        """Null every page text past its purge time; the number of pages purged."""
        with current().transaction():
            count = crawl_repo.purge_text(self._clock.now())
        log.info(PAGE_TEXT_PURGED, extra={"count": count})
        return count


class ContactMarkets:
    """The one-off resolution of the Contacts stored before Story 3.2; see the module
    docstring."""

    def __init__(self, clock: Clock, research: Research, queue: JobQueue) -> None:
        self._clock = clock
        self._research = research
        self._queue = queue

    @use_case
    def queue_backfill(self) -> bool:
        """Queue ``resolve_contact_markets`` when any Contact is unresolved; whether a job is
        now queued."""
        with current().transaction():
            if not contacts_repo.has_unresolved():
                return False
            enqueued = self._queue.enqueue(
                RESOLVE_CONTACT_MARKETS_JOB, scope="global", discriminator="1", payload={"v": 1}
            )
        log.info("contact_markets_queued", extra={"new_job": enqueued.created})
        return True

    @use_case
    def backfill(self) -> int:
        """Resolve every unresolved Contact, one batch per transaction; how many it resolved (0,
        with nothing written, while no Market is stored)."""
        uow = current()
        with uow.transaction():
            if not setup_queries.markets():
                return 0
        count = 0
        after: str | None = None
        while True:
            with uow.transaction():
                ids = contacts_repo.unresolved_ids(after, BACKFILL_BATCH)
                for contact_id in ids:
                    self._research.resolve_market(contact_id)
            count += len(ids)
            if len(ids) < BACKFILL_BATCH:
                break
            after = ids[-1]
        log.info("contact_markets_resolved", extra={"count": count})
        return count


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
    detections: tuple[Detection, ...] = field(default=(), repr=False)
    tag_managers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A page ``render_pages`` renders."""

    page_id: str
    final_url: str


@dataclass(frozen=True, slots=True)
class _Rendered:
    """What one render gave: its text and detections, or the error code it failed with."""

    page_id: str
    at: datetime
    text: str = field(default="", repr=False)
    detections: tuple[Detection, ...] = field(default=(), repr=False)
    tag_managers: tuple[str, ...] = ()
    error_code: str | None = None


def _embeds(links: Iterable[PageLink]) -> list[tuple[str, str]]:
    """A page's links as ``nextron.domain.widgets.detect`` takes them."""
    return [(link.tag, link.url) for link in links]


def _log_absence(lead_id: str, run_id: str, absence: AbsenceApplied) -> None:
    """Log what the "contact form only" rule did, once its transaction committed."""
    log.info(
        ABSENCE_CHECKED,
        extra={
            "lead_id": lead_id,
            "run_id": run_id,
            "reason": str(absence.reason),
            "confirmed": absence.confirmed,
            "inserted": absence.inserted,
            "superseded": absence.superseded,
            "retracted": absence.retracted,
            "signals_created": absence.signals_created,
            "signals_retracted": absence.signals_retracted,
        },
    )


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
    render_queued: bool = False
    absence: AbsenceApplied | None = None  # what the "contact form only" rule did, if it ran


@dataclass(frozen=True, slots=True)
class _Origin:
    """The origin robots.txt answered from, its rule and the run's interval."""

    origin: str
    allows: RobotsRule
    interval: float


class WebsiteCrawls:
    """Polite crawls of Leads' websites; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        crawler: CrawlerPort,
        renderer: RendererPort,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._crawler = crawler
        self._renderer = renderer
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
                "render_queued": run.render_queued,
            },
        )
        if run.absence is not None:
            _log_absence(lead_id, run_id, run.absence)
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
            detections=detect(fetched.html, _embeds(fetched.links), fetched.text),
            tag_managers=tag_managers(fetched.html, _embeds(fetched.links)),
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
        for page_id, page in zip(page_ids, run.pages, strict=True):
            crawl_repo.replace_contact_methods(page_id, page.detections, now)
            crawl_repo.set_tag_managers(page_id, page.tag_managers)
        seen: set[str] = set()
        for page_id, page in zip(page_ids, run.pages, strict=True):
            for link in page.links:
                target = _target(link.url)
                if target is None or target.host not in ats_hosts or target.url in seen:
                    continue
                seen.add(target.url)
                self._upsert_ats_link(run.lead_id, run_id, page_id, target, link.tag, now)
        discriminator, concurrency_key = lead_job_keys(run.site, now)
        payload = {VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: run.lead_id, RUN_ID_FIELD: run_id}
        if run.final_domain is not None:
            self._queue.enqueue(
                CANONICALIZE_LEAD_JOB,
                scope=run.lead_id,
                discriminator=discriminator,
                payload=payload,
                concurrency_key=concurrency_key,
            )
        if any(needs_render(page.text) for page in run.pages):
            # the Signal extraction waits for the renders: render_pages' apply enqueues it
            self._queue.enqueue(
                RENDER_PAGES_JOB,
                scope=run.lead_id,
                discriminator=discriminator,
                payload=payload,
                concurrency_key=concurrency_key,
            )
            run.render_queued = True
        elif run.pages:
            run.absence = self._set_contact_methods(run.lead_id, lead.segment_id, run_id, now)
            self._enqueue_extraction(run.lead_id, run_id, run.site, now)
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

    def _enqueue_extraction(self, lead_id: str, run_id: str, site: str, now: datetime) -> None:
        """Enqueue the run's ``extract_lead`` job (Story 2.9); see the module docstring."""
        _, concurrency_key = lead_job_keys(site, now)
        self._queue.enqueue(
            EXTRACT_LEAD_JOB,
            scope=lead_id,
            discriminator=str(crawl_repo.run_count(lead_id)),
            payload={VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead_id, RUN_ID_FIELD: run_id},
            concurrency_key=concurrency_key,
        )

    @staticmethod
    def _set_contact_methods(
        lead_id: str, segment_id: str, run_id: str, now: datetime
    ) -> AbsenceApplied:
        """Set the Lead's ``contact_methods`` to ``summarize`` over the run's pages, then
        reconcile its "contact form only" Absence with them (Story 2.9)."""
        pages = crawl_repo.run_pages(run_id)
        page_ids = {page.id for page in pages}
        methods: dict[str, set[str]] = {}
        for row in crawl_repo.contact_methods_of(lead_id):
            if row.page_id in page_ids:
                methods.setdefault(row.page_id, set()).add(row.method)
        summary = summarize(
            run_id, now, [(page.url, sorted(methods.get(page.id, ()))) for page in pages]
        )
        profile_repo.set_contact_methods(lead_id, encode(summary))
        seeded = setup_queries.segment_by_id(segment_id)
        if seeded is None:  # leads.segment_id is a foreign key
            raise RuntimeError(f"Lead {lead_id}'s Segment has no row")
        loaded = crawl_repo.tag_managers_of(page_ids)
        checked = [CheckedPage(page.url, page.text, loaded.get(page.id)) for page in pages]
        return apply_contact_form_only(lead_id, seeded.definition, summary, checked, now)

    # Rendering (Story 2.6) ---------------------------------------------------------------------

    @use_case
    def render_pages(self, lead_id: str, run_id: str) -> bool:
        """Render the run's near-empty pages and record what they show; see the module
        docstring."""
        uow = current()
        now = self._clock.now()
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(BudgetClass.BLOCKABLE, self._meter.snapshot(now), limits):
                return False
            skipped, site, candidates = self._render_plan(lead_id, run_id)
        if skipped is not None:
            log.info(
                RENDER_SKIPPED, extra={"lead_id": lead_id, "run_id": run_id, "reason": skipped}
            )
            return True
        results = [self._render(candidate, site) for candidate in candidates]
        with uow.transaction():
            absence = self._apply_renders(lead_id, run_id, results)
        log.info(
            RENDER_FINISHED,
            extra={
                "lead_id": lead_id,
                "run_id": run_id,
                "pages": len(results),
                "renders": sum(1 for result in results if result.error_code is None),
                "errors": sum(1 for result in results if result.error_code is not None),
                "error_codes": sorted(
                    {result.error_code for result in results if result.error_code is not None}
                ),
                "applied": absence is not None,
            },
        )
        if absence is not None:
            _log_absence(lead_id, run_id, absence)
        return True

    @staticmethod
    def _render_plan(lead_id: str, run_id: str) -> tuple[str | None, str, list[_Candidate]]:
        """Step 1 of ``render_pages``: why nothing is rendered (or ``None``), the site the
        renders are pinned to, and the pages to render."""
        run = crawl_repo.run(run_id) if is_ulid(run_id) else None
        if run is None:
            return UNKNOWN_RUN, "", []
        if run.lead_id != lead_id:
            return OTHER_LEAD, "", []
        lead = discovery_queries.lead(lead_id)
        if lead is None or lead.merged_into is not None:
            return MERGED_LEAD, "", []
        latest = crawl_repo.latest_run(lead_id)
        if latest is None or latest.id != run_id:
            return STALE_RUN, "", []
        pages = sorted(
            (
                page
                for page in crawl_repo.run_pages(run_id)
                if page.text is not None and needs_render(page.text)
            ),
            key=lambda page: (KIND_ORDER[PageKind(page.kind)], page.url),
        )
        site = run.final_domain if run.final_domain is not None else run.site_domain
        return None, site, [_Candidate(page.id, page.final_url) for page in pages[:MAX_RENDERS]]

    def _render(self, candidate: _Candidate, site: str) -> _Rendered:
        """Step 2 of ``render_pages``, with no transaction open: one page rendered and
        detected, or the code it failed with."""
        try:
            rendered = self._renderer.render(
                candidate.final_url, site=site, min_interval_s=MIN_INTERVAL_S
            )
        except AdapterError as error:
            return _Rendered(candidate.page_id, self._clock.now(), error_code=error.code)
        return _Rendered(
            candidate.page_id,
            self._clock.now(),
            text=rendered.text,
            detections=detect(rendered.html, _embeds(rendered.links), rendered.text),
            tag_managers=tag_managers(rendered.html, _embeds(rendered.links)),
        )

    def _apply_renders(
        self, lead_id: str, run_id: str, results: list[_Rendered]
    ) -> AbsenceApplied | None:
        """Step 3 of ``render_pages``, in the caller's transaction: what the "contact form
        only" rule did, or ``None`` when nothing was written (the Lead was merged or a newer
        run was recorded)."""
        lead = discovery_queries.lead(lead_id)
        if lead is None or lead.merged_into is not None:
            return None
        latest = crawl_repo.latest_run(lead_id)
        if latest is None or latest.id != run_id:  # a crawl finished during the renders
            return None
        pages = {page.id: page for page in crawl_repo.run_pages(run_id)}
        renders = errors = 0
        for result in results:
            if result.error_code is not None:
                errors += 1
                continue
            renders += 1
            page = pages.get(result.page_id)
            if page is None:
                continue
            if result.text:
                digest = text_hash(result.text)
                crawl_repo.set_page_text(
                    page.id,
                    text=result.text,
                    content_hash=digest,
                    changed_at=page.changed_at if digest == page.content_hash else result.at,
                )
            crawl_repo.add_contact_methods(page.id, result.detections, result.at)
            crawl_repo.add_tag_managers(page.id, result.tag_managers)
        crawl_repo.add_render_counts(run_id, renders=renders, errors=errors)
        now = self._clock.now()
        absence = self._set_contact_methods(lead_id, lead.segment_id, run_id, now)
        # Story 2.9: the run's Signals are extracted once its renders are written
        self._enqueue_extraction(lead_id, run_id, latest.site_domain, now)
        return absence
