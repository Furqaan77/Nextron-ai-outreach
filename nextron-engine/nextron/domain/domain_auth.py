"""Sending Domain authentication (FR-64, NFR-4, NFR-20): SPF, DKIM, DMARC and relaxed
alignment judged from TXT answers, the cold-send verdict and the daily check's schedule.
Standard library, ``nextron.domain.identity`` and ``nextron.domain.backup.latest_slot`` only;
a function that needs the time takes ``now``.

**Items and reasons.** ``AuthItem`` is ``spf``, ``dkim``, ``dmarc`` and ``alignment``, in that
order; ``ITEM_LABELS`` are "SPF", "DKIM", "DMARC" and "Alignment". ``AuthReason`` is ``ok`` or
why an item failed; ``ITEM_REASONS`` holds the reasons each item can have, and ``REASON_TEXTS``
a plain-English text for every reason.

**The names.** ``SPF_INCLUDE`` is ``_spf.google.com`` and ``DKIM_SELECTOR`` is ``google``:
``dkim_name(d)`` is ``google._domainkey.<d>`` and ``dmarc_name(d)`` is ``_dmarc.<d>``. SPF is
read at the domain itself.

**``judge(domain, spf_txt, dkim_txt, dmarc_txt, from_domains) -> AuthJudgement``**, from the TXT
strings of the three names:

- **SPF.** An SPF record is one whose first whitespace-separated term is ``v=spf1`` (any case).
  None is ``spf_missing``, more than one ``spf_multiple``. The one record is ``ok`` only when a
  term, in any case, is ``include:_spf.google.com`` or ``+include:_spf.google.com``; else
  ``spf_no_google``. Nested includes and ``redirect=`` are never followed.
- **DKIM.** No record is ``dkim_missing``. It is ``ok`` when some record's ``;``-separated tag
  list has a ``p`` tag (DKIM tags are case-sensitive) whose value, with whitespace removed, isn't
  empty; else ``dkim_empty_key``.
- **DMARC.** A DMARC record is one whose first tag is ``v`` with the exact value ``DMARC1``,
  whitespace allowed around ``=`` and ``;``. None is ``dmarc_missing`` (``v=DMARC2`` is no DMARC
  record), two or more ``dmarc_multiple``, exactly one ``ok``. ``aspf`` and ``adkim`` aren't
  read: alignment is always relaxed.
- **Alignment (relaxed).** The authenticated domain is ``domain``, where SPF and DKIM were
  judged. ``from_domains`` are the domain parts of its inboxes' addresses (``domain`` itself when
  none is given). When neither SPF nor DKIM is ``ok`` it is ``unauthenticated``; otherwise it is
  ``ok`` only when every From domain's ``identity.registrable_domain`` equals ``domain`` (one
  that has none fails), else ``from_domain_mismatch``.

``AuthJudgement`` holds one reason per item (``reasons``, in ``AuthItem`` order), ``passed``
(every item ``ok``) and ``failing`` (the failed items, in ``AuthItem`` order).

**The verdict.** ``CHECK_WINDOW`` is 26 hours. ``auth_verdict(latest, now) ->
AuthVerdict(blocked, failing, text)``: ``latest`` is ``None`` or the newest check (anything with
``checked_at``, ``passed`` and ``failing``). Cold sends are allowed only when ``latest`` passed
and ``0 <= now - checked_at < CHECK_WINDOW``; a check stamped in the future (the clock was set
back) isn't fresh. The texts:

- ``Cold sends allowed``;
- ``Cold sends blocked: not checked yet`` (``latest`` is ``None``);
- ``Cold sends blocked: <labels joined by ", "> failing`` (the latest check failed; ``failing``
  holds its items);
- ``Cold sends blocked: no check passed in the last 26 hours`` (stale or future-dated).

**Storage.** ``encode_details(reasons)`` is the compact JSON, keys sorted, of one key per item
whose value is that item's reason (one of its ``ITEM_REASONS``); ``decode_details(text)`` reads
it back, in ``AuthItem`` order. Anything else raises ``ValueError``.

**The daily check.** ``CHECK_DOMAINS_JOB`` (``check_domains``) is the job type and the AD-10
trigger; a per-domain job's payload is ``{"v": 1, "sending_domain_id": <id>}``
(``SENDING_DOMAIN_ID_FIELD``). ``DOMAIN_CHECK_TIME`` is 07:00 (in ``sender_timezone``).
``check_missed(newest_times, now, at, timezone)`` is whether any domain's newest check time is
``None``, earlier than ``backup.latest_slot(now, at, timezone)``, or later than ``now`` (stamped
in the future: the clock was set back, so it proves nothing); with no domain it is ``False``.
``DomainCheckSchedule(at, timezone, missed)`` is what ``sending`` reads for the trigger.
``DOMAIN_AUTH_FAILING`` is the health-issue kind of a blocked domain, and ``RECHECK_HINT`` its
detail.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from nextron.domain.backup import latest_slot
from nextron.domain.identity import IdentityError, registrable_domain


class AuthItem(StrEnum):
    """What a domain check judges, in the order the panel shows it."""

    SPF = "spf"
    DKIM = "dkim"
    DMARC = "dmarc"
    ALIGNMENT = "alignment"


class AuthReason(StrEnum):
    """An item's result: ``ok`` or why it failed."""

    OK = "ok"
    SPF_MISSING = "spf_missing"
    SPF_MULTIPLE = "spf_multiple"
    SPF_NO_GOOGLE = "spf_no_google"
    DKIM_MISSING = "dkim_missing"
    DKIM_EMPTY_KEY = "dkim_empty_key"
    DMARC_MISSING = "dmarc_missing"
    DMARC_MULTIPLE = "dmarc_multiple"
    UNAUTHENTICATED = "unauthenticated"
    FROM_DOMAIN_MISMATCH = "from_domain_mismatch"


SPF_INCLUDE: Final = "_spf.google.com"
DKIM_SELECTOR: Final = "google"
CHECK_WINDOW: Final = timedelta(hours=26)
DOMAIN_CHECK_TIME: Final = time(7, 0)
CHECK_DOMAINS_JOB: Final = "check_domains"
SENDING_DOMAIN_ID_FIELD: Final = "sending_domain_id"
PAYLOAD_VERSION: Final = 1
DOMAIN_AUTH_FAILING: Final = "domain_auth_failing"  # the health-issue kind
RECHECK_HINT: Final = (
    "Fix the domain's DNS records with its DNS host, then press Recheck on the Sending "
    "Inboxes page."
)

SPF_VERSION: Final = "v=spf1"
SPF_GOOGLE_TERMS: Final = frozenset({f"include:{SPF_INCLUDE}", f"+include:{SPF_INCLUDE}"})
DKIM_KEY_TAG: Final = "p"
DMARC_VERSION_TAG: Final = "v"
DMARC_VERSION: Final = "DMARC1"

ITEM_LABELS: Final[Mapping[AuthItem, str]] = MappingProxyType(
    {
        AuthItem.SPF: "SPF",
        AuthItem.DKIM: "DKIM",
        AuthItem.DMARC: "DMARC",
        AuthItem.ALIGNMENT: "Alignment",
    }
)
ITEM_REASONS: Final[Mapping[AuthItem, frozenset[AuthReason]]] = MappingProxyType(
    {
        AuthItem.SPF: frozenset(
            {
                AuthReason.OK,
                AuthReason.SPF_MISSING,
                AuthReason.SPF_MULTIPLE,
                AuthReason.SPF_NO_GOOGLE,
            }
        ),
        AuthItem.DKIM: frozenset(
            {AuthReason.OK, AuthReason.DKIM_MISSING, AuthReason.DKIM_EMPTY_KEY}
        ),
        AuthItem.DMARC: frozenset(
            {AuthReason.OK, AuthReason.DMARC_MISSING, AuthReason.DMARC_MULTIPLE}
        ),
        AuthItem.ALIGNMENT: frozenset(
            {AuthReason.OK, AuthReason.UNAUTHENTICATED, AuthReason.FROM_DOMAIN_MISMATCH}
        ),
    }
)
REASON_TEXTS: Final[Mapping[AuthReason, str]] = MappingProxyType(
    {
        AuthReason.OK: "Passes",
        AuthReason.SPF_MISSING: "No SPF record on the domain",
        AuthReason.SPF_MULTIPLE: "More than one SPF record on the domain",
        AuthReason.SPF_NO_GOOGLE: f"The SPF record doesn't include {SPF_INCLUDE}",
        AuthReason.DKIM_MISSING: f"No DKIM record at {DKIM_SELECTOR}._domainkey",
        AuthReason.DKIM_EMPTY_KEY: (
            f"The DKIM record at {DKIM_SELECTOR}._domainkey has an empty key (p=)"
        ),
        AuthReason.DMARC_MISSING: "No v=DMARC1 record at _dmarc",
        AuthReason.DMARC_MULTIPLE: "More than one v=DMARC1 record at _dmarc",
        AuthReason.UNAUTHENTICATED: "Neither SPF nor DKIM passes, so nothing can align",
        AuthReason.FROM_DOMAIN_MISMATCH: (
            "An inbox's From domain is on another organizational domain"
        ),
    }
)

ALLOWED_TEXT: Final = "Cold sends allowed"
BLOCKED_PREFIX: Final = "Cold sends blocked: "
NOT_CHECKED_TEXT: Final = f"{BLOCKED_PREFIX}not checked yet"
STALE_TEXT: Final = f"{BLOCKED_PREFIX}no check passed in the last 26 hours"


def dkim_name(domain: str) -> str:
    """The DKIM key's name of ``domain``: ``google._domainkey.<domain>``."""
    return f"{DKIM_SELECTOR}._domainkey.{domain}"


def dmarc_name(domain: str) -> str:
    """The DMARC record's name of ``domain``: ``_dmarc.<domain>``."""
    return f"_dmarc.{domain}"


def failing_text(failing: Iterable[AuthItem]) -> str:
    """``Cold sends blocked: <labels> failing`` for the failed items."""
    return f"{BLOCKED_PREFIX}{', '.join(ITEM_LABELS[item] for item in failing)} failing"


# The judgement ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthJudgement:
    """One reason per item; see the module docstring."""

    spf: AuthReason
    dkim: AuthReason
    dmarc: AuthReason
    alignment: AuthReason

    def __post_init__(self) -> None:
        for item, reason in self.reasons.items():
            if not isinstance(reason, AuthReason) or reason not in ITEM_REASONS[item]:
                raise ValueError(f"{item} has no reason {reason!r}")

    @property
    def reasons(self) -> dict[AuthItem, AuthReason]:
        """Each item's reason, in ``AuthItem`` order."""
        return {
            AuthItem.SPF: self.spf,
            AuthItem.DKIM: self.dkim,
            AuthItem.DMARC: self.dmarc,
            AuthItem.ALIGNMENT: self.alignment,
        }

    @property
    def passed(self) -> bool:
        """Whether every item is ``ok``."""
        return not self.failing

    @property
    def failing(self) -> tuple[AuthItem, ...]:
        """The failed items, in ``AuthItem`` order."""
        return tuple(item for item, reason in self.reasons.items() if reason is not AuthReason.OK)


def _spf(records: Sequence[str]) -> AuthReason:
    spf = [terms for terms in (record.split() for record in records) if _is_spf(terms)]
    if not spf:
        return AuthReason.SPF_MISSING
    if len(spf) > 1:
        return AuthReason.SPF_MULTIPLE
    [terms] = spf
    if any(term.lower() in SPF_GOOGLE_TERMS for term in terms):
        return AuthReason.OK
    return AuthReason.SPF_NO_GOOGLE


def _is_spf(terms: list[str]) -> bool:
    return bool(terms) and terms[0].lower() == SPF_VERSION


def _tags(record: str) -> list[tuple[str, str]]:
    """A ``;``-separated tag list as ``(name, value)`` pairs, each part split at its first
    ``=`` and the name stripped; a part without ``=`` is skipped."""
    pairs = []
    for part in record.split(";"):
        name, equals, value = part.partition("=")
        if equals:
            pairs.append((name.strip(), value))
    return pairs


def _dkim(records: Sequence[str]) -> AuthReason:
    if not records:
        return AuthReason.DKIM_MISSING
    for record in records:
        for name, value in _tags(record):
            if name == DKIM_KEY_TAG and "".join(value.split()):
                return AuthReason.OK
    return AuthReason.DKIM_EMPTY_KEY


def _is_dmarc(record: str) -> bool:
    name, equals, value = record.split(";", 1)[0].partition("=")
    return bool(equals) and name.strip() == DMARC_VERSION_TAG and value.strip() == DMARC_VERSION


def _dmarc(records: Sequence[str]) -> AuthReason:
    found = sum(1 for record in records if _is_dmarc(record))
    if found == 0:
        return AuthReason.DMARC_MISSING
    if found > 1:
        return AuthReason.DMARC_MULTIPLE
    return AuthReason.OK


def _organizational(domain: str) -> str | None:
    try:
        return registrable_domain(domain)
    except IdentityError:
        return None


def _alignment(
    domain: str, spf: AuthReason, dkim: AuthReason, from_domains: Sequence[str]
) -> AuthReason:
    if spf is not AuthReason.OK and dkim is not AuthReason.OK:
        return AuthReason.UNAUTHENTICATED
    if all(_organizational(found) == domain for found in from_domains or (domain,)):
        return AuthReason.OK
    return AuthReason.FROM_DOMAIN_MISMATCH


def _strings(name: str, values: Iterable[str]) -> tuple[str, ...]:
    found = tuple(values)
    if not all(isinstance(value, str) for value in found):
        raise TypeError(f"{name} must hold str values")
    return found


def judge(
    domain: str,
    spf_txt: Iterable[str],
    dkim_txt: Iterable[str],
    dmarc_txt: Iterable[str],
    from_domains: Iterable[str],
) -> AuthJudgement:
    """Judge ``domain``'s SPF, DKIM, DMARC and alignment; see the module docstring."""
    if not isinstance(domain, str):
        raise TypeError("domain must be a str")
    spf = _spf(_strings("spf_txt", spf_txt))
    dkim = _dkim(_strings("dkim_txt", dkim_txt))
    dmarc = _dmarc(_strings("dmarc_txt", dmarc_txt))
    alignment = _alignment(domain, spf, dkim, _strings("from_domains", from_domains))
    return AuthJudgement(spf, dkim, dmarc, alignment)


# The verdict ------------------------------------------------------------------------------------


class LatestCheck(Protocol):
    """What ``auth_verdict`` reads of the newest check."""

    @property
    def checked_at(self) -> datetime: ...

    @property
    def passed(self) -> bool: ...

    @property
    def failing(self) -> tuple[AuthItem, ...]: ...


@dataclass(frozen=True, slots=True)
class AuthVerdict:
    """Whether cold sends from a Sending Domain are blocked, the failed items of its latest
    check (when that check failed) and the text the panels show."""

    blocked: bool
    failing: tuple[AuthItem, ...]
    text: str


def auth_verdict(latest: LatestCheck | None, now: datetime) -> AuthVerdict:
    """The cold-send verdict of the newest check; see the module docstring."""
    if latest is None:
        return AuthVerdict(True, (), NOT_CHECKED_TEXT)
    if not latest.passed:
        failing = tuple(latest.failing)
        return AuthVerdict(True, failing, failing_text(failing))
    age = now - latest.checked_at
    if timedelta(0) <= age < CHECK_WINDOW:
        return AuthVerdict(False, (), ALLOWED_TEXT)
    return AuthVerdict(True, (), STALE_TEXT)


# Storage ----------------------------------------------------------------------------------------


def encode_details(reasons: Mapping[AuthItem, AuthReason]) -> str:
    """The stored ``details`` of ``reasons``: compact JSON, keys sorted; ``ValueError`` unless
    it holds exactly one of its ``ITEM_REASONS`` per item."""
    if not isinstance(reasons, Mapping) or set(reasons) != set(AuthItem):
        raise ValueError("details hold exactly one reason per item")
    for item, reason in reasons.items():
        if not isinstance(reason, AuthReason) or reason not in ITEM_REASONS[AuthItem(item)]:
            raise ValueError(f"{item} has no such reason")
    return json.dumps(
        {str(item): str(reason) for item, reason in reasons.items()},
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_details(text: str) -> dict[AuthItem, AuthReason]:
    """The reasons stored as ``details``, in ``AuthItem`` order; ``ValueError`` for anything
    ``encode_details`` doesn't write."""
    if not isinstance(text, str):
        raise ValueError("details must be a str")
    try:
        found = json.loads(text)
    except ValueError:
        raise ValueError("details are not JSON") from None
    if not isinstance(found, dict) or set(found) != {str(item) for item in AuthItem}:
        raise ValueError("details hold exactly one reason per item")
    reasons: dict[AuthItem, AuthReason] = {}
    for item in AuthItem:
        value = found[str(item)]
        if not isinstance(value, str) or value not in ITEM_REASONS[item]:
            raise ValueError(f"{item} has no such reason")
        reasons[item] = AuthReason(value)
    if encode_details(reasons) != text:
        raise ValueError("details are not in their stored form")
    return reasons


# The daily check --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainCheckSchedule:
    """When the daily domain checks run (``at`` in ``timezone``), and whether a listed domain
    missed the latest one, so the engine checks once at start."""

    at: time
    timezone: str
    missed: bool


def check_missed(
    newest_times: Iterable[datetime | None], now: datetime, at: time, timezone: str
) -> bool:
    """Whether any domain has no check (``None``), none since the latest daily slot, or a newest
    check stamped after ``now``."""
    slot: datetime | None = None
    for newest in newest_times:
        if newest is None or newest > now:
            return True
        if slot is None:
            slot = latest_slot(now, at, timezone)
        if newest < slot:
            return True
    return False
