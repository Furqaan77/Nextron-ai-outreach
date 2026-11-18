"""The Operator's manual Pipeline moves, call bookings and deal details (Story 9.1, FR-45, FR-37,
FR-63). Standard library only, plus ``nextron.domain``.

**Refusals.** ``StageRefused(reason)`` is what ``nextron.app.pipeline.api.Pipeline`` raises when
it refuses a manual command, having written nothing; ``reason`` is a ``StageRefusal``:

==================  =====================================================================
Reason              When
==================  =====================================================================
automatic           the target is one of ``pipeline_stage.AUTOMATIC_STAGES``
illegal             the move (or a booking) isn't allowed from the Lead's stage
booking_incomplete  a move to ``call_booked`` without the call form, or a missing date,
                    time or Contact
bad_date_time       a date or time that isn't one, or a local time that doesn't exist
                    (a daylight-saving gap)
bad_contact         a Contact that isn't the Lead's, or is erased
no_timezone         no timezone typed and the Contact has none
bad_timezone        a timezone that isn't an IANA zone
already_closed      a close of a Lead that is already ``lost`` or ``disqualified``
not_reopenable      a reopen of a Lead that isn't closed, or whose stage before its close
                    isn't open or parked
bad_deal_value      a deal value that isn't whole US dollars from 0 to 1,000,000,000
notes_too_long      notes longer than ``MAX_NOTES_LENGTH`` (5,000) characters
==================  =====================================================================

**Deal details.** Money is integer micro-dollars (``MICROS_PER_DOLLAR``, 1,000,000).

- ``parse_deal_value(text)``: ``None`` for a blank text (it clears the value); otherwise the
  text, stripped, must be 1 to 10 ASCII digits worth at most ``MAX_DEAL_DOLLARS``
  (1,000,000,000), and gives that many dollars in micro-dollars; anything else raises
  ``StageRefused(bad_deal_value)``.
- ``clean_notes(text)``: CRLF (and a lone CR) becomes LF; a blank text gives ``None`` (it clears
  the notes); more than ``MAX_NOTES_LENGTH`` characters raises ``StageRefused(notes_too_long)``.
  Otherwise the normalized text, unstripped.
- ``check_deal(deal_value, notes)``: what ``Pipeline.save_deal`` accepts: ``deal_value``
  ``None`` or an ``int`` (not a ``bool``) that is a whole number of dollars in range, and
  ``notes`` ``None`` or a non-blank ``str`` without CR of at most 5,000 characters; a
  wrong type raises ``TypeError``, a bad value ``StageRefused``.

**Call time.** ``check_timezone(name)`` returns the ``ZoneInfo`` of an IANA zone name (at most 64
characters of ``[A-Za-z0-9_+-]`` parts joined by ``/``), else raises
``StageRefused(bad_timezone)``. ``call_time(day, at, zone)`` reads ``day`` (``YYYY-MM-DD``) and
``at`` (``HH:MM`` or ``HH:MM:SS``, 24-hour) as a local time in ``zone`` and returns it in UTC; a
text that isn't a real date or time, a local time the zone skips, or one whose UTC time
falls outside the representable range (year 1 or 9999), raises
``StageRefused(bad_date_time)``. An ambiguous local time (the hour repeated when clocks go back)
is its first occurrence.
"""

import re
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MICROS_PER_DOLLAR: Final = 1_000_000
MAX_DEAL_DOLLARS: Final = 1_000_000_000
MAX_DEAL_VALUE: Final = MAX_DEAL_DOLLARS * MICROS_PER_DOLLAR
MAX_NOTES_LENGTH: Final = 5_000
MAX_TIMEZONE_LENGTH: Final = 64

DOLLARS_PATTERN: Final = re.compile(r"[0-9]{1,10}")  # fullmatch; ASCII digits only
TIMEZONE_PATTERN: Final = re.compile(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*")  # fullmatch
DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # fullmatch
TIME_PATTERN: Final = re.compile(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?")  # fullmatch


class StageRefusal(StrEnum):
    AUTOMATIC = "automatic"
    ILLEGAL = "illegal"
    BOOKING_INCOMPLETE = "booking_incomplete"
    BAD_DATE_TIME = "bad_date_time"
    BAD_CONTACT = "bad_contact"
    NO_TIMEZONE = "no_timezone"
    BAD_TIMEZONE = "bad_timezone"
    ALREADY_CLOSED = "already_closed"
    NOT_REOPENABLE = "not_reopenable"
    BAD_DEAL_VALUE = "bad_deal_value"
    NOTES_TOO_LONG = "notes_too_long"


class StageRefused(Exception):
    """A manual pipeline command refused, nothing written; ``reason`` says why."""

    def __init__(self, reason: StageRefusal) -> None:
        super().__init__(str(reason))
        self.reason = reason


def parse_deal_value(text: str) -> int | None:
    """The deal value typed as ``text``, in micro-dollars, or ``None`` when blank."""
    stripped = text.strip()
    if not stripped:
        return None
    if DOLLARS_PATTERN.fullmatch(stripped) is None:
        raise StageRefused(StageRefusal.BAD_DEAL_VALUE)
    dollars = int(stripped)
    if dollars > MAX_DEAL_DOLLARS:
        raise StageRefused(StageRefusal.BAD_DEAL_VALUE)
    return dollars * MICROS_PER_DOLLAR


def clean_notes(text: str) -> str | None:
    """The notes typed as ``text``, line breaks normalized, or ``None`` when blank."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return None
    if len(normalized) > MAX_NOTES_LENGTH:
        raise StageRefused(StageRefusal.NOTES_TOO_LONG)
    return normalized


def check_deal(deal_value: object, notes: object) -> None:
    """Raise unless ``deal_value`` and ``notes`` can be stored; see the module docstring."""
    if deal_value is not None:
        if not isinstance(deal_value, int) or isinstance(deal_value, bool):
            raise TypeError(f"deal_value must be an int, not {type(deal_value).__name__}")
        if not 0 <= deal_value <= MAX_DEAL_VALUE or deal_value % MICROS_PER_DOLLAR:
            raise StageRefused(StageRefusal.BAD_DEAL_VALUE)
    if notes is not None:
        if not isinstance(notes, str):
            raise TypeError(f"notes must be a str, not {type(notes).__name__}")
        if "\r" in notes or not notes.strip():
            raise ValueError("notes must be normalized by clean_notes")
        if len(notes) > MAX_NOTES_LENGTH:
            raise StageRefused(StageRefusal.NOTES_TOO_LONG)


def check_timezone(name: object) -> ZoneInfo:
    """The ``ZoneInfo`` named ``name``; ``StageRefused(bad_timezone)`` when it is none."""
    if (
        not isinstance(name, str)
        or len(name) > MAX_TIMEZONE_LENGTH
        or TIMEZONE_PATTERN.fullmatch(name) is None
    ):
        raise StageRefused(StageRefusal.BAD_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise StageRefused(StageRefusal.BAD_TIMEZONE) from None


def call_time(day: str, at: str, zone: ZoneInfo) -> datetime:
    """The local ``day`` and ``at`` in ``zone``, in UTC; see the module docstring."""
    if DATE_PATTERN.fullmatch(day) is None or TIME_PATTERN.fullmatch(at) is None:
        raise StageRefused(StageRefusal.BAD_DATE_TIME)
    try:
        local = datetime.combine(date.fromisoformat(day), time.fromisoformat(at))
    except ValueError:
        raise StageRefused(StageRefusal.BAD_DATE_TIME) from None
    aware = local.replace(tzinfo=zone, fold=0)
    try:
        in_utc = aware.astimezone(UTC)
        back = in_utc.astimezone(zone).replace(tzinfo=None)
    except OverflowError:  # past datetime's range once shifted (year 1 or 9999)
        raise StageRefused(StageRefusal.BAD_DATE_TIME) from None
    if back != local:  # skipped by the zone
        raise StageRefused(StageRefusal.BAD_DATE_TIME)
    return in_utc
