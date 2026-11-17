"""A Lead's identity and source (FR-9, AD-3). Standard library only.

**``LeadSource``** is where a candidate Lead came from: ``csv``, ``signal_inbox``, ``places``,
``companies_house`` or ``job_aggregator``. It has no column: the Lead's creation ``events``
row records it as the cause ``created_from_<source>`` (``nextron.domain.pipeline_stage``).

**``LeadIdentity(name, segment_id, web_domain=None, ch_number=None, place_id=None,
country_code=None)``** is what ``Discovery.upsert_lead`` matches and stores. It is frozen and
checks itself when built:

- ``name``: a ``str`` of 1 to 200 characters, equal to its own strip, with no ``Cc``, ``Cf``,
  ``Zl`` or ``Zp`` character;
- ``segment_id``: a ULID;
- ``web_domain``, ``ch_number`` and ``place_id`` (the three keys): each ``None`` or already
  normalised by ``nextron.domain.identity`` (``registrable_domain``,
  ``normalize_company_number`` and ``normalize_place_id`` return it unchanged);
- ``country_code``: ``None`` or in ``identity.COUNTRY_CODES``;
- at least one of the three keys is set, because a Lead without one could never be matched.

A problem raises ``InvalidLeadIdentity(field, reason)`` (code ``invalid_lead_identity``, message
``"<field>: <reason>"``), never showing the value.

**``MergeRefused(reason)``** (code ``merge_refused``) is ``Discovery.merge`` refusing two Leads:
the same Lead twice, an unknown Lead, a Lead already merged, or a survivor newer than the loser.

**Shared platforms.** ``is_shared_platform(web_domain)`` is true when a registrable domain is
one that many unrelated organizations share, so it can't key one organization's Lead: it is in
``SHARED_PLATFORM_DOMAINS`` (site builders such as ``wix.com``, profile and link pages such as
``linktr.ee``, directories such as ``yelp.com`` and ``lawsociety.org.uk``, review sites and
business registers such as ``trustpilot.com`` and ``company-information.service.gov.uk``, and
URL shorteners such as ``bit.ly``), or its first label is in
``SHARED_PLATFORM_BRANDS`` (``google``, ``facebook``: ``sites.google.com`` and
``facebook.co.uk`` key as ``google.com`` and ``facebook.co.uk``). A site under a public-suffix
platform (``x.wixsite.com``, ``x.blogspot.com``) already keys as its own registrable domain and
isn't listed. Story 2.3's CSV import refuses such a website rather than merge unrelated firms.
"""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.identity import (
    COUNTRY_CODES,
    IdentityError,
    normalize_company_number,
    normalize_place_id,
    registrable_domain,
)
from nextron.domain.ids import is_ulid

MAX_NAME_LENGTH: Final = 200
KEY_FIELDS: Final = ("web_domain", "ch_number", "place_id")
# control, format, line separator and paragraph separator characters
REFUSED_NAME_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})

# Registrable domains that many unrelated organizations share (each one's own, as
# registrable_domain gives it: tests/unit/test_leads_domain.py checks).
SHARED_PLATFORM_DOMAINS: Final = frozenset(
    {
        # site builders
        "business.site",
        "company.site",
        "godaddysites.com",
        "squarespace.com",
        "wordpress.com",
        "weebly.com",
        "jimdosite.com",
        "wix.com",
        "mailchimpsites.com",
        "strikingly.com",
        "mystrikingly.com",
        "site123.me",
        "ueniweb.com",
        "webnode.com",
        "hubspotpagebuilder.com",
        "myportfolio.com",
        # profile and link pages
        "tumblr.com",
        "substack.com",
        "medium.com",
        "linktr.ee",
        "about.me",
        "g.page",
        "fb.com",
        "x.com",
        "twitter.com",
        "youtube.com",
        "tiktok.com",
        "pinterest.com",
        "threads.net",
        "bsky.app",
        "calendly.com",
        # directories
        "yelp.com",
        "yell.com",
        "avvo.com",
        "justia.com",
        "findlaw.com",
        "lawyers.com",
        "martindale.com",
        "lawsociety.org.uk",
        "sra.org.uk",
        "chambers.com",
        "legal500.com",
        # review sites and business registers
        "trustpilot.com",
        "checkatrade.com",
        "yellowpages.com",
        "bbb.org",
        "company-information.service.gov.uk",
        "opencorporates.com",
        # URL shorteners
        "bit.ly",
        "goo.gl",
        "g.co",
        "share.google",
        "tinyurl.com",
        "t.co",
        "ow.ly",
        "rebrand.ly",
        "cutt.ly",
        "is.gd",
        "buff.ly",
    }
)
# First labels of registrable domains that are shared platforms under any suffix.
SHARED_PLATFORM_BRANDS: Final = frozenset({"google", "facebook"})

_NORMALISERS: Final[dict[str, Callable[[str], str]]] = {
    "web_domain": registrable_domain,
    "ch_number": normalize_company_number,
    "place_id": normalize_place_id,
}


class LeadSource(StrEnum):
    CSV = "csv"
    SIGNAL_INBOX = "signal_inbox"
    PLACES = "places"
    COMPANIES_HOUSE = "companies_house"
    JOB_AGGREGATOR = "job_aggregator"


class InvalidLeadIdentity(DomainError):
    """A ``LeadIdentity`` field that breaks its rule; the message names the field, never its
    value."""

    code = "invalid_lead_identity"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class MergeRefused(DomainError):
    """``Discovery.merge`` refused to merge two Leads; the message is the reason."""

    code = "merge_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_name(name: object) -> None:
    """Raise ``InvalidLeadIdentity("name", ...)`` unless ``name`` follows ``LeadIdentity``'s
    name rule (the CSV import checks a row's name with it before building the identity)."""
    if not isinstance(name, str):
        raise InvalidLeadIdentity("name", "must be a str")
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        raise InvalidLeadIdentity("name", f"must be 1 to {MAX_NAME_LENGTH} characters")
    if name != name.strip():
        raise InvalidLeadIdentity("name", "must not start or end with whitespace")
    if any(unicodedata.category(ch) in REFUSED_NAME_CATEGORIES for ch in name):
        raise InvalidLeadIdentity("name", "must not hold a control or format character")


def _check_key(field: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise InvalidLeadIdentity(field, "must be a str or None")
    try:
        normalised = _NORMALISERS[field](value)
    except IdentityError as error:
        raise InvalidLeadIdentity(field, error.reason) from None
    if normalised != value:
        raise InvalidLeadIdentity(field, "is not in normalised form")


@dataclass(frozen=True, slots=True)
class LeadIdentity:
    """The name, Segment and keys of a candidate Lead; see the module docstring."""

    name: str
    segment_id: str
    web_domain: str | None = None
    ch_number: str | None = None
    place_id: str | None = None
    country_code: str | None = None

    def __post_init__(self) -> None:
        check_name(self.name)
        if not is_ulid(self.segment_id):
            raise InvalidLeadIdentity("segment_id", "must be a ULID")
        for field in KEY_FIELDS:
            _check_key(field, getattr(self, field))
        if self.country_code is not None and not (
            isinstance(self.country_code, str) and self.country_code in COUNTRY_CODES
        ):
            raise InvalidLeadIdentity("country_code", "must be an ISO 3166-1 alpha-2 code")
        if all(getattr(self, field) is None for field in KEY_FIELDS):
            raise InvalidLeadIdentity("keys", "set at least one of web_domain, ch_number, place_id")

    def keys(self) -> dict[str, str]:
        """The keys that are set, by field name, in ``KEY_FIELDS`` order."""
        return {
            field: value for field in KEY_FIELDS if (value := getattr(self, field)) is not None
        }


def is_shared_platform(web_domain: str) -> bool:
    """Whether the registrable domain ``web_domain`` is a shared platform; see the module
    docstring."""
    if not isinstance(web_domain, str):
        raise TypeError(f"is_shared_platform takes a str, not {type(web_domain).__name__}")
    return (
        web_domain in SHARED_PLATFORM_DOMAINS
        or web_domain.split(".", 1)[0] in SHARED_PLATFORM_BRANDS
    )
