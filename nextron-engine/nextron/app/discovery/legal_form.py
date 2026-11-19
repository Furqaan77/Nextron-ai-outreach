"""A UK Lead's legal form, confirmed through Companies House (FR-12, FR-9, NFR-3, AD-3, AD-7,
AD-9, INT-5; Story 2.12): the ``check_legal_form`` stage of the research chain, owned by the
``discovery`` service. ``nextron.app.discovery.api``'s docstring points here.

UK cold email may go only to corporate subscribers (PECR). This stage links a GB Lead to its
Companies House record only when Evidence on the Lead's own stored pages does; the rules (the
phrases, the numbers, the postcodes, the names and the status) are
``nextron.domain.legal_form``'s. A ``ch_number`` the Lead already holds (from a CSV, say) is
never Evidence. No log line, ``events`` row, error or job payload holds a name, a quote, a URL
or a postcode: IDs, codes and counts only.

**``LegalFormChecks(clock, meter, registry, discovery, research, queue)``** runs the job.
``@use_case check_legal_form(lead_id) -> bool`` (``False`` only when the budget refused the
work, so the job is deferred), in three phases:

1. **Prepare** (a transaction). ``nextron.domain.budget.allow(BudgetClass.BLOCKABLE,
   meter.snapshot(now), queries.setup.budget_limits())`` comes first; a refusal returns
   ``False`` and nothing else happens. An unknown Lead (a text that isn't a ULID included) or a
   merged one logs ``legal_form_skipped`` (``lead_id``, ``reason``) and returns ``True``. A Lead
   whose country isn't ``GB`` enqueues ``select_primary_contact`` (below), logs
   ``legal_form_skipped`` (reason ``not_gb``) and returns ``True``. Otherwise it reads the
   Lead's name and its stored pages with text (``queries.research.crawled_pages``, by URL).
2. **The calls**, with no transaction open (``RegistryPort``):

   - **The number path.** The company numbers the pages quote (``find_company_numbers``), each
     once, in page order (pages by URL, then text order); up to ``MAX_NUMBERS`` (3) are looked
     up with ``company(number)``. A record links the Lead when it has that number and its
     normalised registered name equals the Lead's normalised name or occurs, as a whole-token
     run, in the citing page's text (so an unrelated number on the page links nothing). Exactly
     one distinct linking record is the link; none, or two or more (ambiguity), is no link.
   - **The name path**, when the number path gave no link. With no postcode on the pages
     (``find_postcodes``) or an empty normalised Lead name, there is no call. Otherwise one
     ``search_companies(<the Lead's name>)``: the candidates are the hits whose normalised name
     equals the Lead's and whose registered office postcode is a postcode the site quotes.
     Exactly one distinct candidate number is looked up with ``company(number)``, and links the
     Lead when the profile passes the same name and postcode checks; anything else is no link.

   A retryable ``AdapterError`` (``registry_rate_limited``, ``registry_unavailable``) logs
   ``legal_form_checked`` with its code and is raised again: nothing is recorded or enqueued,
   and the runner retries the job. Any other ``AdapterError`` (``credential_not_set``,
   ``registry_unauthorized``, ``registry_refused``, ``registry_bad_response``) keeps the Lead's
   earlier legal form as it is, enqueues ``select_primary_contact`` (in a transaction, when the
   Lead is still neither unknown nor merged), so research isn't blocked, logs
   ``legal_form_checked`` with its code and returns ``True``.
3. **Apply** (a transaction). The Lead is read again: unknown or merged now, it logs
   ``legal_form_skipped`` and returns ``True``. Otherwise, all of these or none:

   - with a link, ``discovery.set_ch_number(lead, number)`` (FR-9: the Lead may merge into an
     older Lead holding the number, which then survives). ``None`` (the Lead's survivor holds
     another number) is a conflict: it logs ``legal_form_conflict`` (``lead_id``) and the
     finding becomes ``UNLINKED``, recorded only when that survivor is the checked Lead itself
     (after a merge into another survivor nothing about the legal form is recorded, so its own
     legal form and Evidence stay);
   - ``research.record_legal_form(<the Lead's survivor>, finding)``: with a link, the
     ``LegalFormFinding`` of the record (``status_for(type, status)``, the site quote, the
     ``registry_quote`` and ``retrieved_at``, when the record was read); without one,
     ``UNLINKED`` (``unconfirmed``), except that a Lead whose stored finding is registry-only
     (Story 8.4: site Evidence pointer NULL, registry pointer set, as Companies House discovery
     records it) keeps it as it is: it stays ``unconfirmed`` either way (FR-12);
   - ``select_primary_contact`` for the survivor.

   Then it logs ``legal_form_checked`` (``lead_id``, ``status``, ``path``: ``number``, ``name``
   or ``none``, ``error_code``: ``None``) and returns ``True``.

``select_primary_contact`` is enqueued with Story 2.10's keys: scope the Lead, discriminator
``tick`` (a live job absorbs a repeat), payload ``{v, lead_id}`` and, when the Lead has a
``web_domain``, the concurrency key ``domain:<web_domain>``.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.db.uow import current, use_case
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.crawl import DOMAIN_KEY_PREFIX, LEAD_ID_FIELD, PAYLOAD_VERSION
from nextron.domain.job import TICK, VERSION_FIELD, Lane
from nextron.domain.legal_form import (
    UNLINKED,
    LegalFormFinding,
    NumberQuote,
    PostcodeQuote,
    SiteQuote,
    find_company_numbers,
    find_postcodes,
    name_occurs,
    normalise_company_name,
    postcode_key,
    registry_quote,
    status_for,
)
from nextron.domain.markets import GB
from nextron.domain.people import SELECT_PRIMARY_CONTACT_JOB
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.registry import RegistryCompany, RegistryPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

MAX_NUMBERS: Final = 3  # distinct site numbers looked up, in page order
LEGAL_FORM_SKIPPED: Final = "legal_form_skipped"
LEGAL_FORM_CHECKED: Final = "legal_form_checked"
LEGAL_FORM_CONFLICT: Final = "legal_form_conflict"
# Why a check was skipped, as logged
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
NOT_GB: Final = "not_gb"
# How the Lead was linked, as logged
PATH_NUMBER: Final = "number"
PATH_NAME: Final = "name"
PATH_NONE: Final = "none"


@dataclass(frozen=True, slots=True)
class _Page:
    """What the calls need of one stored page."""

    url: str = field(repr=False)
    fetched_at: datetime
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Check:
    """What phase 1 read for the calls."""

    lead_id: str
    name: str = field(repr=False)
    pages: tuple[_Page, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Link:
    """The record Evidence links the Lead to, and that Evidence."""

    record: RegistryCompany
    site: SiteQuote
    path: str
    retrieved_at: datetime

    def finding(self) -> LegalFormFinding:
        record = self.record
        return LegalFormFinding(
            status=status_for(record.company_type, record.company_status),
            number=record.number,
            legal_form=record.company_type,
            company_status=record.company_status,
            site=self.site,
            registry_quote=registry_quote(
                number=record.number,
                name=record.name,
                company_type=record.company_type,
                company_status=record.company_status,
                office_postcode=record.office_postcode,
            ),
            retrieved_at=self.retrieved_at,
        )


def _numbers(pages: Iterable[_Page]) -> list[tuple[NumberQuote, _Page]]:
    """Each company number the pages quote, once, in page order."""
    found: dict[str, tuple[NumberQuote, _Page]] = {}
    for page in pages:
        for quoted in find_company_numbers(page.text):
            found.setdefault(quoted.number, (quoted, page))
    return list(found.values())


def _postcodes(pages: Iterable[_Page]) -> dict[str, tuple[PostcodeQuote, _Page]]:
    """Each postcode the pages quote, by key, with its first quote in page order."""
    found: dict[str, tuple[PostcodeQuote, _Page]] = {}
    for page in pages:
        for quoted in find_postcodes(page.text):
            found.setdefault(quoted.key, (quoted, page))
    return found


def _registry_only(lead_id: str) -> bool:
    """Whether the Lead's stored legal form is registry-only (Story 8.4: Companies House
    discovery recorded the register's record, and no site Evidence links it yet)."""
    stored = research_queries.legal_form(lead_id)
    return (
        stored is not None
        and stored.site_evidence_id is None
        and stored.registry_evidence_id is not None
    )


class LegalFormChecks:
    """The ``check_legal_form`` jobs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        registry: RegistryPort,
        discovery: Discovery,
        research: Research,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._registry = registry
        self._discovery = discovery
        self._research = research
        self._queue = queue

    @use_case
    def check_legal_form(self, lead_id: str, *, lane: Lane | None = None) -> bool:
        """Confirm the GB Lead's legal form through Companies House; see the module
        docstring. ``False`` when the budget refused the work. ``select_primary_contact`` is
        enqueued on ``lane``, the lane the job ran on (Story 2.16; ``None``: its default)."""
        uow = current()
        check: _Check | None = None
        skipped: str | None = None
        with uow.transaction():
            now = self._clock.now()
            if not allow(
                BudgetClass.BLOCKABLE, self._meter.snapshot(now), setup_queries.budget_limits()
            ):
                return False
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                skipped = UNKNOWN_LEAD
            elif lead.merged_into is not None:
                skipped = MERGED_LEAD
            elif lead.country_code != GB:
                self._select(lead.id, lead.web_domain, lane)
                skipped = NOT_GB
            else:
                check = _Check(
                    lead.id,
                    lead.name,
                    tuple(
                        _Page(page.url, page.fetched_at, page.text)
                        for page in research_queries.crawled_pages(lead.id)
                        if page.text is not None
                    ),
                )
        if check is None:
            log.info(LEGAL_FORM_SKIPPED, extra={"lead_id": lead_id, "reason": skipped})
            return True
        path = PATH_NUMBER
        try:
            link = self._by_number(check)
            if link is None:
                path = PATH_NAME
                link = self._by_name(check)
        except AdapterError as error:
            return self._failed(check, path, error, lane)
        return self._apply(check, link, lane)

    # The calls ----------------------------------------------------------------------------------

    def _by_number(self, check: _Check) -> _Link | None:
        """The one record a company number on the pages links the Lead to, or ``None``."""
        lead_key = normalise_company_name(check.name)
        links: dict[str, _Link] = {}
        for quoted, page in _numbers(check.pages)[:MAX_NUMBERS]:
            record = self._registry.company(quoted.number)
            retrieved_at = self._clock.now()
            if record is None or record.number != quoted.number:
                continue
            key = normalise_company_name(record.name)
            if key and (key == lead_key or name_occurs(key, page.text)):
                site = SiteQuote(page.url, quoted.quote, page.fetched_at)
                links.setdefault(record.number, _Link(record, site, PATH_NUMBER, retrieved_at))
        return next(iter(links.values())) if len(links) == 1 else None

    def _by_name(self, check: _Check) -> _Link | None:
        """The one record the Lead's name and a site postcode link it to, or ``None``."""
        lead_key = normalise_company_name(check.name)
        postcodes = _postcodes(check.pages)
        if not postcodes or not lead_key:
            return None  # no call
        hits = self._registry.search_companies(check.name)
        candidates = {
            hit.number
            for hit in hits
            if normalise_company_name(hit.name) == lead_key
            and postcode_key(hit.office_postcode) in postcodes
        }
        if len(candidates) != 1:
            return None
        [number] = candidates
        record = self._registry.company(number)
        retrieved_at = self._clock.now()
        if record is None or record.number != number:
            return None
        key = postcode_key(record.office_postcode)
        if normalise_company_name(record.name) != lead_key or key is None or key not in postcodes:
            return None
        quoted, page = postcodes[key]
        site = SiteQuote(page.url, quoted.quote, page.fetched_at)
        return _Link(record, site, PATH_NAME, retrieved_at)

    def _failed(self, check: _Check, path: str, error: AdapterError, lane: Lane | None) -> bool:
        """A registry call failed: raise a retryable error again; after any other, keep the
        earlier legal form and enqueue the selection."""
        if error.retryable:
            self._log_checked(check.lead_id, None, path, error.code)
            raise error  # the runner retries the job
        with current().transaction():
            lead = discovery_queries.lead(check.lead_id)
            if lead is not None and lead.merged_into is None:
                self._select(lead.id, lead.web_domain, lane)
        self._log_checked(check.lead_id, None, path, error.code)
        return True

    # Phase 3 ------------------------------------------------------------------------------------

    def _apply(self, check: _Check, link: _Link | None, lane: Lane | None) -> bool:
        conflict = False
        finding: LegalFormFinding = UNLINKED
        with current().transaction():
            lead = discovery_queries.lead(check.lead_id)
            if lead is None or lead.merged_into is not None:
                skipped = UNKNOWN_LEAD if lead is None else MERGED_LEAD
            else:
                skipped = None
                target = lead.id
                if link is not None:
                    owner = self._discovery.set_ch_number(lead.id, link.record.number)
                    if owner is None:  # the survivor holds another number: never overwritten
                        conflict = True
                        survivor = discovery_queries.survivor_of(lead.id)
                        if survivor is None:  # set_ch_number followed the same chain
                            raise RuntimeError(f"Lead {lead.id} has no survivor")
                        target = survivor.id
                    else:
                        target = owner
                        finding = link.finding()
                if (not conflict or target == lead.id) and not (
                    not finding.linked and _registry_only(target)
                ):
                    # after a merge into another survivor, a conflict records nothing: that
                    # survivor's own legal form and Evidence stay as they are; and no link
                    # leaves a registry-only finding (Story 8.4) in place
                    self._research.record_legal_form(target, finding)
                survivor = discovery_queries.lead(target)
                if survivor is None:  # Leads are never deleted
                    raise RuntimeError(f"Lead {target} has no row")
                self._select(survivor.id, survivor.web_domain, lane)
        if skipped is not None:
            log.info(LEGAL_FORM_SKIPPED, extra={"lead_id": check.lead_id, "reason": skipped})
            return True
        if conflict:
            log.warning(LEGAL_FORM_CONFLICT, extra={"lead_id": check.lead_id})
        path = PATH_NONE if not finding.linked or link is None else link.path
        self._log_checked(check.lead_id, str(finding.status), path, None)
        return True

    def _select(self, lead_id: str, web_domain: str | None, lane: Lane | None) -> None:
        """Enqueue the Lead's ``select_primary_contact`` job on ``lane`` in the caller's
        transaction."""
        self._queue.enqueue(
            SELECT_PRIMARY_CONTACT_JOB,
            scope=lead_id,
            discriminator=TICK,
            payload={VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead_id},
            lane=lane,
            concurrency_key=None if web_domain is None else f"{DOMAIN_KEY_PREFIX}{web_domain}",
        )

    @staticmethod
    def _log_checked(lead_id: str, status: str | None, path: str, error_code: str | None) -> None:
        log.info(
            LEGAL_FORM_CHECKED,
            extra={"lead_id": lead_id, "status": status, "path": path, "error_code": error_code},
        )
