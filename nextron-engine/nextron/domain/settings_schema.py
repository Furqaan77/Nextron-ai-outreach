"""Operator settings, declared once with their types (AD-12, SC-5, NFR-7, NFR-40, UX-DR23).

Every setting the engine keeps in the database is declared here, in ``SCHEMA``, and nowhere
else; bootstrap config (``nextron.bootstrap``) holds only what the engine needs before it has a
database. A declaration is a ``Setting``:

- ``key``: a ``Key`` member, ``[a-z][a-z0-9_]{0,30}``: at most 31 characters, because log
  redaction masks any run of 32 or more as ``<key>``;
- ``type``: a ``SettingType`` (the table below);
- ``section``: the ``Section`` of the Settings page it appears in (sections render in ``Section``
  order, and a section without settings doesn't render);
- ``label`` and ``unit``: the field's label and hint, neither empty;
- ``default``: a typed value in canonical form, or ``REQUIRED``;
- ``rules``: extra checks on the typed value (``minimum``, ``maximum``, ``max_length`` or any
  ``Rule``);
- ``timezone_key``: for a ``time`` or ``availability`` setting only, the key of the declared
  ``timezone`` setting its times are in.

Consumers read a typed value through ``nextron.app.queries.setup.get(Key.<NAME>)``; the
Settings page and ``nextron.app.setup.api.Setup`` write them. Other modules reach the schema only
through ``setting(key)`` (``UnknownSetting`` for an undeclared key) and ``declared()`` (every
setting, in declaration order); both read ``SCHEMA`` when called, so a test can swap it with
``monkeypatch.setattr(settings_schema, "SCHEMA", probe)``.

**Text forms.** The ``settings`` table stores each value as the canonical text ``encode``
returns, and ``parse`` reads it back (and reads what the Operator types, which may be in a
looser form). By type, the value and its text:

- ``string``: a ``str``; one line, stripped, with no ``Cc``, ``Cf``, ``Cs``, ``Zl`` or ``Zp``
  character, at most 1,000 characters.
- ``integer``: an ``int`` (not a ``bool``); ``-?[0-9]{1,18}``.
- ``boolean``: a ``bool``; ``true`` or ``false``.
- ``time``: a ``datetime.time`` in whole minutes, without ``tzinfo``; ``HH:MM`` from 00:00 to
  23:59.
- ``timezone``: a ``str``; a region name that is an exact member of
  ``zoneinfo.available_timezones()``. Every name without a ``/`` except ``UTC`` (``EST``,
  ``EST5EDT``, ``GB``, ``Zulu``, ``Factory``) and every ``Etc/`` name (``Etc/GMT+5`` is UTC-5)
  is refused: fixed offsets don't follow daylight saving. Region links such as ``US/Pacific``
  work.
- ``duration``: a ``timedelta``, zero or more, in whole seconds, under 1,000,000 days; ISO 8601
  ``PnW`` or ``PnDTnHnMnS``, uppercase, each n at most 6 digits, no years or months. The
  canonical text is days, then the ``T`` part: ``P1W`` is ``P7D``, and zero is ``PT0S``.
- ``money``: an ``int`` of micro-dollars, zero or more; US dollars: 1 to 9 digits, then
  optionally a point and 1 to 6 digits. The canonical text has at least 2 decimals: ``20`` is
  ``20.00``.
- ``list``: a ``tuple[str, ...]``; one item per line, blank lines dropped, each item a valid
  string, at most 200 items, no duplicates.
- ``availability``: a ``tuple[Slot, ...]``; one slot per line, such as ``Mon 09:00-17:00``
  (``Mon`` to ``Sun``, any case), start before end, no overlaps within a day, at most 200
  lines. The canonical text is sorted.

- ``parse(setting, text)`` reads a text form, then applies the type's checks and the rules.
- ``encode(setting, value)`` checks the type, then the rules, then returns the canonical text.
  It normalises what has one meaning: a string is stripped, list items are stripped, and an
  availability is sorted.
- A stored text is never blank, because a blank field on the Settings page means "use the
  default": ``parse`` and ``encode`` refuse blank with rule ``required``. A default may be blank
  (an empty string, list or availability).
- A refusal raises ``InvalidSettingValue`` with ``.key``, ``.rule`` (the type's name, a rule's
  name or ``required``) and ``.reason``; the message is ``"<key>: <reason> (rule <name>)"``.
  No message here ever includes a setting's value.

``schema_problems`` checks a schema; this module runs it on ``SCHEMA`` at import
(``check_schema``) and raises ``TypeError`` listing what it finds. No setting may disable
Suppression, Eligibility, the footer, opt-out handling or Market rules, and no setting holds a
secret (secrets live only in Windows Credential Manager, AD-12);
``tests/contracts/test_settings_contracts.py`` checks the key names and labels.
"""

import re
import unicodedata
import zoneinfo
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import time, timedelta
from enum import Enum, StrEnum
from functools import cache
from types import MappingProxyType
from typing import Any, Final, NamedTuple

from nextron.domain.alert import is_telegram_chat_id
from nextron.domain.budget import MAX_PERCENT, NEVER_BLOCKED
from nextron.domain.errors import DomainError
from nextron.domain.job import MAX_BULK_THREADS
from nextron.domain.sink import is_sink_recipient

KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
REQUIRED_RULE: Final = "required"
MAX_TEXT_LENGTH: Final = 1000
MAX_ITEMS: Final = 200
MAX_INTEGER: Final = 10**18 - 1  # 18 digits
MAX_DURATION_DAYS: Final = 999_999  # the canonical text's day count has at most 6 digits
MICROS_PER_DOLLAR: Final = 1_000_000
MAX_MONEY: Final = 10**9 * MICROS_PER_DOLLAR - 1  # $999,999,999.999999
WEEKDAYS: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ALLOWED_PLAIN_TIMEZONES: Final = frozenset({"UTC"})  # the one name without a "/" accepted
REFUSED_TIMEZONE_PREFIX: Final = "Etc/"  # fixed offsets such as Etc/GMT+5
# control, format, surrogate, line separator and paragraph separator characters
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class SettingType(StrEnum):
    """The type of a setting's value; its name is the rule a malformed value breaks."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TIME = "time"
    TIMEZONE = "timezone"
    DURATION = "duration"
    MONEY = "money"
    LIST = "list"
    AVAILABILITY = "availability"


class Section(StrEnum):
    """The sections of the Settings page (UX-DR23), in page order; the value is the heading."""

    FOOTER_AND_BOOKING = "Footer and booking"
    SENDER = "Sender"
    ALERTS_AND_RECIPIENTS = "Alerts and recipients"
    SENDING = "Sending"
    BUDGET_AND_LIMITS = "Budget and limits"
    ENGINE = "Engine"


class Key(StrEnum):
    """Every declared setting's key. A story that needs a setting adds it here and to
    ``SCHEMA``; ``schema_problems`` fails when the two differ."""

    SENDER_TIMEZONE = "sender_timezone"
    SINK_ALLOWLIST = "sink_allowlist"
    BULK_THREADS = "bulk_threads"
    ALERT_CHAT_ID = "alert_chat_id"
    QUOTA_HUNTER_CREDITS = "quota_hunter_credits"
    QUOTA_PROSPEO_CREDITS = "quota_prospeo_credits"
    QUOTA_PLACES_ENTERPRISE = "quota_places_enterprise"
    QUOTA_PLACES_PRO = "quota_places_pro"
    QUOTA_JOB_AGGREGATOR = "quota_job_aggregator"
    AI_BUDGET_MONTHLY = "ai_budget_monthly"
    AI_BUDGET_ALERT_PCT = "ai_budget_alert_pct"


class _Required(Enum):
    REQUIRED = "required"

    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED: Final = _Required.REQUIRED  # the default of a setting that has none


# Errors ---------------------------------------------------------------------------------------


class UnknownSetting(DomainError):
    """A key ``SCHEMA`` doesn't declare."""

    code = "unknown_setting"

    def __init__(self, key: object) -> None:
        named = isinstance(key, str) and KEY_PATTERN.fullmatch(key) is not None
        super().__init__(f"unknown setting: {key if named else '(not a setting name)'}")
        self.key = str(key) if named else None


class InvalidSettingValue(DomainError):
    """A value or text a setting refuses. ``rule`` is the type's name, a ``Rule``'s name or
    ``required``; ``reason`` says what a valid value is, never what the value was."""

    code = "invalid_setting"

    def __init__(self, key: str, rule: str, reason: str) -> None:
        super().__init__(f"{key}: {reason} (rule {rule})")
        self.key = str(key)
        self.rule = str(rule)
        self.reason = reason


class InvalidSettings(DomainError):
    """A save with at least one refused field; ``problems`` maps each such key to its refusal.
    Nothing was stored."""

    code = "invalid_settings"

    def __init__(self, problems: Mapping[str, InvalidSettingValue]) -> None:
        self.problems: Mapping[str, InvalidSettingValue] = MappingProxyType(dict(problems))
        listed = ", ".join(f"{key} (rule {problem.rule})" for key, problem in problems.items())
        super().__init__(f"settings not saved; refused: {listed}")


class SettingNotSet(DomainError):
    """A required setting nobody has set yet; consumers fail closed on it."""

    code = "setting_not_set"

    def __init__(self, key: str) -> None:
        super().__init__(f"{key} is not set; set it on the Settings page")
        self.key = str(key)


# Declarations ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """An extra check on a setting's typed value: ``check(value)`` is true when the value
    passes, and ``message`` says what a passing value is."""

    name: str
    message: str
    check: Callable[[Any], bool]


def minimum(bound: Any, shown: str) -> Rule:
    """The value is at least ``bound``; ``shown`` is the bound as the Operator reads it."""
    return Rule("minimum", f"must be at least {shown}", lambda value: value >= bound)


def maximum(bound: Any, shown: str) -> Rule:
    """The value is at most ``bound``; ``shown`` is the bound as the Operator reads it."""
    return Rule("maximum", f"must be at most {shown}", lambda value: value <= bound)


def max_length(n: int) -> Rule:
    """A string has at most ``n`` characters, or a list at most ``n`` items."""
    return Rule("max_length", f"must have a length of at most {n}", lambda value: len(value) <= n)


@dataclass(frozen=True, slots=True)
class Setting:
    """One declared setting; see the module docstring."""

    key: str
    type: SettingType
    section: Section
    label: str
    unit: str
    default: Any
    rules: tuple[Rule, ...] = ()
    timezone_key: str | None = None

    @property
    def required(self) -> bool:
        """Whether the setting has no default."""
        return self.default is REQUIRED


class Slot(NamedTuple):
    """One weekly availability slot: ``weekday`` 0 (Monday) to 6 (Sunday), from ``start`` to
    ``end``, in the timezone the setting's ``timezone_key`` names."""

    weekday: int
    start: time
    end: time


# Text forms -----------------------------------------------------------------------------------


class _Refused(Exception):
    """A type check failed; ``reason`` never holds the value."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _plain(text: str) -> str:
    """``text`` as a plain ``str`` (a ``StrEnum`` member becomes its value)."""
    return str.__str__(text)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


_LINE_BREAK: Final = re.compile(r"\r\n|\r|\n")
STRING_REASON: Final = (
    f"must be one line of at most {MAX_TEXT_LENGTH:,} characters, without control or "
    "formatting characters"
)
ITEM_REASON: Final = (
    f"must be at most {MAX_TEXT_LENGTH:,} characters, without control or formatting characters"
)


def _line(text: str) -> str | None:
    """``text`` stripped, or ``None`` when it is too long or holds a refused character."""
    line = text.strip()
    if len(line) > MAX_TEXT_LENGTH:
        return None
    if any(unicodedata.category(character) in REFUSED_CATEGORIES for character in line):
        return None
    return line


def _lines(text: str) -> list[tuple[int, str]]:
    """The non-blank lines of ``text``, stripped, each with its 1-based line number."""
    return [
        (number, line.strip())
        for number, line in enumerate(_LINE_BREAK.split(text), start=1)
        if line.strip()
    ]


# string


def _read_string(text: str) -> str:
    line = _line(text)
    if line is None:
        raise _Refused(STRING_REASON)
    return line


def _check_string(value: object) -> str:
    if not isinstance(value, str):
        raise _Refused("must be a str")
    return _read_string(_plain(value))


# integer

_INTEGER: Final = re.compile(r"-?[0-9]{1,18}")


def _read_integer(text: str) -> int:
    if not _INTEGER.fullmatch(text.strip()):
        raise _Refused("must be a whole number such as 42 or -3, with at most 18 digits")
    return int(text.strip())


def _check_integer(value: object) -> int:
    if not _is_int(value):
        raise _Refused("must be an int, not a bool")
    number = int(value)  # type: ignore[call-overload]
    if abs(number) > MAX_INTEGER:
        raise _Refused("must have at most 18 digits")
    return number


# boolean

BOOLEAN_TEXTS: Final = {"true": True, "false": False}


def _read_boolean(text: str) -> bool:
    value = BOOLEAN_TEXTS.get(text.strip())
    if value is None:
        raise _Refused("must be true or false")
    return value


def _check_boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise _Refused("must be a bool")
    return value


def _write_boolean(value: bool) -> str:
    return "true" if value else "false"


# time

_TIME: Final = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
TIME_REASON: Final = "must be a time of day as HH:MM, from 00:00 to 23:59"


def _hhmm(text: str) -> time | None:
    match = _TIME.fullmatch(text)
    return time(int(match[1]), int(match[2])) if match else None


def _read_time(text: str) -> time:
    value = _hhmm(text.strip())
    if value is None:
        raise _Refused(TIME_REASON)
    return value


def _check_time(value: object) -> time:
    if (
        not isinstance(value, time)
        or value.tzinfo is not None
        or value.second
        or value.microsecond
    ):
        raise _Refused("must be a time of day in whole minutes, without a timezone")
    return time(value.hour, value.minute)


def _write_time(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


# timezone

TIMEZONE_REASON: Final = (
    "must be an IANA timezone region name in exact capitals, such as America/Los_Angeles"
)


@cache
def timezone_names() -> frozenset[str]:
    """Every IANA timezone name a ``timezone`` setting accepts: the region names, plus
    ``UTC``."""
    return frozenset(
        name
        for name in zoneinfo.available_timezones()
        if ("/" in name or name in ALLOWED_PLAIN_TIMEZONES)
        and not name.startswith(REFUSED_TIMEZONE_PREFIX)
    )


def _read_timezone(text: str) -> str:
    name = text.strip()
    if name not in timezone_names():
        raise _Refused(TIMEZONE_REASON)
    return name


def _check_timezone(value: object) -> str:
    if not isinstance(value, str):
        raise _Refused("must be a str")
    return _read_timezone(_plain(value))


# duration

_DURATION: Final = re.compile(
    r"P(?:(?P<weeks>[0-9]{1,6})W"
    r"|(?:(?P<days>[0-9]{1,6})D)?"
    r"(?:T(?=[0-9])(?:(?P<hours>[0-9]{1,6})H)?(?:(?P<minutes>[0-9]{1,6})M)?"
    r"(?:(?P<seconds>[0-9]{1,6})S)?)?)"
)
DURATION_REASON: Final = (
    "must be an ISO 8601 duration such as PT30M, P1DT2H or P2W (no years or months)"
)


def _read_duration(text: str) -> timedelta:
    match = _DURATION.fullmatch(text.strip())
    parts = {} if match is None else {k: int(v) for k, v in match.groupdict().items() if v}
    if not parts:
        raise _Refused(DURATION_REASON)
    return _check_duration(timedelta(**parts))


def _check_duration(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise _Refused("must be a timedelta")
    if value < timedelta(0):
        raise _Refused("must not be negative")
    if value.microseconds:
        raise _Refused("must be a whole number of seconds")
    if value.days > MAX_DURATION_DAYS:
        raise _Refused(f"must be less than {MAX_DURATION_DAYS + 1:,} days")
    return timedelta(days=value.days, seconds=value.seconds)


def _write_duration(value: timedelta) -> str:
    hours, rest = divmod(value.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    days = f"{value.days}D" if value.days else ""
    clock = "".join(f"{n}{unit}" for n, unit in ((hours, "H"), (minutes, "M"), (seconds, "S")) if n)
    if not days and not clock:
        return "PT0S"
    return f"P{days}T{clock}" if clock else f"P{days}"


# money

_MONEY: Final = re.compile(r"([0-9]{1,9})(?:\.([0-9]{1,6}))?")


def _read_money(text: str) -> int:
    match = _MONEY.fullmatch(text.strip())
    if match is None:
        raise _Refused(
            "must be an amount in US dollars such as 20 or 20.50: digits, with at most 6 "
            "decimals and no sign, $ or thousands separator"
        )
    return int(match[1]) * MICROS_PER_DOLLAR + int((match[2] or "").ljust(6, "0"))


def _check_money(value: object) -> int:
    if not _is_int(value):
        raise _Refused("must be an int of micro-dollars, not a bool")
    micros = int(value)  # type: ignore[call-overload]
    if micros < 0:
        raise _Refused("must not be negative")
    if micros > MAX_MONEY:
        raise _Refused("must be less than 1,000,000,000 dollars")
    return micros


def _write_money(value: int) -> str:
    dollars, micros = divmod(value, MICROS_PER_DOLLAR)
    return f"{dollars}.{f'{micros:06d}'.rstrip('0').ljust(2, '0')}"


# list


def _unique(items: list[str], where: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    for item, place in zip(items, where, strict=True):
        if item in seen:
            raise _Refused(f"{place} repeats an earlier item")
        seen.add(item)
    return tuple(items)


def _read_list(text: str) -> tuple[str, ...]:
    lines = _lines(text)
    if len(lines) > MAX_ITEMS:
        raise _Refused(f"must have at most {MAX_ITEMS} items, one per line")
    items = []
    for number, text_line in lines:
        item = _line(text_line)
        if item is None:
            raise _Refused(f"line {number} {ITEM_REASON}")
        items.append(item)
    return _unique(items, [f"line {number}" for number, _ in lines])


def _check_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise _Refused("must be a tuple of str")
    if len(value) > MAX_ITEMS:
        raise _Refused(f"must have at most {MAX_ITEMS} items")
    items = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, str):
            raise _Refused(f"item {index} must be a str")
        item = _line(_plain(raw))
        if item is None:
            raise _Refused(f"item {index} {ITEM_REASON}")
        if not item:
            raise _Refused(f"item {index} must not be blank")
        items.append(item)
    return _unique(items, [f"item {index}" for index in range(1, len(items) + 1)])


def _write_list(value: tuple[str, ...]) -> str:
    return "\n".join(value)


# availability

_SLOT: Final = re.compile(r"([A-Za-z]{3})[ \t]+([0-9]{2}:[0-9]{2})[ \t]*-[ \t]*([0-9]{2}:[0-9]{2})")
_WEEKDAY_NUMBERS: Final = {name.lower(): number for number, name in enumerate(WEEKDAYS)}
SLOT_REASON: Final = "must look like Mon 09:00-17:00 (Mon to Sun, times from 00:00 to 23:59)"


def _arranged(slots: Iterable[Slot]) -> tuple[Slot, ...]:
    """``slots`` sorted; refused when two on the same day overlap."""
    ordered = tuple(sorted(slots))
    for before, after in zip(ordered, ordered[1:], strict=False):
        if before.weekday == after.weekday and after.start < before.end:
            raise _Refused(f"slots on {WEEKDAYS[after.weekday]} must not overlap")
    return ordered


def _read_availability(text: str) -> tuple[Slot, ...]:
    lines = _lines(text)
    if len(lines) > MAX_ITEMS:
        raise _Refused(f"must have at most {MAX_ITEMS} slots, one per line")
    slots = []
    for number, line in lines:
        match = _SLOT.fullmatch(line)
        weekday = _WEEKDAY_NUMBERS.get(match[1].lower()) if match else None
        start = _hhmm(match[2]) if match else None
        end = _hhmm(match[3]) if match else None
        if weekday is None or start is None or end is None:
            raise _Refused(f"line {number} {SLOT_REASON}")
        if start >= end:
            raise _Refused(f"line {number} must start before it ends")
        slots.append(Slot(weekday, start, end))
    return _arranged(slots)


def _check_availability(value: object) -> tuple[Slot, ...]:
    if not isinstance(value, (tuple, list)):
        raise _Refused("must be a tuple of Slot")
    if len(value) > MAX_ITEMS:
        raise _Refused(f"must have at most {MAX_ITEMS} slots")
    slots = []
    for index, slot in enumerate(value, start=1):
        if not isinstance(slot, Slot):
            raise _Refused(f"slot {index} must be a Slot")
        if not (_is_int(slot.weekday) and 0 <= slot.weekday <= 6):
            raise _Refused(f"slot {index} must have a weekday from 0 (Mon) to 6 (Sun)")
        try:
            start, end = _check_time(slot.start), _check_time(slot.end)
        except _Refused as refused:
            raise _Refused(f"slot {index} {refused.reason}") from None
        if start >= end:
            raise _Refused(f"slot {index} must start before it ends")
        slots.append(Slot(int(slot.weekday), start, end))
    return _arranged(slots)


def _write_availability(value: tuple[Slot, ...]) -> str:
    return "\n".join(
        f"{WEEKDAYS[slot.weekday]} {_write_time(slot.start)}-{_write_time(slot.end)}"
        for slot in value
    )


class _Codec(NamedTuple):
    read: Callable[[str], Any]  # a non-blank text form -> a value; raises _Refused
    check: Callable[[Any], Any]  # a value -> the same value, normalised; raises _Refused
    write: Callable[[Any], str]  # a checked value -> its canonical text


_CODECS: Final[Mapping[SettingType, _Codec]] = MappingProxyType(
    {
        SettingType.STRING: _Codec(_read_string, _check_string, str),
        SettingType.INTEGER: _Codec(_read_integer, _check_integer, str),
        SettingType.BOOLEAN: _Codec(_read_boolean, _check_boolean, _write_boolean),
        SettingType.TIME: _Codec(_read_time, _check_time, _write_time),
        SettingType.TIMEZONE: _Codec(_read_timezone, _check_timezone, str),
        SettingType.DURATION: _Codec(_read_duration, _check_duration, _write_duration),
        SettingType.MONEY: _Codec(_read_money, _check_money, _write_money),
        SettingType.LIST: _Codec(_read_list, _check_list, _write_list),
        SettingType.AVAILABILITY: _Codec(
            _read_availability, _check_availability, _write_availability
        ),
    }
)
# The value whose canonical text is blank, for the types that have one.
_BLANK_VALUES: Final[Mapping[SettingType, object]] = MappingProxyType(
    {SettingType.STRING: "", SettingType.LIST: (), SettingType.AVAILABILITY: ()}
)


def _blank(setting: Setting) -> InvalidSettingValue:
    return InvalidSettingValue(setting.key, REQUIRED_RULE, "must not be blank")


def _validated(setting: Setting, value: object) -> Any:
    """``value`` type-checked and normalised, after the rules; raises ``InvalidSettingValue``."""
    try:
        checked = _CODECS[setting.type].check(value)
    except _Refused as refused:
        raise InvalidSettingValue(setting.key, setting.type, refused.reason) from None
    for rule in setting.rules:
        if not rule.check(checked):
            raise InvalidSettingValue(setting.key, rule.name, rule.message)
    return checked


def parse(setting: Setting, text: str) -> Any:
    """The typed value of ``text``, a text form of ``setting`` (stored or typed in), after the
    type's checks and the rules. Raises ``InvalidSettingValue``; blank text breaks ``required``.
    """
    if not isinstance(text, str):
        raise InvalidSettingValue(setting.key, setting.type, "must be text")
    if not text.strip():
        raise _blank(setting)
    try:
        value = _CODECS[setting.type].read(_plain(text))
    except _Refused as refused:
        raise InvalidSettingValue(setting.key, setting.type, refused.reason) from None
    return _validated(setting, value)


def encode(setting: Setting, value: object) -> str:
    """The canonical text of ``value``: the type is checked, then the rules. Raises
    ``InvalidSettingValue``; a value whose text would be blank breaks ``required``."""
    text = _CODECS[setting.type].write(_validated(setting, value))
    if not text:
        raise _blank(setting)
    return text


def default_text(setting: Setting) -> str | None:
    """The canonical text of ``setting``'s default (blank for a blank default), or ``None``
    when the setting is required."""
    if setting.default is REQUIRED:
        return None
    return _CODECS[setting.type].write(_validated(setting, setting.default))


# Checking a schema ----------------------------------------------------------------------------

_ZONED_TYPES: Final = frozenset({SettingType.TIME, SettingType.AVAILABILITY})


def _default_problems(name: str, found: Setting) -> list[str]:
    if found.default is REQUIRED:
        return []
    try:
        text = _CODECS[found.type].write(_validated(found, found.default))
        again = parse(found, text) if text else _BLANK_VALUES.get(found.type)
    except InvalidSettingValue as invalid:
        return [f"{name}: the default breaks rule {invalid.rule}"]
    except Exception as exc:  # noqa: BLE001 - a rule that can't check the default is the finding
        return [f"{name}: checking the default raised {type(exc).__name__}"]
    if type(again) is not type(found.default) or again != found.default:
        return [f"{name}: the default is not in canonical form"]
    return []


def _timezone_problems(schema: Mapping[str, Setting], name: str, found: Setting) -> list[str]:
    if found.type in _ZONED_TYPES:
        zone = schema.get(found.timezone_key) if isinstance(found.timezone_key, str) else None
        if not (isinstance(zone, Setting) and zone.type is SettingType.TIMEZONE):
            return [
                f"{name}: a {found.type} setting needs a timezone_key naming a timezone setting"
            ]
    elif found.timezone_key is not None:
        return [f"{name}: only a time or availability setting names a timezone_key"]
    return []


def _setting_problems(schema: Mapping[str, Setting], name: str, found: object) -> list[str]:
    if not isinstance(found, Setting):
        return [f"{name}: not a Setting"]
    problems = []
    if not (isinstance(found.key, str) and KEY_PATTERN.fullmatch(found.key)):
        problems.append(
            f"{name}: the key must match [a-z][a-z0-9_]{{0,30}} (at most 31 characters)"
        )
    if found.key != name:
        problems.append(f"{name}: the setting's key is not its mapping key")
    for attribute in ("label", "unit"):
        text = getattr(found, attribute)
        if not (isinstance(text, str) and text.strip()):
            problems.append(f"{name}: the {attribute} is empty")
    if not isinstance(found.section, Section):
        problems.append(f"{name}: the section is not a Section")
    if not all(isinstance(rule, Rule) for rule in found.rules):
        problems.append(f"{name}: a rule is not a Rule")
    if not isinstance(found.type, SettingType):
        return [*problems, f"{name}: the type is not a SettingType"]
    return problems + _default_problems(name, found) + _timezone_problems(schema, name, found)


def schema_problems(
    schema: Mapping[str, Setting], keys: Iterable[str] | None = Key
) -> list[str]:
    """Everything wrong with ``schema``, which maps each key to its ``Setting``: a malformed or
    mismatched key, an empty label or unit, a default that fails validation or isn't in
    canonical form, a ``time`` or ``availability`` setting without a ``timezone_key`` naming a
    declared ``timezone`` setting (or another type with one), and, unless ``keys`` is ``None``,
    keys other than exactly ``keys`` (``Key`` by default). Messages name keys, never values."""
    problems = []
    for name, found in schema.items():
        problems += _setting_problems(schema, name, found)
    if keys is not None:
        expected = {_plain(key) for key in keys}
        if missing := expected - set(schema):
            problems.append(f"Key members not declared: {', '.join(sorted(missing))}")
        if extra := set(schema) - expected:
            problems.append(f"declared but not in Key: {', '.join(sorted(map(str, extra)))}")
    return problems


def check_schema(schema: Mapping[str, Setting], keys: Iterable[str] | None = Key) -> None:
    """Raise ``TypeError`` listing ``schema_problems(schema, keys)``, if there are any. This
    module calls it on ``SCHEMA`` at import."""
    problems = schema_problems(schema, keys)
    if problems:
        raise TypeError("settings schema: " + "; ".join(problems))


def _declare(*settings: Setting) -> Mapping[str, Setting]:
    """The schema mapping of ``settings``, in declaration order; a repeated key raises."""
    schema: dict[str, Setting] = {}
    for declared_setting in settings:
        if declared_setting.key in schema:
            raise TypeError(f"setting {declared_setting.key} is declared twice")
        schema[declared_setting.key] = declared_setting
    return MappingProxyType(schema)


# Access ---------------------------------------------------------------------------------------


def setting(key: str) -> Setting:
    """The declared setting ``key``; raises ``UnknownSetting``."""
    found = SCHEMA.get(key) if isinstance(key, str) else None
    if found is None:
        raise UnknownSetting(key)
    return found


def declared() -> tuple[Setting, ...]:
    """Every declared setting, in declaration order."""
    return tuple(SCHEMA.values())


# The schema -----------------------------------------------------------------------------------

SINK_RECIPIENT_RULE: Final = Rule(
    "sink_recipient",
    "each line must be an email address or a numeric Telegram chat id",
    lambda value: all(is_sink_recipient(item) for item in value),
)

TELEGRAM_CHAT_ID_RULE: Final = Rule(
    "telegram_chat_id",
    "must be a numeric Telegram chat id, such as -1001234567890",
    is_telegram_chat_id,
)

# Budget and limits (Story 1.13, FR-57, NFR-12). Each quota counts the UTC calendar month; at
# the quota, only the work it names stops, never sending, replies or unsubscribe polling. The
# Places maximums are Google's free monthly allowance (March 2025 Maps pricing), so no value
# can start paid Places use.
MAX_QUOTA: Final = 100_000
PLACES_ENTERPRISE_FREE_MONTHLY: Final = 1_000
PLACES_PRO_FREE_MONTHLY: Final = 5_000
MAX_AI_BUDGET: Final = 100 * MICROS_PER_DOLLAR  # the whole engine's monthly ceiling (SM-9)

SCHEMA: Mapping[str, Setting] = _declare(
    Setting(
        key=Key.SENDER_TIMEZONE,
        type=SettingType.TIMEZONE,
        section=Section.SENDER,
        label="Sender's timezone",
        unit="IANA timezone name, such as America/Los_Angeles",
        default=REQUIRED,
    ),
    # Outside prod, the sink (nextron.adapters.sink) delivers mail and alerts only to these
    # recipients (AD-14). Empty by default: nothing is delivered until Mac lists his own.
    Setting(
        key=Key.SINK_ALLOWLIST,
        type=SettingType.LIST,
        section=Section.ENGINE,
        label="Dev and test allowlist",
        unit=(
            "One email address or numeric Telegram chat id per line. Outside prod, email and "
            "alerts reach only these; everything else is written to the sink folder."
        ),
        default=(),
        rules=(SINK_RECIPIENT_RULE,),
    ),
    # The job runner's bulk lane threads (AD-10), read once when the runner starts.
    Setting(
        key=Key.BULK_THREADS,
        type=SettingType.INTEGER,
        section=Section.ENGINE,
        label="Bulk worker threads",
        unit=(
            "1 or 2. Threads for crawls, enrichment and other bulk work; a change takes effect "
            "when the engine restarts."
        ),
        default=1,
        rules=(minimum(1, "1"), maximum(MAX_BULK_THREADS, str(MAX_BULK_THREADS))),
    ),
    # The Telegram chat that receives ops alerts (AD-15, Story 1.12), read when each alert is
    # sent. Numeric only: a @name can be handed to someone else.
    Setting(
        key=Key.ALERT_CHAT_ID,
        type=SettingType.STRING,
        section=Section.ALERTS_AND_RECIPIENTS,
        label="Telegram alert chat id",
        unit=(
            "The numeric id of the Telegram chat that receives ops alerts, such as "
            "-1001234567890; user names (@name) aren't accepted."
        ),
        default=REQUIRED,
        rules=(TELEGRAM_CHAT_ID_RULE,),
    ),
    Setting(
        key=Key.QUOTA_HUNTER_CREDITS,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label="Hunter credits a month",
        unit=(
            "0 to 100,000 Hunter credits each UTC calendar month (a find or a Domain Search "
            "uses 1, a verification half of one). At the quota, enrichment stops until the "
            f"next month; 0 stops it altogether. {NEVER_BLOCKED}"
        ),
        default=50,
        rules=(minimum(0, "0"), maximum(MAX_QUOTA, "100,000")),
    ),
    Setting(
        key=Key.QUOTA_PROSPEO_CREDITS,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label="Prospeo credits a month",
        unit=(
            "0 to 100,000 Prospeo credits each UTC calendar month. At the quota, enrichment "
            f"stops until the next month; 0 stops it altogether. {NEVER_BLOCKED}"
        ),
        default=100,
        rules=(minimum(0, "0"), maximum(MAX_QUOTA, "100,000")),
    ),
    Setting(
        key=Key.QUOTA_PLACES_ENTERPRISE,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label="Places Enterprise requests a month",
        unit=(
            "0 to 1,000 Google Places Enterprise requests each UTC calendar month. The maximum "
            "is Google's free monthly allowance, so paid use never starts. At the quota, "
            f"discovery stops until the next month; 0 stops it altogether. {NEVER_BLOCKED}"
        ),
        default=1_000,
        rules=(minimum(0, "0"), maximum(PLACES_ENTERPRISE_FREE_MONTHLY, "1,000")),
    ),
    Setting(
        key=Key.QUOTA_PLACES_PRO,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label="Places Pro requests a month",
        unit=(
            "0 to 5,000 Google Places Pro requests each UTC calendar month. The maximum is "
            "Google's free monthly allowance, so paid use never starts. At the quota, "
            f"discovery stops until the next month; 0 (the default) stops it. {NEVER_BLOCKED}"
        ),
        default=0,
        rules=(minimum(0, "0"), maximum(PLACES_PRO_FREE_MONTHLY, "5,000")),
    ),
    Setting(
        key=Key.QUOTA_JOB_AGGREGATOR,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label="Job-aggregator requests a month",
        unit=(
            "0 to 100,000 job-aggregator requests each UTC calendar month. At the quota, "
            f"discovery stops until the next month; 0 (the default) stops it. {NEVER_BLOCKED}"
        ),
        default=0,
        rules=(minimum(0, "0"), maximum(MAX_QUOTA, "100,000")),
    ),
    Setting(
        key=Key.AI_BUDGET_MONTHLY,
        type=SettingType.MONEY,
        section=Section.BUDGET_AND_LIMITS,
        label="AI budget a month",
        unit=(
            "US dollars, $0 to $100, of AI spend each UTC calendar month. At the budget, "
            "discovery, enrichment and re-checks stop until the next month; $0 stops them "
            f"altogether. {NEVER_BLOCKED}"
        ),
        default=20 * MICROS_PER_DOLLAR,
        rules=(minimum(0, "$0"), maximum(MAX_AI_BUDGET, "$100")),
    ),
    Setting(
        key=Key.AI_BUDGET_ALERT_PCT,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label="AI spend alert at (%)",
        unit=(
            "1 to 100: the percentage of the monthly AI budget at which the dashboard warns, "
            "once each UTC calendar month. Nothing stops at the warning."
        ),
        default=80,
        rules=(minimum(1, "1"), maximum(MAX_PERCENT, "100")),
    ),
)

check_schema(SCHEMA)
