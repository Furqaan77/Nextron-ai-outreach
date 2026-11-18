"""The rules of checking a Contact's email address and assigning its Address Class (FR-15,
FR-57, FR-59, AD-4, AD-9; Story 2.14). Standard library and ``nextron.domain`` only.

The ``verify_contact_email`` job (``nextron.app.research.verification``) runs the free offline
checks (syntax, the disposable-domain list, MX) on an address, then asks Hunter's Email Verifier
only about the Primary Contact's ``guessed`` address, once per address. These rules hold the
codes it stores and the list it reads; nothing here reads the database, DNS or a provider.

**Constants.** ``VERIFY_CONTACT_EMAIL_JOB`` (``verify_contact_email``), ``BUDGET_CLASS``
(``BudgetClass.BLOCKABLE``), ``HUNTER_QUOTA`` (``hunter``, a ``budget.QUOTAS`` name) and
``VERIFIER_DETAIL`` ("Hunter Email Verifier", the ``finder`` Provenance detail of a Hunter
result).

**Results.** ``contacts.email_check_result`` holds the last check's result: a ``CheckResult``
(``checks_passed``, ``invalid``, ``suppressed``, ``quota_reached``) or, after Hunter answered,
its status through ``normalise_status`` (``valid``, ``invalid``, ``accept_all``, ...).
``normalise_status(text)`` is the stripped, lower-cased status when it matches ``[a-z_]{1,32}``
and isn't one of ``RESERVED_RESULTS`` (``checks_passed``, ``suppressed``, ``quota_reached``),
else ``other``. ``VALID_STATUS`` (``valid``) verifies the address and ``INVALID_STATUS``
(``invalid``) invalidates it; any other status leaves it ``guessed``.

**Reasons.** ``InvalidReason`` (``contacts.email_invalid_reason``): ``syntax``, ``disposable``,
``no_mx``, ``null_mx``, ``verifier_invalid``. ``REASON_TEXT`` is each one's text on the Lead
page.

**The disposable list.** ``disposable_domains()`` is the vendored list
``data/disposable_domains.txt`` (the ``disposable-email-domains`` project, CC0, at
``DISPOSABLE_COMMIT``; its header records the source and the list's SHA-256,
``DISPOSABLE_SHA256``), read once through ``importlib.resources``: one lower-case domain per
line, ``#`` lines and blank lines skipped. ``is_disposable(domain)``: the domain (lower-cased,
final dot dropped) or its registrable domain (``identity.registrable_domain``; a domain it
refuses counts as itself only) is in the list.

**MX.** ``mx_reason(records)``: ``no_mx`` for no records, ``null_mx`` when every record is a
null MX (an empty ``exchange``, RFC 7505), else ``None`` (the domain receives mail).

A non-``str`` where text is expected raises ``TypeError``.
"""

import functools
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from importlib import resources
from types import MappingProxyType
from typing import Final, Protocol

from nextron.domain.budget import BudgetClass
from nextron.domain.identity import IdentityError, registrable_domain

VERIFY_CONTACT_EMAIL_JOB: Final = "verify_contact_email"
BUDGET_CLASS: Final = BudgetClass.BLOCKABLE
HUNTER_QUOTA: Final = "hunter"
VERIFIER_DETAIL: Final = "Hunter Email Verifier"
DISPOSABLE_PATH: Final = ("data", "disposable_domains.txt")
DISPOSABLE_COMMIT: Final = "f4cad16eb9fa2cd9ceb352ff5a8c213a25a10083"
DISPOSABLE_SHA256: Final = "d0ee73b42a7e8fc7aa679b39e7e661244a6d3e09bc7e2235901614706e540ae8"
COMMENT: Final = "#"
STATUS_PATTERN: Final = re.compile(r"[a-z_]{1,32}")
OTHER_STATUS: Final = "other"
VALID_STATUS: Final = "valid"
INVALID_STATUS: Final = "invalid"


class CheckResult(StrEnum):
    CHECKS_PASSED = "checks_passed"
    INVALID = "invalid"
    SUPPRESSED = "suppressed"
    QUOTA_REACHED = "quota_reached"


RESERVED_RESULTS: Final = frozenset(
    {CheckResult.CHECKS_PASSED, CheckResult.SUPPRESSED, CheckResult.QUOTA_REACHED}
)


class InvalidReason(StrEnum):
    SYNTAX = "syntax"
    DISPOSABLE = "disposable"
    NO_MX = "no_mx"
    NULL_MX = "null_mx"
    VERIFIER_INVALID = "verifier_invalid"


REASON_TEXT: Final[Mapping[str, str]] = MappingProxyType(
    {
        InvalidReason.SYNTAX: "the address isn't well-formed",
        InvalidReason.DISPOSABLE: "a disposable-address domain",
        InvalidReason.NO_MX: "the domain has no mail server (no MX record)",
        InvalidReason.NULL_MX: "the domain accepts no mail (null MX)",
        InvalidReason.VERIFIER_INVALID: "Hunter says the address doesn't exist",
    }
)


class _MxLike(Protocol):
    @property
    def exchange(self) -> str: ...


def _check_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def normalise_status(text: str) -> str:
    """A verifier's status as stored; see the module docstring."""
    status = _check_str(text, "status").strip().lower()
    if STATUS_PATTERN.fullmatch(status) is None or status in RESERVED_RESULTS:
        return OTHER_STATUS
    return status


def _parse(text: str) -> frozenset[str]:
    return frozenset(
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(COMMENT)
    )


@functools.cache
def disposable_domains() -> frozenset[str]:
    """The vendored disposable-domain list, read once."""
    text = resources.files(__package__).joinpath(*DISPOSABLE_PATH).read_text(encoding="utf-8")
    return _parse(text)


def is_disposable(domain: str) -> bool:
    """Whether ``domain`` or its registrable domain is on the disposable list."""
    name = _check_str(domain, "domain").strip().lower().removesuffix(".")
    listed = disposable_domains()
    if name in listed:
        return True
    try:
        return registrable_domain(name) in listed
    except IdentityError:
        return False


def mx_reason(records: Iterable[_MxLike]) -> InvalidReason | None:
    """Why the MX records show a domain can't receive mail, or ``None`` when it can."""
    found = tuple(records)
    if not found:
        return InvalidReason.NO_MX
    if all(record.exchange == "" for record in found):
        return InvalidReason.NULL_MX
    return None
