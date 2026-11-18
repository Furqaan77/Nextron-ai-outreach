"""The rules of a Places discovery run (Story 8.3; FR-5, FR-9, FR-49, AD-7, AD-10). Standard
library and ``nextron.domain`` only.

**The job.** ``DISCOVER_PLACES_JOB`` (``discover_places``) runs one Campaign's discovery in one
location, on the bulk lane, keyed ``discover_places:<campaign ULID>:<location hash>``. Its
payload is ``{v: 1, campaign_id, location, country_code}``: ``location`` is the location's
hash (``location_hash``) and ``country_code`` its country code in lowercase, so the payload
holds only a ULID and tokens (``nextron.domain.job``'s payload rules refuse free text); the run
finds the location's text again in the ``places_discovery_locations`` setting. Every such job
carries ``PLACES_CONCURRENCY_KEY`` (``domain:places.googleapis.com``), so two runs never search
Places at the same time.

**Locations.** Each item of the ``places_discovery_locations`` setting is ``<location> | <CC>``
(for example ``Columbus, OH | US``). ``parse_location(item)`` returns its
``DiscoveryLocation(location, country_code)``: the text before the last ``|`` and the code after
it, each stripped; the location is 1 to ``MAX_LOCATION_LENGTH`` (100) characters with no ``|``
and no control or format character, and the code an ISO 3166-1 alpha-2 code in uppercase
(``identity.COUNTRY_CODES``). Anything else raises ``ValueError``; ``is_location_item(item)``
says whether it would parse. ``location_hash(location, country_code)`` is the first 16
lowercase hex characters of the SHA-256 of ``<location>|<CC>``: the job key's discriminator.

**Queries.** A run searches its Segment's Places queries in their seed order. ``query_hash``
of them is the first 16 lowercase hex characters of the SHA-256 of the queries joined by
``\\n``: the run records it, never the queries.

**Pages.** ``MAX_PAGES_PER_RUN`` (3) is the most pages a run may fetch (a Text Search returns
at most three pages); the ``places_max_pages_per_run`` setting (1 to 3, default 3) caps the
whole run.

**The site's name.** ``site_name(html)`` is the Lead's name from its own homepage: the first
``<meta property="og:site_name" content="...">`` that isn't blank, else the first ``<title>``'s
text; whitespace runs become one space, control, format and line or paragraph separator characters
are removed, and the result is cut to ``leads.MAX_NAME_LENGTH`` (200) characters and stripped.
``None`` when neither yields a name ``leads.check_name`` accepts. A Places display name is never
an input.

**Runs.** ``RunStatus``: ``running``, ``finished``, ``deferred`` (the budget refused a page),
``failed`` (an error ended it) or ``stopped`` (the Campaign could no longer run when the job
ran: nothing fetched). ``Outcome``: what one candidate came to (``created``, ``merged``,
``suppressed``, ``no_website``, ``market_blocked``), each a count on the run besides ``seen``.
"""

import hashlib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import Final

from nextron.domain.identity import COUNTRY_CODES
from nextron.domain.leads import MAX_NAME_LENGTH, REFUSED_NAME_CATEGORIES, InvalidLeadIdentity
from nextron.domain.leads import check_name as check_lead_name

DISCOVER_PLACES_JOB: Final = "discover_places"
PLACES_CONCURRENCY_KEY: Final = "domain:places.googleapis.com"
SOURCE_PLACES: Final = "places"  # discovery_runs.source
PAYLOAD_VERSION: Final = 1
CAMPAIGN_ID_FIELD: Final = "campaign_id"
LOCATION_FIELD: Final = "location"
COUNTRY_CODE_FIELD: Final = "country_code"
MAX_LOCATION_LENGTH: Final = 100
MAX_PAGES_PER_RUN: Final = 3
DEFAULT_PAGES_PER_RUN: Final = 3
HASH_LENGTH: Final = 16
SEPARATOR: Final = "|"
SITE_NAME_PROPERTY: Final = "og:site_name"


class RunStatus(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    DEFERRED = "deferred"
    FAILED = "failed"
    STOPPED = "stopped"


class Outcome(StrEnum):
    """What one candidate came to; each value is a count column of ``discovery_runs``."""

    CREATED = "created"
    MERGED = "merged"
    SUPPRESSED = "suppressed"
    NO_WEBSITE = "no_website"
    MARKET_BLOCKED = "market_blocked"


COUNT_FIELDS: Final = ("seen", *(str(outcome) for outcome in Outcome))


@dataclass(frozen=True, slots=True)
class DiscoveryLocation:
    """One item of the ``places_discovery_locations`` setting."""

    location: str
    country_code: str

    @property
    def hash(self) -> str:
        return location_hash(self.location, self.country_code)

    @property
    def text(self) -> str:
        """The item as the setting writes it."""
        return f"{self.location} {SEPARATOR} {self.country_code}"


def parse_location(item: str) -> DiscoveryLocation:
    """The setting item ``item`` as a ``DiscoveryLocation``; ``ValueError`` when it isn't one."""
    if not isinstance(item, str):
        raise ValueError("a location item must be text")
    location, separator, code = item.rpartition(SEPARATOR)
    if not separator:
        raise ValueError("a location item is <location> | <country code>")
    location = location.strip()
    code = code.strip()
    if not 1 <= len(location) <= MAX_LOCATION_LENGTH:
        raise ValueError(f"a location is 1 to {MAX_LOCATION_LENGTH} characters")
    if SEPARATOR in location or any(
        unicodedata.category(char) in REFUSED_NAME_CATEGORIES for char in location
    ):
        raise ValueError("a location holds no | and no control or format character")
    if code not in COUNTRY_CODES:
        raise ValueError("a location's country is an ISO 3166-1 alpha-2 code in uppercase")
    return DiscoveryLocation(location, code)


def is_location_item(item: str) -> bool:
    """Whether ``parse_location(item)`` accepts ``item``."""
    try:
        parse_location(item)
    except ValueError:
        return False
    return True


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def location_hash(location: str, country_code: str) -> str:
    """The job key's discriminator for one location; see the module docstring."""
    return _short_hash(f"{location}{SEPARATOR}{country_code}")


def query_hash(queries: Iterable[str]) -> str:
    """The run's record of the queries it searched; see the module docstring."""
    return _short_hash("\n".join(queries))


class _NameParser(HTMLParser):
    """The first ``og:site_name`` content and the first ``<title>`` text of a document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.site_names: list[str] = []
        self.titles: list[str] = []
        self._title: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            values = {name.lower(): value for name, value in attrs}
            prop = (values.get("property") or values.get("name") or "").strip().lower()
            content = values.get("content")
            if prop == SITE_NAME_PROPERTY and content and content.strip():
                self.site_names.append(content)
        elif tag == "title" and self._title is None and not self.titles:
            self._title = []

    def handle_data(self, data: str) -> None:
        if self._title is not None:
            self._title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title is not None:
            self.titles.append("".join(self._title))
            self._title = None


def clean_name(text: str) -> str | None:
    """``text`` as a Lead name (whitespace collapsed, control and format characters removed,
    cut and stripped), or ``None`` when that isn't a valid name."""
    kept = "".join(
        char for char in text if unicodedata.category(char) not in REFUSED_NAME_CATEGORIES
    )
    name = " ".join(kept.split())[:MAX_NAME_LENGTH].strip()
    try:
        check_lead_name(name)
    except InvalidLeadIdentity:
        return None
    return name


def site_name(html: str) -> str | None:
    """The Lead's name from its homepage's HTML; see the module docstring."""
    if not isinstance(html, str):
        raise TypeError("site_name takes the page's HTML as text")
    parser = _NameParser()
    try:
        parser.feed(html)
        parser.close()
    except AssertionError:  # html.parser's way of giving up on badly broken markup
        pass
    if parser._title is not None:  # an unclosed <title>: its text so far
        parser.titles.append("".join(parser._title))
    for candidate in (*parser.site_names[:1], *parser.titles[:1]):
        name = clean_name(candidate)
        if name is not None:
            return name
    return None
