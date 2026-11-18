"""Channel Permissions for the warm channels, WhatsApp and Instagram (FR-24, FR-47, FR-41,
AD-4, AD-16; Story 10.7). Standard library only, plus ``nextron.domain``. A pure helper: it
fetches, posts and sends nothing, and builds no link to either platform.

A WhatsApp or Instagram Draft (Story 10.8) may exist only once the prospect opened that channel.
``pipeline`` records each opening as one ``channel_permissions`` row: what the prospect did,
when, and the Evidence (an operator Quote, stored by ``research``). Only the Operator records
one, through the Channel Permission page of a Contact. ``NOT_QUALIFYING`` is the form's own
warning: "An email reply, or a number in a signature or on a website, doesn't qualify"; no
such option exists.

**Channels and actions.** ``PermissionChannel`` is ``whatsapp`` or ``instagram``;
``ProspectAction`` is what the prospect did. ``VALID_ACTIONS`` maps each channel to the actions
it accepts, and nothing else is ever offered:

=========  ============================================================
Channel    Prospect actions
=========  ============================================================
whatsapp   ``started_whatsapp_conversation``, ``gave_number_for_whatsapp``
instagram  ``started_instagram_conversation``
=========  ============================================================

**The machine.** ``PermissionStatus`` is ``active`` or ``revoked`` (``TERMINAL``). Recording
creates an ``active`` row (``CREATION``: cause ``permission_recorded``); ``transition(current,
event)`` follows ``TRANSITIONS`` or raises ``IllegalTransition``:

=========  ======  =======  ===================
Event      From    To       Cause
=========  ======  =======  ===================
revoke     active  revoked  permission_revoked
opt_out    active  revoked  opted_out
erase      active  revoked  contact_erased
=========  ======  =======  ===================

Updating an Instagram permission's ``last_inbound_at`` keeps it ``active`` (cause
``INBOUND_UPDATED``, ``inbound_updated``).

**The fields.** ``check_record(channel, action, occurred_at, evidence, number, handle, *, now,
tz)`` checks the record form and returns ``RecordFields``, or raises ``PermissionRefused`` with
every refused field's message:

- ``channel``: a ``PermissionChannel`` value (``BAD_CHANNEL``); ``action``: one of
  ``VALID_ACTIONS[channel]`` (``BAD_ACTION``).
- ``occurred_at``: a local date and time in ``tz`` (``parse_local``), not after ``now``
  (``FUTURE_TIME``).
- ``evidence``: ``check_evidence``: each ``\\r\\n`` (a browser textarea's line break) read as
  ``\\n``, then 1 to ``MAX_EVIDENCE`` (``extraction.MAX_QUOTE``, 300) characters after stripping
  (``BLANK_EVIDENCE``, ``LONG_EVIDENCE``), with no control character but tab and line breaks
  and no surrogate (``BAD_EVIDENCE``).
- the channel's own identifier, required: a WhatsApp number (``check_number``: ``+`` and 8 to 15
  digits once spaces and hyphens are removed, ``BAD_NUMBER``) or an Instagram handle
  (``check_handle``: 1 to 30 of ``A-Z a-z 0-9 . _``, one leading ``@`` dropped, ``BAD_HANDLE``);
  the other channel's field must be blank (``OTHER_CHANNEL``).

``parse_local(text, tz)`` reads ``YYYY-MM-DDTHH:MM`` (a space for ``T`` and ``:SS`` accepted,
surrounding blanks stripped) as a local time in ``tz`` and returns it in UTC; ``ValueError``
with ``BAD_TIME`` (also for a time past ``datetime``'s bounds once in UTC), or
``NO_SUCH_TIME`` for a local time the clocks skipped (a DST gap). An ambiguous local time (the
clocks went back) is its first occurrence.

``check_inbound(text, *, now, tz, stored)`` reads the new ``last_inbound_at`` of an Instagram
permission: a local time (``parse_local``), not after ``now`` (``FUTURE_TIME``), and after the
stored one (``NOT_LATER``); ``PermissionRefused`` on ``last_inbound_at`` otherwise.

**The 24-hour window** (Instagram only). ``window_closes_at(last_inbound_at)`` is
``last_inbound_at + 24 h``; ``window_open(last_inbound_at, now)`` is whether ``now`` is before
it (at exactly 24 h the window is closed). ``window_text(last_inbound_at, now, tz)`` is
"24-hour window closes at <time>" while open and "24-hour window closed at <time>: don't send"
once closed, ``<time>`` being ``local_text(at, tz)``: ``YYYY-MM-DD HH:MM (<zone>)`` in ``tz``.

**Blocks.** ``blocked_text(reasons)`` is the refusal a ``Blocked`` contactability answer gives:
"Nothing was recorded: this Contact can't be contacted (<reason>; ...)", each reason through
``BLOCK_TEXTS``. ``PermissionRefused`` (code ``channel_permission_refused``) carries
``.problems``, each refused field (or ``form``) to its message; its own message names the
fields, never a value. Every time must be an aware ``datetime`` and ``tz`` a ``tzinfo``
(``TypeError`` otherwise).
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.contactability import BlockReason
from nextron.domain.errors import DomainError
from nextron.domain.extraction import MAX_QUOTE
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "channel_permission"
MAX_EVIDENCE: Final = MAX_QUOTE  # research stores the Evidence as a quote
MIN_NUMBER_DIGITS: Final = 8
MAX_NUMBER_DIGITS: Final = 15
MAX_NUMBER_LENGTH: Final = MAX_NUMBER_DIGITS + 1  # the "+"
MAX_HANDLE_LENGTH: Final = 30
REPLY_WINDOW: Final = timedelta(hours=24)
RECORDED_BY: Final = "operator"
LOCAL_FORMAT: Final = "%Y-%m-%d %H:%M"

# Field names, as the form and the table use them.
FORM_FIELD: Final = "form"
CHANNEL_FIELD: Final = "channel"
ACTION_FIELD: Final = "prospect_action"
OCCURRED_FIELD: Final = "occurred_at"
EVIDENCE_FIELD: Final = "evidence"
NUMBER_FIELD: Final = "whatsapp_number"
HANDLE_FIELD: Final = "instagram_handle"
INBOUND_FIELD: Final = "last_inbound_at"

# Messages.
NOT_QUALIFYING: Final = (
    "An email reply, or a number in a signature or on a website, doesn't qualify"
)
BAD_CHANNEL: Final = "Choose WhatsApp or Instagram"
BAD_ACTION: Final = "Choose what the prospect did on this channel"
BAD_TIME: Final = "Enter a date and time, such as 2026-11-18T14:30"
NO_SUCH_TIME: Final = "This local time doesn't exist (the clocks changed then); enter another"
FUTURE_TIME: Final = "Can't be in the future"
NOT_LATER: Final = "Must be later than the last inbound message already recorded"
BLANK_EVIDENCE: Final = "Describe what the prospect did"
LONG_EVIDENCE: Final = f"At most {MAX_EVIDENCE} characters"
BAD_EVIDENCE: Final = "Plain text only"
BAD_NUMBER: Final = "Enter the WhatsApp number as + and 8 to 15 digits, such as +447700900123"
BAD_HANDLE: Final = "Enter the Instagram handle: 1 to 30 letters, digits, dots or underscores"
OTHER_CHANNEL: Final = "Leave this blank for the chosen channel"
UNKNOWN_PERMISSION: Final = "This permission no longer exists"
ALREADY_REVOKED: Final = "This permission is already revoked"
NO_WINDOW: Final = "Only an Instagram permission has a last inbound message to update"
NOT_ACTIVE: Final = "Only an active permission can be updated"

_ALLOWED_CONTROLS: Final = frozenset("\t\n\r")
_REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cs"})
_LOCAL_TIME: Final = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?"
)
_NUMBER: Final = re.compile(rf"\+[0-9]{{{MIN_NUMBER_DIGITS},{MAX_NUMBER_DIGITS}}}")
_HANDLE: Final = re.compile(rf"[A-Za-z0-9._]{{1,{MAX_HANDLE_LENGTH}}}")
_NUMBER_SEPARATORS: Final = re.compile(r"[ \-]")


class PermissionChannel(StrEnum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"


class ProspectAction(StrEnum):
    STARTED_WHATSAPP_CONVERSATION = "started_whatsapp_conversation"
    GAVE_NUMBER_FOR_WHATSAPP = "gave_number_for_whatsapp"
    STARTED_INSTAGRAM_CONVERSATION = "started_instagram_conversation"


VALID_ACTIONS: Final[Mapping[PermissionChannel, tuple[ProspectAction, ...]]] = MappingProxyType(
    {
        PermissionChannel.WHATSAPP: (
            ProspectAction.STARTED_WHATSAPP_CONVERSATION,
            ProspectAction.GAVE_NUMBER_FOR_WHATSAPP,
        ),
        PermissionChannel.INSTAGRAM: (ProspectAction.STARTED_INSTAGRAM_CONVERSATION,),
    }
)

CHANNEL_LABELS: Final[Mapping[PermissionChannel, str]] = MappingProxyType(
    {PermissionChannel.WHATSAPP: "WhatsApp", PermissionChannel.INSTAGRAM: "Instagram"}
)
ACTION_LABELS: Final[Mapping[ProspectAction, str]] = MappingProxyType(
    {
        ProspectAction.STARTED_WHATSAPP_CONVERSATION: "Started a WhatsApp conversation with us",
        ProspectAction.GAVE_NUMBER_FOR_WHATSAPP: "Gave us their number to use on WhatsApp",
        ProspectAction.STARTED_INSTAGRAM_CONVERSATION: (
            "Started an Instagram conversation with us"
        ),
    }
)


class PermissionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


TERMINAL: Final = (PermissionStatus.REVOKED,)


class PermissionEvent(StrEnum):
    REVOKE = "revoke"
    OPT_OUT = "opt_out"
    ERASE = "erase"


class PermissionCause(StrEnum):
    RECORDED = "permission_recorded"
    INBOUND_UPDATED = "inbound_updated"
    REVOKED = "permission_revoked"
    OPTED_OUT = "opted_out"
    CONTACT_ERASED = "contact_erased"


CREATION: Final = Transition(PermissionStatus.ACTIVE, PermissionCause.RECORDED)
TRANSITIONS: Final[
    Mapping[
        tuple[PermissionStatus, PermissionEvent],
        Transition[PermissionStatus, PermissionCause],
    ]
] = MappingProxyType(
    {
        (PermissionStatus.ACTIVE, PermissionEvent.REVOKE): Transition(
            PermissionStatus.REVOKED, PermissionCause.REVOKED
        ),
        (PermissionStatus.ACTIVE, PermissionEvent.OPT_OUT): Transition(
            PermissionStatus.REVOKED, PermissionCause.OPTED_OUT
        ),
        (PermissionStatus.ACTIVE, PermissionEvent.ERASE): Transition(
            PermissionStatus.REVOKED, PermissionCause.CONTACT_ERASED
        ),
    }
)

BLOCK_TEXTS: Final[Mapping[BlockReason, str]] = MappingProxyType(
    {
        BlockReason.ADDRESS_SUPPRESSED: "the email address is on the Suppression List",
        BlockReason.ADDRESS_DOMAIN_SUPPRESSED: (
            "the email address's domain is on the Suppression List"
        ),
        BlockReason.LEAD_DOMAIN_SUPPRESSED: "the Lead's domain is on the Suppression List",
        BlockReason.PERSON_SUPPRESSED: "the person is on the Suppression List",
        BlockReason.PROFILE_SUPPRESSED: "the person's profile is on the Suppression List",
        BlockReason.LEAD_LOST: "the Lead is closed as lost",
        BlockReason.LEAD_DISQUALIFIED: "the Lead is disqualified",
        BlockReason.CONTACT_ERASED: "the Contact was erased",
    }
)


class PermissionRefused(DomainError):
    """An Operator's Channel Permission action refused: ``problems`` maps each refused field
    (or ``form``) to its message. The message names the fields, never a value."""

    code = "channel_permission_refused"

    def __init__(self, problems: Mapping[str, str]) -> None:
        self.problems: dict[str, str] = dict(problems)
        super().__init__(f"channel permission refused: {', '.join(self.problems) or 'no field'}")


@dataclass(frozen=True, slots=True)
class RecordFields:
    """A checked record form: the channel and action, when (UTC), the stripped Evidence and the
    channel's identifier (the other one ``None``)."""

    channel: PermissionChannel
    action: ProspectAction
    occurred_at: datetime
    evidence: str
    whatsapp_number: str | None
    instagram_handle: str | None


def transition(
    current: PermissionStatus, event: PermissionEvent
) -> Transition[PermissionStatus, PermissionCause]:
    """Where ``event`` leads from ``current``; ``IllegalTransition`` when it can't."""
    found = TRANSITIONS.get((PermissionStatus(current), PermissionEvent(event)))
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{name} must be an aware datetime")
    return value


def _zone(tz: object) -> tzinfo:
    if not isinstance(tz, tzinfo):
        raise TypeError(f"tz must be a tzinfo, not {type(tz).__name__}")
    return tz


def parse_local(text: object, tz: tzinfo) -> datetime:
    """The local time ``text`` in ``tz``, in UTC; see the module docstring."""
    zone = _zone(tz)
    found = _LOCAL_TIME.fullmatch(text.strip()) if isinstance(text, str) else None
    if found is None:
        raise ValueError(BAD_TIME)
    parts = [int(part) if part else 0 for part in found.groups()]
    try:
        naive = datetime(*parts)  # noqa: DTZ001 - a wall time, given its zone just below
    except ValueError:
        raise ValueError(BAD_TIME) from None
    local = naive.replace(tzinfo=zone)
    try:
        in_utc = local.astimezone(UTC)
    except OverflowError:  # past datetime's bounds once in UTC
        raise ValueError(BAD_TIME) from None
    if in_utc.astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError(NO_SUCH_TIME)
    return in_utc


def check_evidence(value: object) -> str:
    """The stripped Evidence text, each ``\\r\\n`` read as ``\\n``; ``ValueError`` with its
    problem."""
    stripped = value.replace("\r\n", "\n").strip() if isinstance(value, str) else ""
    if not stripped:
        raise ValueError(BLANK_EVIDENCE)
    if len(stripped) > MAX_EVIDENCE:
        raise ValueError(LONG_EVIDENCE)
    if any(
        unicodedata.category(char) in _REFUSED_CATEGORIES and char not in _ALLOWED_CONTROLS
        for char in stripped
    ):
        raise ValueError(BAD_EVIDENCE)
    return stripped


def check_number(value: object) -> str:
    """The WhatsApp number, spaces and hyphens removed; ``ValueError(BAD_NUMBER)``."""
    text = _NUMBER_SEPARATORS.sub("", value.strip()) if isinstance(value, str) else ""
    if _NUMBER.fullmatch(text) is None:
        raise ValueError(BAD_NUMBER)
    return text


def check_handle(value: object) -> str:
    """The Instagram handle, one leading ``@`` dropped; ``ValueError(BAD_HANDLE)``."""
    text = value.strip() if isinstance(value, str) else ""
    if text.startswith("@"):
        text = text[1:]
    if _HANDLE.fullmatch(text) is None:
        raise ValueError(BAD_HANDLE)
    return text


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def check_record(
    channel: object,
    action: object,
    occurred_at: object,
    evidence: object,
    number: object,
    handle: object,
    *,
    now: datetime,
    tz: tzinfo,
) -> RecordFields:
    """The checked record form; ``PermissionRefused`` with every problem."""
    moment = _aware("now", now)
    zone = _zone(tz)
    problems: dict[str, str] = {}
    chosen = (
        PermissionChannel(channel)
        if isinstance(channel, str) and channel in set(PermissionChannel)
        else None
    )
    if chosen is None:
        problems[CHANNEL_FIELD] = BAD_CHANNEL
    did = ProspectAction(action) if isinstance(action, str) and action in set(ProspectAction) else None
    if did is None or (chosen is not None and did not in VALID_ACTIONS[chosen]):
        problems[ACTION_FIELD] = BAD_ACTION
    when: datetime | None = None
    try:
        when = parse_local(occurred_at, zone)
    except ValueError as refused:
        problems[OCCURRED_FIELD] = str(refused)
    if when is not None and when > moment:
        problems[OCCURRED_FIELD] = FUTURE_TIME
    text = ""
    try:
        text = check_evidence(evidence)
    except ValueError as refused:
        problems[EVIDENCE_FIELD] = str(refused)
    whatsapp: str | None = None
    insta: str | None = None
    if chosen is PermissionChannel.WHATSAPP:
        try:
            whatsapp = check_number(number)
        except ValueError as refused:
            problems[NUMBER_FIELD] = str(refused)
        if not _blank(handle):
            problems[HANDLE_FIELD] = OTHER_CHANNEL
    elif chosen is PermissionChannel.INSTAGRAM:
        try:
            insta = check_handle(handle)
        except ValueError as refused:
            problems[HANDLE_FIELD] = str(refused)
        if not _blank(number):
            problems[NUMBER_FIELD] = OTHER_CHANNEL
    if problems or chosen is None or did is None or when is None:
        raise PermissionRefused(problems)
    return RecordFields(chosen, did, when, text, whatsapp, insta)


def check_inbound(text: object, *, now: datetime, tz: tzinfo, stored: datetime) -> datetime:
    """The new ``last_inbound_at`` (UTC); ``PermissionRefused`` on ``last_inbound_at``."""
    moment = _aware("now", now)
    before = _aware("stored", stored)
    try:
        when = parse_local(text, tz)
    except ValueError as refused:
        raise PermissionRefused({INBOUND_FIELD: str(refused)}) from None
    if when > moment:
        raise PermissionRefused({INBOUND_FIELD: FUTURE_TIME})
    if when <= before:
        raise PermissionRefused({INBOUND_FIELD: NOT_LATER})
    return when


def window_closes_at(last_inbound_at: datetime) -> datetime:
    """When the 24-hour reply window opened by ``last_inbound_at`` closes."""
    return _aware("last_inbound_at", last_inbound_at) + REPLY_WINDOW


def window_open(last_inbound_at: datetime, now: datetime) -> bool:
    """Whether ``now`` is still inside the 24-hour window (closed at exactly 24 h)."""
    return _aware("now", now) < window_closes_at(last_inbound_at)


def local_text(at: datetime, tz: tzinfo) -> str:
    """``at`` as ``YYYY-MM-DD HH:MM (<zone>)`` in ``tz``."""
    zone = _zone(tz)
    return f"{_aware('at', at).astimezone(zone):{LOCAL_FORMAT}} ({zone})"


def window_text(last_inbound_at: datetime, now: datetime, tz: tzinfo) -> str:
    """The window's line; see the module docstring."""
    closes = local_text(window_closes_at(last_inbound_at), tz)
    if window_open(last_inbound_at, now):
        return f"24-hour window closes at {closes}"
    return f"24-hour window closed at {closes}: don't send"


def already_active(channel: PermissionChannel) -> str:
    """The refusal of a second ``active`` permission on ``channel``."""
    return f"This Contact already has an active {CHANNEL_LABELS[channel]} permission"


def blocked_text(reasons: Iterable[BlockReason]) -> str:
    """The refusal of a Contact the contactability gate blocks."""
    texts = "; ".join(BLOCK_TEXTS[BlockReason(reason)] for reason in reasons)
    return f"Nothing was recorded: this Contact can't be contacted ({texts})"
