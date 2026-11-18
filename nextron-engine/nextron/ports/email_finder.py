"""The email-finder ports (FR-14, FR-57, AD-9, INT-6, Story 2.13): a person's address asked of
a paid finder, and a domain's people and address pattern asked of Hunter's Domain Search.

AD-9's ``EmailFinderPort`` names three operations (find, verify and Domain Search), but not
every provider serves all three, so each is its own Protocol here: a Protocol method a provider
can't honour would only fail at run time. Story 2.14 adds verification to this module.

- **``EmailFinderPort.find_email(query) -> FinderAnswer``** asks for one person's address at a
  domain. ``nextron.adapters.hunter.HunterFinder`` (Hunter's Email Finder) and
  ``nextron.adapters.prospeo.ProspeoFinder`` (Prospeo's Enrich Person) implement it; tests use
  ``tests.fakes.email_finder.ScriptedFinder``.
- **``DomainSearchPort.domain_search(domain) -> DomainSearchAnswer``** asks for a domain's
  address pattern and the people the provider knows there. ``HunterFinder`` implements it;
  tests use ``tests.fakes.email_finder.ScriptedDomainSearch``.

**Values.** Every value is frozen and checks its fields when built (``TypeError`` for a wrong
type, ``ValueError`` for a wrong value; neither message shows the value):

- ``FinderQuery(domain, first_name, last_name)``: three non-blank ``str``s.
- ``FinderAnswer(email, claimed=False)``: ``email`` is the address found (a non-blank ``str``)
  or ``None``. ``claimed=True`` (only with ``email=None``) means the person asked the provider
  not to process their address (Hunter's ``claimed_email``), so the search must stop.
- ``DomainPerson(email, first_name, last_name, personal)``: one address the provider knows at
  the domain; ``email`` is a non-blank ``str``, each name a ``str`` or ``None``, and
  ``personal`` says the provider classes it as a person's address rather than a generic one.
- ``DomainSearchAnswer(pattern, people)``: ``pattern`` is the provider's raw pattern text (such
  as Hunter's ``"{first}.{last}"``; ``nextron.domain.email_finding`` maps it) or ``None``, and
  ``people`` a tuple of ``DomainPerson``.

Names and addresses never show in a ``repr``; a query's domain and an answer's pattern do.

**Failures.** Every failure is an ``AdapterError`` whose message carries no key, URL, domain,
name, address or response body: ``<provider>_unavailable`` (retryable: no answer, a rate
limit, a 5xx), ``<provider>_refused`` (the provider refused the request) and
``<provider>_bad_answer`` (a 2xx that isn't the expected JSON), plus ``read_credential``'s own
``credential_not_set`` and ``keyring_failed``, raised before any request.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


def _text(value: object, what: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str")
    if not value.strip():
        raise ValueError(f"{what} must not be blank")


def _optional_text(value: object, what: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{what} must be a str or None")


@dataclass(frozen=True, slots=True)
class FinderQuery:
    """One person to find an address for, at one domain. The names never show in the
    ``repr``."""

    domain: str
    first_name: str = field(repr=False)
    last_name: str = field(repr=False)

    def __post_init__(self) -> None:
        _text(self.domain, "a finder query's domain")
        _text(self.first_name, "a finder query's first name")
        _text(self.last_name, "a finder query's last name")


@dataclass(frozen=True, slots=True)
class FinderAnswer:
    """A finder's answer: the address found, or ``None``. ``claimed`` (only without an address)
    means the person asked the provider not to process their address. The address never shows
    in the ``repr``."""

    email: str | None = field(repr=False)
    claimed: bool = False

    def __post_init__(self) -> None:
        if self.email is not None:
            _text(self.email, "a finder answer's email")
        if not isinstance(self.claimed, bool):
            raise TypeError("a finder answer's claimed must be a bool")
        if self.claimed and self.email is not None:
            raise ValueError("a claimed finder answer carries no email")


@dataclass(frozen=True, slots=True)
class DomainPerson:
    """One address a Domain Search knows at the domain, with the names it gives. The address
    and the names never show in the ``repr``."""

    email: str = field(repr=False)
    first_name: str | None = field(repr=False)
    last_name: str | None = field(repr=False)
    personal: bool

    def __post_init__(self) -> None:
        _text(self.email, "a domain person's email")
        _optional_text(self.first_name, "a domain person's first name")
        _optional_text(self.last_name, "a domain person's last name")
        if not isinstance(self.personal, bool):
            raise TypeError("a domain person's personal must be a bool")


@dataclass(frozen=True, slots=True)
class DomainSearchAnswer:
    """A Domain Search's answer: the provider's raw pattern text (or ``None``) and the people
    it knows at the domain. The people never show in the ``repr``."""

    pattern: str | None
    people: tuple[DomainPerson, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _optional_text(self.pattern, "a domain search's pattern")
        if not isinstance(self.people, tuple) or not all(
            isinstance(person, DomainPerson) for person in self.people
        ):
            raise TypeError("a domain search's people must be a tuple of DomainPerson")


@runtime_checkable
class EmailFinderPort(Protocol):
    """Finds one person's address at a domain."""

    def find_email(self, query: FinderQuery) -> FinderAnswer:
        """The address found for ``query``, or ``FinderAnswer(None)``; see the module
        docstring."""
        ...


@runtime_checkable
class DomainSearchPort(Protocol):
    """Reads a domain's address pattern and the people a provider knows there."""

    def domain_search(self, domain: str) -> DomainSearchAnswer:
        """The pattern and people known at ``domain``; see the module docstring."""
        ...
