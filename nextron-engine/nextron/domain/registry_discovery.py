"""The rules of a Companies House discovery run (Story 8.4; FR-6, FR-9, FR-12, FR-13, AD-7,
AD-10). Standard library and ``nextron.domain`` only.

**The job.** ``DISCOVER_REGISTRY_JOB`` (``discover_registry``) runs one UK Campaign's
Companies House discovery on the bulk lane, keyed ``discover_registry:<campaign ULID>:<query
hash>``; its payload is ``{v: 1, campaign_id}`` and the run reads the Segment's current queries.
Every such job carries ``REGISTRY_CONCURRENCY_KEY``
(``domain:api.company-information.service.gov.uk``), so two runs never search the register at
the same time.

**Queries.** ``registry_searches(queries)`` are the searches one run makes, in order: each
keyword with all the Segment's SIC codes, or, with no keywords, one SIC-only search
(``(None, codes)``); none without SIC codes (a request refuses such a Segment).
``registry_query_hash(queries)`` is the first 16 lowercase hex characters of the SHA-256 of the
canonical JSON ``{"keywords": [...], "sic_codes": [...]}`` (sorted keys, no spaces): the job
key's discriminator and the run's record, never the queries.

**Pages.** The ``registry_max_pages_per_run`` setting (1 to ``MAX_PAGES_PER_RUN``, 10; default
``DEFAULT_PAGES_PER_RUN``, 3) caps the whole run's search pages, in query order; the next page
of a search is fetched only after a full page of ``nextron.ports.registry.PAGE_SIZE`` (20).

**Officers.** ``officer_name(raw)`` turns the register's ``SURNAME, Forenames`` into
``Forenames Surname``: the text before the first comma is the surname (title-cased when it is
all capitals, else kept), the rest the forenames (any further commas read as spaces), whitespace
collapsed; a text without a comma is kept as it is. ``None`` when the result isn't a Contact
name (``contacts.check_name``). ``kept_officer(officer, segment)`` is the Contact an officer
becomes, or ``None``: only an officer who hasn't resigned, whose role is ``director`` or
``llp-designated-member`` (``KEPT_ROLES``: never a ``corporate-*`` role), whose ``occupation``
is set and matches one of the Segment's decision-maker titles (``contacts.role_match``), and
whose name ``officer_name`` accepts. The Contact's role is the occupation; it has no email.

**Accounts.** ``is_inactive(company_status, accounts_category)``: any status but ``active``
(an unknown one included), or the latest accounts' category ``dormant``.
"""

import hashlib
import json
from typing import Final, Protocol

from nextron.domain.contacts import ContactCandidate, InvalidContact, check_name, role_match
from nextron.domain.legal_form import ACTIVE, DORMANT
from nextron.domain.place_discovery import (
    DEFAULT_REGISTRY_PAGES_PER_RUN as DEFAULT_PAGES_PER_RUN,
)
from nextron.domain.place_discovery import MAX_REGISTRY_PAGES_PER_RUN as MAX_PAGES_PER_RUN
from nextron.domain.segments import RegistryQueries, SegmentDef

DISCOVER_REGISTRY_JOB: Final = "discover_registry"
REGISTRY_CONCURRENCY_KEY: Final = "domain:api.company-information.service.gov.uk"
PAYLOAD_VERSION: Final = 1
CAMPAIGN_ID_FIELD: Final = "campaign_id"
HASH_LENGTH: Final = 16
DIRECTOR: Final = "director"
LLP_DESIGNATED_MEMBER: Final = "llp-designated-member"
KEPT_ROLES: Final = frozenset({DIRECTOR, LLP_DESIGNATED_MEMBER})
OFFICERS_DETAIL: Final = "Companies House API officers, company {number}"


class OfficerFacts(Protocol):
    """What ``kept_officer`` reads of an officer (``nextron.ports.registry.RegistryOfficer``
    fits)."""

    @property
    def name(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def occupation(self) -> str | None: ...

    @property
    def resigned(self) -> bool: ...


def registry_searches(queries: RegistryQueries) -> tuple[tuple[str | None, tuple[str, ...]], ...]:
    """The ``(keyword, sic_codes)`` searches of one run, in order; see the module docstring."""
    if not isinstance(queries, RegistryQueries):
        raise TypeError("registry_searches takes RegistryQueries")
    if not queries.sic_codes:
        return ()
    if not queries.keywords:
        return ((None, queries.sic_codes),)
    return tuple((keyword, queries.sic_codes) for keyword in queries.keywords)


def registry_query_hash(queries: RegistryQueries) -> str:
    """The job key's discriminator and the run's record of its queries."""
    if not isinstance(queries, RegistryQueries):
        raise TypeError("registry_query_hash takes RegistryQueries")
    text = json.dumps(
        {"keywords": list(queries.keywords), "sic_codes": list(queries.sic_codes)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def is_inactive(company_status: str | None, accounts_category: str | None) -> bool:
    """Whether a company is discarded as inactive: not ``active``, or dormant accounts."""
    return company_status != ACTIVE or accounts_category == DORMANT


def officer_name(raw: str) -> str | None:
    """The register's officer name as a Contact name; see the module docstring."""
    if not isinstance(raw, str):
        raise TypeError("officer_name takes the register's name as a str")
    surname, comma, forenames = raw.partition(",")
    if comma:
        surname = " ".join(surname.split())
        if surname.isupper():
            surname = surname.title()
        name = " ".join(f"{forenames.replace(',', ' ')} {surname}".split())
    else:
        name = " ".join(raw.split())
    try:
        check_name(name)
    except InvalidContact:
        return None
    return name


def kept_officer(officer: OfficerFacts, segment: SegmentDef) -> ContactCandidate | None:
    """The Contact the officer becomes, or ``None`` when it is discarded; see the module
    docstring."""
    if not isinstance(segment, SegmentDef):
        raise TypeError("kept_officer takes a SegmentDef")
    if officer.resigned or officer.role not in KEPT_ROLES or officer.occupation is None:
        return None
    if role_match(officer.occupation, segment) is None:
        return None
    name = officer_name(officer.name)
    if name is None:
        return None
    try:
        return ContactCandidate(name=name, role=officer.occupation)
    except InvalidContact:
        return None


def officers_detail(number: str) -> str:
    """The Provenance detail of an officer's Contact: the API and the company number."""
    return OFFICERS_DETAIL.format(number=number)
