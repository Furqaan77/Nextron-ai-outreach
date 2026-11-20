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

**Connecting (Story 6.2, FR-32, AD-12, AD-14).** An inbox connects through Gmail OAuth with
``GMAIL_SCOPE`` (``gmail.modify``) only; its refresh token lives in keyring at
``gmail_secret_name(address)`` (``gmail/<address>``).

- ``ConnectFailure`` names every way a connect stores nothing; ``ConnectRefused`` (code
  ``connect_refused``, ``.failure``) carries one, its message exactly
  ``CONNECT_MESSAGES[failure]`` (never an address, a label or a token). The generic consent
  failures start with ``CONSENT_FAILED`` ("Consent failed"); ``gate_off`` is the OQ-A2 gate's
  text. ``connect_failure(refusal)`` maps a registration ``Refusal`` onto its connect failure
  (``already_registered`` is ``already_connected``).
- ``identity_problem(requested, profile, env, allowlist)``, after the token exchange, in
  order: ``different_account`` unless ``profile`` is ``requested`` after ASCII lowercasing
  (``google_oauth.same_account``); ``check_address(profile)``'s ``invalid_address``,
  ``brand_domain`` (nextronai.com or a subdomain) or ``role_address`` (``+tag`` ignored); then
  ``not_allowlisted`` outside ``prod`` (``allowlist_refusal``); else ``None``.

**Warm-up (Story 6.4, FR-33, FR-34).** ``check_warmup(started_on, method, warm_level,
tool_daily, ramp_step, ramp_target, *, today) -> WarmupRecord`` reads the warm-up form's six
texts (each stripped) into typed values, with the ranges of ``nextron.domain.sending_policy``:

- ``started_on``: an ISO date ``YYYY-MM-DD`` (``invalid_start``) not after ``today``, the date
  in ``sender_timezone`` (``future_start``);
- ``method``: ``manual`` in any case (stored ``manual``), or a warm-up tool's name of 1 to
  ``MAX_METHOD_LENGTH`` (40) characters on one line, by ``check_label``'s character rule
  (``invalid_method``);
- ``warm_level`` (``invalid_warm_level``), ``tool_daily`` (``invalid_tool_daily``),
  ``ramp_step`` (``invalid_ramp_step``) and ``ramp_target`` (``invalid_ramp_target``): ASCII
  digits only, within their range.

The start date, method and warm level are the warm-up record: all three blank clears it
(``WarmupRecord`` with ``None`` for each: warm-up unrecorded, so the cap is 0); one blank while
another is filled is that field's refusal. The fields are checked in form order and the first
refusal wins. Every refusal raises ``WarmupRefused`` (code ``warmup_refused``) with
``.refusal`` (a ``WarmupRefusal``); its message is exactly ``WARMUP_MESSAGES[refusal]``, which
names the field, never the value. ``timezone_unset``, ``unknown_inbox`` and ``inbox_removed``
are ``Sending.record_warmup``'s.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain import identity
from nextron.domain.errors import DomainError
from nextron.domain.google_oauth import same_account
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.sending_policy import (
    RAMP_STEP_RANGE,
    RAMP_TARGET_RANGE,
    TOOL_DAILY_RANGE,
    WARM_LEVEL_RANGE,
)
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


def _one_line(text: str, max_length: int) -> bool:
    """Whether ``text`` is 1 to ``max_length`` characters on one line."""
    return 1 <= len(text) <= max_length and not any(
        unicodedata.category(character) in REFUSED_LABEL_CATEGORIES for character in text
    )


def check_label(text: str) -> str:
    """The label stripped; ``InboxRefused`` unless it is 1 to 80 characters on one line."""
    label = _require_str(text, "check_label").strip()
    if not _one_line(label, MAX_LABEL_LENGTH):
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


# Connecting through Gmail OAuth (Story 6.2) ------------------------------------------------------

GMAIL_SCOPE: Final = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SECRET_PREFIX: Final = "gmail/"


def gmail_secret_name(address: str) -> str:
    """The keyring entry holding the refresh token of the inbox ``address``: ``gmail/<address>``
    (AD-12)."""
    return GMAIL_SECRET_PREFIX + _require_str(address, "gmail_secret_name")


class ConnectFailure(StrEnum):
    """Why connecting a Sending Inbox through Gmail OAuth stored nothing."""

    GATE_OFF = "gate_off"
    INVALID_ADDRESS = "invalid_address"
    INVALID_LABEL = "invalid_label"
    BRAND_DOMAIN = "brand_domain"
    ROLE_ADDRESS = "role_address"
    NOT_ALLOWLISTED = "not_allowlisted"
    SENDER_NAME_UNSET = "sender_name_unset"
    CLIENT_NOT_IMPORTED = "client_not_imported"
    UNKNOWN_INBOX = "unknown_inbox"
    INBOX_REMOVED = "inbox_removed"
    ALREADY_CONNECTED = "already_connected"
    TIMED_OUT = "timed_out"
    STATE_MISMATCH = "state_mismatch"
    DECLINED = "declined"
    EXCHANGE_FAILED = "exchange_failed"
    SCOPE_NOT_GRANTED = "scope_not_granted"
    NO_REFRESH_TOKEN = "no_refresh_token"
    TOKEN_TOO_LONG = "token_too_long"
    PROFILE_FAILED = "profile_failed"
    DIFFERENT_ACCOUNT = "different_account"
    STORE_FAILED = "store_failed"
    BROWSER_FAILED = "browser_failed"
    LISTENER_FAILED = "listener_failed"
    CRASHED = "crashed"


CONSENT_FAILED: Final = "Consent failed"
GATE_OFF_MESSAGE: Final = (
    "Connecting inboxes waits for the Gmail spike (Story 6.1) to confirm the Internal OAuth app"
)

CONNECT_MESSAGES: Final[Mapping[ConnectFailure, str]] = MappingProxyType(
    {
        ConnectFailure.GATE_OFF: GATE_OFF_MESSAGE,
        ConnectFailure.INVALID_ADDRESS: REFUSAL_MESSAGES[Refusal.INVALID_ADDRESS],
        ConnectFailure.INVALID_LABEL: REFUSAL_MESSAGES[Refusal.INVALID_LABEL],
        ConnectFailure.BRAND_DOMAIN: "Sending Inboxes can't be on nextronai.com",
        ConnectFailure.ROLE_ADDRESS: "Role addresses can't send",
        ConnectFailure.NOT_ALLOWLISTED: (
            "Outside prod, only allowlisted test inboxes can be connected"
        ),
        ConnectFailure.SENDER_NAME_UNSET: REFUSAL_MESSAGES[Refusal.SENDER_NAME_UNSET],
        ConnectFailure.CLIENT_NOT_IMPORTED: (
            "Import the Google Desktop client on the Credentials page first"
        ),
        ConnectFailure.UNKNOWN_INBOX: "That inbox isn't registered",
        ConnectFailure.INBOX_REMOVED: "That inbox has been removed",
        ConnectFailure.ALREADY_CONNECTED: "That inbox is already connected",
        ConnectFailure.TIMED_OUT: f"{CONSENT_FAILED}: no answer from Google within 5 minutes",
        ConnectFailure.STATE_MISMATCH: (
            f"{CONSENT_FAILED}: the answer didn't match this request"
        ),
        ConnectFailure.DECLINED: f"{CONSENT_FAILED}: access was declined",
        ConnectFailure.EXCHANGE_FAILED: f"{CONSENT_FAILED}: Google refused the sign-in",
        ConnectFailure.SCOPE_NOT_GRANTED: (
            f"{CONSENT_FAILED}: Gmail access (gmail.modify) wasn't granted"
        ),
        ConnectFailure.NO_REFRESH_TOKEN: f"{CONSENT_FAILED}: Google returned no refresh token",
        ConnectFailure.TOKEN_TOO_LONG: (
            f"{CONSENT_FAILED}: the refresh token is too long for Credential Manager"
        ),
        ConnectFailure.PROFILE_FAILED: (
            f"{CONSENT_FAILED}: the Gmail profile couldn't be read"
        ),
        ConnectFailure.DIFFERENT_ACCOUNT: "Signed in as a different account",
        ConnectFailure.STORE_FAILED: f"{CONSENT_FAILED}: the connection couldn't be saved",
        ConnectFailure.BROWSER_FAILED: f"{CONSENT_FAILED}: the browser couldn't be opened",
        ConnectFailure.LISTENER_FAILED: (
            f"{CONSENT_FAILED}: the local sign-in listener couldn't start"
        ),
        ConnectFailure.CRASHED: f"{CONSENT_FAILED}: an unexpected error",
    }
)

# A registration refusal and the connect failure it is when connecting.
_REFUSAL_FAILURES: Final[Mapping[Refusal, ConnectFailure]] = MappingProxyType(
    {
        Refusal.INVALID_ADDRESS: ConnectFailure.INVALID_ADDRESS,
        Refusal.INVALID_LABEL: ConnectFailure.INVALID_LABEL,
        Refusal.BRAND_DOMAIN: ConnectFailure.BRAND_DOMAIN,
        Refusal.ROLE_ADDRESS: ConnectFailure.ROLE_ADDRESS,
        Refusal.NOT_ALLOWLISTED: ConnectFailure.NOT_ALLOWLISTED,
        Refusal.SENDER_NAME_UNSET: ConnectFailure.SENDER_NAME_UNSET,
        Refusal.ALREADY_REGISTERED: ConnectFailure.ALREADY_CONNECTED,
    }
)


def connect_failure(refusal: Refusal) -> ConnectFailure:
    """The connect failure a registration ``refusal`` is."""
    return _REFUSAL_FAILURES[Refusal(refusal)]


class ConnectRefused(DomainError):
    """A connect that can't start or finish; the message is ``CONNECT_MESSAGES[failure]`` and
    never holds the address, the label or a token."""

    code = "connect_refused"

    def __init__(self, failure: ConnectFailure) -> None:
        failure = ConnectFailure(failure)
        super().__init__(CONNECT_MESSAGES[failure])
        self.failure = failure


INBOX_AUTH_FAILED: Final = "inbox_auth_failed"  # the ops alert's kind and the health issue's
INBOX_PAUSED: Final = "inbox_paused"  # the health issue's kind
RECONNECT_HINT: Final = "Reconnect it on the Sending Inboxes page; it sends nothing until then."
RESUME_HINT: Final = (
    "It sends nothing while paused. Fix the cause; a Resume button comes with Story 6.3."
)


def auth_failed_text(inbox_id: str) -> str:
    """The ``inbox_auth_failed`` ops alert: the inbox's id only (AD-15)."""
    return (
        f"Sending Inbox {_require_str(inbox_id, 'auth_failed_text')} lost its Gmail grant "
        "(invalid_grant); reconnect it on the Sending Inboxes page"
    )


def identity_problem(
    requested: str, profile: object, env: str, allowlist: Iterable[object]
) -> ConnectFailure | None:
    """Why the Gmail profile address ``profile`` can't connect as the requested inbox
    ``requested`` in ``env``, or ``None``. In order: ``different_account`` unless the two are the
    same address after ASCII lowercasing (``google_oauth.same_account``); then
    ``check_address(profile)``'s refusal as ``invalid_address``, ``brand_domain`` (nextronai.com
    or a subdomain) or ``role_address`` (``+tag`` ignored); then ``not_allowlisted`` outside
    ``prod`` when the address isn't on ``allowlist`` (AD-14)."""
    _require_str(requested, "identity_problem")
    if not isinstance(profile, str) or not same_account(requested, profile):
        return ConnectFailure.DIFFERENT_ACCOUNT
    try:
        checked = check_address(profile)
    except InboxRefused as refused:
        return connect_failure(refused.refusal)
    refusal = allowlist_refusal(checked.address, env, allowlist)
    return None if refusal is None else connect_failure(refusal)


# Warm-up (Story 6.4) ----------------------------------------------------------------------------

MAX_METHOD_LENGTH: Final = 40
MANUAL_METHOD: Final = "manual"
_ISO_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # matched with fullmatch
_DIGITS: Final = re.compile(r"[0-9]{1,6}")  # matched with fullmatch


class WarmupRefusal(StrEnum):
    """Why an inbox's warm-up can't be recorded."""

    TIMEZONE_UNSET = "timezone_unset"
    UNKNOWN_INBOX = "unknown_inbox"
    INBOX_REMOVED = "inbox_removed"
    INVALID_START = "invalid_start"
    FUTURE_START = "future_start"
    INVALID_METHOD = "invalid_method"
    INVALID_WARM_LEVEL = "invalid_warm_level"
    INVALID_TOOL_DAILY = "invalid_tool_daily"
    INVALID_RAMP_STEP = "invalid_ramp_step"
    INVALID_RAMP_TARGET = "invalid_ramp_target"


def _range_text(bounds: tuple[int, int]) -> str:
    return f"{bounds[0]} to {bounds[1]}"


WARMUP_MESSAGES: Final[Mapping[WarmupRefusal, str]] = MappingProxyType(
    {
        WarmupRefusal.TIMEZONE_UNSET: "Set the Sender's timezone on the Settings page first",
        WarmupRefusal.UNKNOWN_INBOX: "That inbox isn't registered",
        WarmupRefusal.INBOX_REMOVED: "That inbox has been removed",
        WarmupRefusal.INVALID_START: "Enter the warm-up start date as YYYY-MM-DD",
        WarmupRefusal.FUTURE_START: "The warm-up start date can't be after today",
        WarmupRefusal.INVALID_METHOD: (
            f"Enter the warm-up method: manual, or a tool's name of 1 to {MAX_METHOD_LENGTH} "
            "characters on one line"
        ),
        WarmupRefusal.INVALID_WARM_LEVEL: (
            f"Enter the warm level as a whole number from {_range_text(WARM_LEVEL_RANGE)}"
        ),
        WarmupRefusal.INVALID_TOOL_DAILY: (
            "Enter the warm-up tool's sends a day as a whole number from "
            f"{_range_text(TOOL_DAILY_RANGE)}"
        ),
        WarmupRefusal.INVALID_RAMP_STEP: (
            f"Enter the ramp step as a whole number from {_range_text(RAMP_STEP_RANGE)}"
        ),
        WarmupRefusal.INVALID_RAMP_TARGET: (
            f"Enter the ramp target as a whole number from {_range_text(RAMP_TARGET_RANGE)}"
        ),
    }
)


class WarmupRefused(DomainError):
    """A warm-up that can't be recorded; the message is ``WARMUP_MESSAGES[refusal]``, which
    names the field and never holds the value."""

    code = "warmup_refused"

    def __init__(self, refusal: WarmupRefusal) -> None:
        refusal = WarmupRefusal(refusal)
        super().__init__(WARMUP_MESSAGES[refusal])
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class WarmupRecord:
    """Checked warm-up form values; the first three are all ``None`` (unrecorded) or all
    set."""

    warmup_started_on: date | None
    warmup_method: str | None
    warm_level: int | None
    warmup_tool_daily: int
    ramp_step: int
    ramp_target: int


def _integer(text: str, bounds: tuple[int, int], refusal: WarmupRefusal) -> int:
    if _DIGITS.fullmatch(text) is None:
        raise WarmupRefused(refusal)
    value = int(text)
    if not bounds[0] <= value <= bounds[1]:
        raise WarmupRefused(refusal)
    return value


def _start(text: str, today: date) -> date:
    if _ISO_DATE.fullmatch(text) is None:
        raise WarmupRefused(WarmupRefusal.INVALID_START)
    try:
        found = date.fromisoformat(text)
    except ValueError:
        raise WarmupRefused(WarmupRefusal.INVALID_START) from None
    if found > today:
        raise WarmupRefused(WarmupRefusal.FUTURE_START)
    return found


def _method(text: str) -> str:
    if text.casefold() == MANUAL_METHOD:
        return MANUAL_METHOD
    if not _one_line(text, MAX_METHOD_LENGTH):
        raise WarmupRefused(WarmupRefusal.INVALID_METHOD)
    return text


def check_warmup(
    started_on: str,
    method: str,
    warm_level: str,
    tool_daily: str,
    ramp_step: str,
    ramp_target: str,
    *,
    today: date,
) -> WarmupRecord:
    """The warm-up form's texts as typed values; ``WarmupRefused`` for the first field that
    isn't valid. See the module docstring."""
    texts = [
        _require_str(value, "check_warmup").strip()
        for value in (started_on, method, warm_level, tool_daily, ramp_step, ramp_target)
    ]
    if not isinstance(today, date):
        raise TypeError("check_warmup takes today as a date")
    start_text, method_text, level_text, tool_text, step_text, target_text = texts
    started: date | None = None
    shown_method: str | None = None
    level: int | None = None
    if start_text or method_text or level_text:
        started = _start(start_text, today)
        shown_method = _method(method_text)
        level = _integer(level_text, WARM_LEVEL_RANGE, WarmupRefusal.INVALID_WARM_LEVEL)
    return WarmupRecord(
        warmup_started_on=started,
        warmup_method=shown_method,
        warm_level=level,
        warmup_tool_daily=_integer(tool_text, TOOL_DAILY_RANGE, WarmupRefusal.INVALID_TOOL_DAILY),
        ramp_step=_integer(step_text, RAMP_STEP_RANGE, WarmupRefusal.INVALID_RAMP_STEP),
        ramp_target=_integer(target_text, RAMP_TARGET_RANGE, WarmupRefusal.INVALID_RAMP_TARGET),
    )
