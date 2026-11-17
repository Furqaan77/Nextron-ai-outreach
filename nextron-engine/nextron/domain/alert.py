"""The Alert machine and the rules of ops alerts and dashboard notifications (AD-4, AD-15, AD-20,
INT-8, UX-DR5). Standard library only.

**Statuses, events and causes.** A Telegram alert is ``pending``, ``sent``, ``unknown`` or
``failed``. ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``; a creation has no ``from`` status and cause ``requested``
(``CREATION``).

=========  =========  ========  ===============
From       Event      To        Cause
=========  =========  ========  ===============
pending    deliver    sent      delivered
pending    lose       unknown   outcome_unknown
pending    fail       failed    failed
unknown    deliver    sent      delivered
unknown    fail       failed    failed
=========  =========  ========  ===============

**Channels.** ``Channel`` is ``telegram`` or ``dashboard``. ``DELIVERY_CHANNELS`` (Telegram) get
an ``alerts`` row and a ``send_alert`` job; the dashboard gets a ``notifications`` row.
``RESENDS_UNKNOWN`` (Telegram) re-sends an ``unknown`` alert once, ``RESEND_DELAY`` (60 s) after
the first send.

**Names.** ``SEND_ALERT_JOB`` is the job type that sends one alert, ``JOB_DEAD`` the ops alert
kind of a dead job, and ``NOT_DELIVERED`` the notification kind of an alert Telegram didn't
take.

**Checks.** ``check_alert(kind, subject, text, channels)`` returns the channels as a
``frozenset`` and raises ``InvalidAlert`` (code ``invalid_alert``) unless:

- ``kind`` fullmatches ``[a-z][a-z0-9_]{0,31}``;
- ``subject`` is a ULID or fullmatches ``[a-z0-9_:.-]{1,64}``;
- ``text`` is a ``str``, not blank, of at most ``MAX_TEXT_UNITS`` (4,096, Telegram's limit)
  UTF-16 code units, with no ``Cc``, ``Cf``, ``Cs``, ``Zl`` or ``Zp`` character except ``\\n``;
- ``channels`` is a non-empty iterable of ``Channel`` members; a bare ``str`` (a ``Channel``
  member included) is refused.

The reason names what is wrong, never a value.

``is_telegram_chat_id(value)`` fullmatches ``-?[1-9][0-9]{0,19}``: a numeric chat id, never a
``@name``. ``job_dead_text(job_type, job_id, attempts)`` is exactly ``"Job dead: <type> <ULID>,
attempts: <n>"``; ops alert text carries IDs and counts only. ``not_delivered_text(kind,
alert_id, code, maybe=False)`` is the dashboard notice of an alert Telegram didn't take.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "alert"

SEND_ALERT_JOB: Final = "send_alert"
JOB_DEAD: Final = "job_dead"
NOT_DELIVERED: Final = "alert_not_delivered"

NOT_DELIVERED_LEAD: Final = "Telegram alert not delivered"
NOT_DELIVERED_MAYBE_LEAD: Final = "Telegram alert may not have been delivered"

RESEND_DELAY: Final = timedelta(seconds=60)
MAX_TEXT_UNITS: Final = 4096  # Telegram's limit, in UTF-16 code units
MAX_KIND_LENGTH: Final = 32
MAX_SUBJECT_LENGTH: Final = 64

# Each matched with fullmatch.
KIND_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
SUBJECT_PATTERN: Final = re.compile(r"[a-z0-9_:.-]{1,64}")
CHAT_ID_PATTERN: Final = re.compile(r"-?[1-9][0-9]{0,19}")
# control, format, surrogate, line separator and paragraph separator characters
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
ALLOWED_CONTROL: Final = "\n"


# The machine ----------------------------------------------------------------------------------


class AlertStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    UNKNOWN = "unknown"
    FAILED = "failed"


class AlertEvent(StrEnum):
    DELIVER = "deliver"
    LOSE = "lose"
    FAIL = "fail"


class AlertCause(StrEnum):
    REQUESTED = "requested"
    DELIVERED = "delivered"
    OUTCOME_UNKNOWN = "outcome_unknown"
    FAILED = "failed"


CREATION: Final[Transition[AlertStatus, AlertCause]] = Transition(
    AlertStatus.PENDING, AlertCause.REQUESTED
)

TRANSITIONS: Final[Mapping[tuple[AlertStatus, AlertEvent], Transition[AlertStatus, AlertCause]]] = (
    MappingProxyType(
        {
            (AlertStatus.PENDING, AlertEvent.DELIVER): Transition(
                AlertStatus.SENT, AlertCause.DELIVERED
            ),
            (AlertStatus.PENDING, AlertEvent.LOSE): Transition(
                AlertStatus.UNKNOWN, AlertCause.OUTCOME_UNKNOWN
            ),
            (AlertStatus.PENDING, AlertEvent.FAIL): Transition(
                AlertStatus.FAILED, AlertCause.FAILED
            ),
            (AlertStatus.UNKNOWN, AlertEvent.DELIVER): Transition(
                AlertStatus.SENT, AlertCause.DELIVERED
            ),
            (AlertStatus.UNKNOWN, AlertEvent.FAIL): Transition(
                AlertStatus.FAILED, AlertCause.FAILED
            ),
        }
    )
)


def transition(current: AlertStatus, event: AlertEvent) -> Transition[AlertStatus, AlertCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


# Channels -------------------------------------------------------------------------------------


class Channel(StrEnum):
    """Where an alert goes: Telegram, or the dashboard's notification area."""

    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"


DELIVERY_CHANNELS: Final = frozenset({Channel.TELEGRAM})  # these get an alerts row and a job
RESENDS_UNKNOWN: Final = frozenset({Channel.TELEGRAM})  # these re-send an unknown alert once


# Checks ---------------------------------------------------------------------------------------


class InvalidAlert(DomainError):
    """An alert ``check_alert`` refuses; ``reason`` names what is wrong, never a value."""

    code = "invalid_alert"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def text_units(text: str) -> int:
    """The length of ``text`` in UTF-16 code units: a character outside the BMP counts 2."""
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _refused_character(text: str) -> bool:
    return any(
        character != ALLOWED_CONTROL and unicodedata.category(character) in REFUSED_CATEGORIES
        for character in text
    )


def is_alert_subject(value: object) -> bool:
    """Whether ``value`` is a ULID or a token of ``[a-z0-9_:.-]{1,64}``."""
    return isinstance(value, str) and (
        is_ulid(value) or SUBJECT_PATTERN.fullmatch(value) is not None
    )


def check_alert(
    kind: object, subject: object, text: object, channels: object
) -> frozenset[Channel]:
    """The alert's channels; raises ``InvalidAlert`` (see the module docstring)."""
    if not (isinstance(kind, str) and KIND_PATTERN.fullmatch(kind)):
        raise InvalidAlert("the kind must match [a-z][a-z0-9_]{0,31}")
    if not is_alert_subject(subject):
        raise InvalidAlert("the subject must be a ULID or a token of [a-z0-9_:.-]{1,64}")
    if not isinstance(text, str):
        raise InvalidAlert("the text must be a str")
    if not text.strip():
        raise InvalidAlert("the text must not be blank")
    if text_units(text) > MAX_TEXT_UNITS:
        raise InvalidAlert(f"the text exceeds {MAX_TEXT_UNITS:,} UTF-16 code units")
    if _refused_character(text):
        raise InvalidAlert(
            "the text must hold no control, format, surrogate or separator character other "
            "than a line feed"
        )
    if isinstance(channels, str | bytes) or not isinstance(channels, Iterable):
        raise InvalidAlert("the channels must be an iterable of Channel members, not a str")
    chosen = tuple(channels)
    if not chosen:
        raise InvalidAlert("the alert needs at least one channel")
    if not all(isinstance(channel, Channel) for channel in chosen):
        raise InvalidAlert("every channel must be a Channel member")
    return frozenset(chosen)


def is_telegram_chat_id(value: object) -> bool:
    """Whether ``value`` is a numeric Telegram chat id (``-?[1-9][0-9]{0,19}``)."""
    return isinstance(value, str) and CHAT_ID_PATTERN.fullmatch(value) is not None


# Texts ----------------------------------------------------------------------------------------


def job_dead_text(job_type: str, job_id: str, attempts: int) -> str:
    """The ops alert text of a dead job: its type, its ULID and its attempts, nothing else."""
    return f"Job dead: {job_type} {job_id}, attempts: {attempts}"


def not_delivered_text(kind: str, alert_id: str, code: str, *, maybe: bool = False) -> str:
    """The dashboard notice of a Telegram alert that wasn't delivered (``maybe``: one whose
    resend's outcome is unknown too)."""
    lead = NOT_DELIVERED_MAYBE_LEAD if maybe else NOT_DELIVERED_LEAD
    return f"{lead}: {kind} {alert_id}, {code}"
