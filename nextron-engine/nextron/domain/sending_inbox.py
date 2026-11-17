"""The SendingInbox machine and the rules for registering a Sending Inbox (AD-4, AD-14, AD-21,
FR-32). Standard library only, plus ``nextron.domain``.

**Statuses** (``InboxStatus``): ``connecting``, ``active``, ``paused``, ``auth_failed`` and
``removed``. ``TERMINAL_STATUSES`` is ``removed``; ``BINDABLE_STATUSES`` (the inboxes a new
Sequence may bind to, AD-21) are ``connecting`` and ``active``. ``sending`` owns the
``sending_inboxes.status`` column and appends one ``events`` row (entity ``sending_inboxes``)
per change.

**Pause causes** (``PauseCause``): ``operator``, ``bounce_rate``, ``policy_bounce``,
``blocklist`` and ``spam_rate``. Each has its own event, ``pause_<cause>`` (``PAUSE_EVENTS``),
whose cause is the pause cause itself.

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

=================  ==================================  ===========  =========================
Event              From                                To           Cause
=================  ==================================  ===========  =========================
connect            connecting                          active       connected
pause_<cause>      active                              paused       <cause> (the pause cause)
resume             paused                              active       resumed
auth_fail          active, paused                      auth_failed  invalid_grant
reconsent          auth_failed                         active       reconsented
remove             every status except removed         removed      operator_removed
=================  ==================================  ===========  =========================

**Creation.** ``CREATION`` is ``Transition(connecting, registered)``: a registered inbox starts
at ``connecting`` (Story 4.7 applies only this). Later stories add the commands for the other
moves; they add no status.

**Registration rules.**

- ``check_address(text) -> InboxAddress(address, domain)``:

  1. ``address`` is ``identity.normalize_email(text)``, and it must be all ASCII (a Gmail
     mailbox's local part is ASCII; an IDN domain arrives in its punycode form), with a local
     part that is an RFC 5322 dot-atom: runs of atext (``A-Za-z0-9!#$%&'*+/=?^_`{|}~-``)
     separated by single dots, with no leading or trailing dot (so no quoted local part,
     comment, comma or ``..``);
  2. ``domain`` is ``identity.registrable_domain`` of the part after ``@``;
  3. a ``domain`` equal to ``BRAND_DOMAIN`` (``nextronai.com``) is refused with
     ``brand_domain``, so its subdomains are refused too (FR-32: cold email never goes from
     nextronai.com);
  4. a local part whose text before the first ``+`` is in ``ROLE_LOCAL_PARTS`` is refused with
     ``role_address``, so ``info+cold@`` is a role address too.

  The brand check comes before the role check, so ``team@nextronai.com`` is ``brand_domain``.
  An ``IdentityError``, a non-ASCII address or a local part that isn't a dot-atom is
  ``invalid_address``.
- ``check_label(text) -> str``: the label stripped; 1 to ``MAX_LABEL_LENGTH`` (80) characters,
  with no ``Cc``, ``Cf``, ``Cs``, ``Zl`` or ``Zp`` character; otherwise ``invalid_label``.
- ``allowlist_refusal(address, env, allowlist) -> Refusal | None``: ``not_allowlisted`` when the
  sink applies to ``env`` (``nextron.domain.sink.sink_applies``: every environment except
  ``prod``) and ``address`` isn't ``allowlisted`` (AD-14); otherwise ``None``.
- ``crowded_domains(domains)``: each domain occurring more than ``MAX_INBOXES_PER_DOMAIN`` (3)
  times, sorted, once each; ``crowding_warning(domain)`` is the panel's warning for one.

Every refusal raises ``InboxRefused`` (code ``inbox_refused``) with ``.refusal`` (a
``Refusal``); its message is exactly ``REFUSAL_MESSAGES[refusal]``, and no message ever holds
the address or the label. A non-``str`` argument raises ``TypeError``.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain import identity
from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.sink import allowlisted, sink_applies

MACHINE: Final = "sending_inbox"
BRAND_DOMAIN: Final = "nextronai.com"
MAX_LABEL_LENGTH: Final = 80
MAX_INBOXES_PER_DOMAIN: Final = 3
PLUS_TAG: Final = "+"
# An RFC 5322 dot-atom local part: atext runs separated by single dots (matched with fullmatch)
_ATEXT_RUN: Final = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~\-]+"
_DOT_ATOM: Final = re.compile(rf"{_ATEXT_RUN}(?:\.{_ATEXT_RUN})*")
# control, format, surrogate, line separator and paragraph separator characters
REFUSED_LABEL_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

# Role (shared) local parts: an inbox must be a person's own (FR-32). The first eight are Story
# 4.7's; the rest are other common role and system mailboxes.
ROLE_LOCAL_PARTS: Final = frozenset(
    {
        "team",
        "info",
        "sales",
        "contact",
        "admin",
        "office",
        "hello",
        "support",
        "abuse",
        "billing",
        "careers",
        "enquiries",
        "help",
        "hostmaster",
        "inquiries",
        "jobs",
        "marketing",
        "no-reply",
        "noreply",
        "postmaster",
        "press",
        "webmaster",
    }
)

CONNECTING_NOTE: Final = "Gmail connection and warm-up not set up yet; nothing can be sent from it"


class InboxStatus(StrEnum):
    CONNECTING = "connecting"
    ACTIVE = "active"
    PAUSED = "paused"
    AUTH_FAILED = "auth_failed"
    REMOVED = "removed"


class PauseCause(StrEnum):
    OPERATOR = "operator"
    BOUNCE_RATE = "bounce_rate"
    POLICY_BOUNCE = "policy_bounce"
    BLOCKLIST = "blocklist"
    SPAM_RATE = "spam_rate"


class InboxEvent(StrEnum):
    CONNECT = "connect"
    PAUSE_OPERATOR = "pause_operator"
    PAUSE_BOUNCE_RATE = "pause_bounce_rate"
    PAUSE_POLICY_BOUNCE = "pause_policy_bounce"
    PAUSE_BLOCKLIST = "pause_blocklist"
    PAUSE_SPAM_RATE = "pause_spam_rate"
    RESUME = "resume"
    AUTH_FAIL = "auth_fail"
    RECONSENT = "reconsent"
    REMOVE = "remove"


class InboxCause(StrEnum):
    REGISTERED = "registered"
    CONNECTED = "connected"
    OPERATOR = "operator"
    BOUNCE_RATE = "bounce_rate"
    POLICY_BOUNCE = "policy_bounce"
    BLOCKLIST = "blocklist"
    SPAM_RATE = "spam_rate"
    RESUMED = "resumed"
    INVALID_GRANT = "invalid_grant"
    RECONSENTED = "reconsented"
    OPERATOR_REMOVED = "operator_removed"


CREATION: Final = Transition(InboxStatus.CONNECTING, InboxCause.REGISTERED)
TERMINAL_STATUSES: Final = (InboxStatus.REMOVED,)
BINDABLE_STATUSES: Final = (InboxStatus.CONNECTING, InboxStatus.ACTIVE)

PAUSE_EVENTS: Final[Mapping[PauseCause, InboxEvent]] = MappingProxyType(
    {cause: InboxEvent(f"pause_{cause}") for cause in PauseCause}
)


def _rows(
    sources: Iterable[InboxStatus], event: InboxEvent, to: InboxStatus, cause: InboxCause
) -> dict[tuple[InboxStatus, InboxEvent], Transition[InboxStatus, InboxCause]]:
    return {(source, event): Transition(to, cause) for source in sources}


_S = InboxStatus
_E = InboxEvent
_C = InboxCause

TRANSITIONS: Final[
    Mapping[tuple[InboxStatus, InboxEvent], Transition[InboxStatus, InboxCause]]
] = MappingProxyType(
    {
        **_rows((_S.CONNECTING,), _E.CONNECT, _S.ACTIVE, _C.CONNECTED),
        **{
            key: row
            for cause, event in PAUSE_EVENTS.items()
            for key, row in _rows((_S.ACTIVE,), event, _S.PAUSED, InboxCause(cause)).items()
        },
        **_rows((_S.PAUSED,), _E.RESUME, _S.ACTIVE, _C.RESUMED),
        **_rows((_S.ACTIVE, _S.PAUSED), _E.AUTH_FAIL, _S.AUTH_FAILED, _C.INVALID_GRANT),
        **_rows((_S.AUTH_FAILED,), _E.RECONSENT, _S.ACTIVE, _C.RECONSENTED),
        **_rows(
            (status for status in InboxStatus if status not in TERMINAL_STATUSES),
            _E.REMOVE,
            _S.REMOVED,
            _C.OPERATOR_REMOVED,
        ),
    }
)


def transition(current: InboxStatus, event: InboxEvent) -> Transition[InboxStatus, InboxCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


# Registration ---------------------------------------------------------------------------------


class Refusal(StrEnum):
    """Why an inbox can't be registered."""

    INVALID_ADDRESS = "invalid_address"
    INVALID_LABEL = "invalid_label"
    BRAND_DOMAIN = "brand_domain"
    ROLE_ADDRESS = "role_address"
    NOT_ALLOWLISTED = "not_allowlisted"
    ALREADY_REGISTERED = "already_registered"
    SENDER_NAME_UNSET = "sender_name_unset"


REFUSAL_MESSAGES: Final[Mapping[Refusal, str]] = MappingProxyType(
    {
        Refusal.INVALID_ADDRESS: "Enter a valid email address, such as furqaan@yourdomain.com",
        Refusal.INVALID_LABEL: (
            f"Enter a label of 1 to {MAX_LABEL_LENGTH} characters on one line"
        ),
        Refusal.BRAND_DOMAIN: "Cold email never goes from nextronai.com",
        Refusal.ROLE_ADDRESS: "Use a personal address in the Sender's name",
        Refusal.NOT_ALLOWLISTED: "Outside prod, only allowlisted test inboxes can be registered",
        Refusal.ALREADY_REGISTERED: "Inbox already registered",
        Refusal.SENDER_NAME_UNSET: "Set the Sender's name on the Settings page first",
    }
)


class InboxRefused(DomainError):
    """An inbox that can't be registered; the message is ``REFUSAL_MESSAGES[refusal]`` and
    never holds the address or the label."""

    code = "inbox_refused"

    def __init__(self, refusal: Refusal) -> None:
        refusal = Refusal(refusal)
        super().__init__(REFUSAL_MESSAGES[refusal])
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class InboxAddress:
    """A checked inbox address and the registrable domain it sends from."""

    address: str
    domain: str


def _require_str(value: object, function: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{function} takes a str, not {type(value).__name__}")
    return value


def check_address(text: str) -> InboxAddress:
    """The normalised address and its Sending Domain; ``InboxRefused`` for an address that
    can't be an inbox. See the module docstring."""
    _require_str(text, "check_address")
    try:
        address = identity.normalize_email(text)
        if not address.isascii():
            raise InboxRefused(Refusal.INVALID_ADDRESS)
        local, _, host = address.rpartition("@")
        if _DOT_ATOM.fullmatch(local) is None:
            raise InboxRefused(Refusal.INVALID_ADDRESS)
        domain = identity.registrable_domain(host)
    except identity.IdentityError:
        raise InboxRefused(Refusal.INVALID_ADDRESS) from None
    if domain == BRAND_DOMAIN:
        raise InboxRefused(Refusal.BRAND_DOMAIN)
    if local.partition(PLUS_TAG)[0] in ROLE_LOCAL_PARTS:
        raise InboxRefused(Refusal.ROLE_ADDRESS)
    return InboxAddress(address, domain)


def check_label(text: str) -> str:
    """The label stripped; ``InboxRefused`` unless it is 1 to 80 characters on one line."""
    label = _require_str(text, "check_label").strip()
    if not 1 <= len(label) <= MAX_LABEL_LENGTH or any(
        unicodedata.category(character) in REFUSED_LABEL_CATEGORIES for character in label
    ):
        raise InboxRefused(Refusal.INVALID_LABEL)
    return label


def allowlist_refusal(address: str, env: str, allowlist: Iterable[object]) -> Refusal | None:
    """``not_allowlisted`` when ``env`` isn't ``prod`` and ``address`` isn't on ``allowlist``
    (AD-14); otherwise ``None``."""
    _require_str(address, "allowlist_refusal")
    if sink_applies(env) and not allowlisted(address, allowlist):
        return Refusal.NOT_ALLOWLISTED
    return None


def crowded_domains(domains: Iterable[str]) -> tuple[str, ...]:
    """Each domain occurring more than ``MAX_INBOXES_PER_DOMAIN`` times, sorted."""
    counts = Counter(_require_str(domain, "crowded_domains") for domain in domains)
    crowded = (domain for domain, count in counts.items() if count > MAX_INBOXES_PER_DOMAIN)
    return tuple(sorted(crowded))


def crowding_warning(domain: str) -> str:
    """The panel's warning for a crowded Sending Domain."""
    _require_str(domain, "crowding_warning")
    return (
        f"More than {MAX_INBOXES_PER_DOMAIN} inboxes on {domain} raises deliverability risk"
    )
