"""The LinkedIn account's fields, its caps and their headroom (FR-49, PRD OQ-5, Story 10.2).
Standard library only, plus ``nextron.domain``. A pure helper: it fetches, posts and sends
nothing (``tests/contracts/test_no_linkedin_adapter.py`` allowlists this module by name, and
every name it defines avoids the platform's word).

**The account.** ``pipeline`` keeps one row per account a person sends LinkedIn Tasks from by
hand (``linkedin_accounts``): a ``label``, the owner's ``owner_name`` and ``owner_role`` (the
voice Drafts are written in) and a ``status`` (``AccountStatus``: ``active`` or ``inactive``; a
new account is ``inactive``). v1 supports one ``active`` account (``ONE_ACCOUNT``).

- ``check_text(value)``: the problem with a text field, or ``None``: it must be a ``str``,
  non-blank after stripping, at most ``MAX_TEXT_LENGTH`` (100) characters, with no ``Cc``,
  ``Cf``, ``Cs``, ``Zl`` or ``Zp`` character (one line) once stripped.

**The caps** (``AccountCaps``), stored per account, each a whole number:

- ``tasks_per_weekday`` (default 10): Tasks prepared on one weekday, whatever their status;
- ``daily_invite_cap`` (default 10): invitations scheduled on one local day;
- ``weekly_invite_cap`` (default 100): invitations scheduled in a rolling 7×24-hour window, at most
  ``MAX_WEEKLY_INVITES`` (100).

``cap_problems(caps)`` maps each refused cap's field to its message: a negative cap
``NEGATIVE_CAP``, a weekly cap over 100 ``WEEKLY_CAP_TOO_HIGH`` ("At most 100 invitations per
account in a rolling week"), and a daily cap over the weekly one ``DAILY_OVER_WEEKLY`` (on
``daily_invite_cap``). The database's CHECKs repeat these rules. ``parse_cap(name, text)`` reads
a typed cap (blank is its default; otherwise an optional minus and 1 to 9 digits, else
``ValueError`` with ``NOT_WHOLE_NUMBER``), and ``check_fields(label, owner_name, owner_role,
caps)`` checks a whole form, returning ``AccountFields`` (stripped texts and ``AccountCaps``) or
raising ``AccountRefused`` with every problem.

**Headroom.** ``remaining(account, tasks, now, tz)`` is how many more invitations may be
scheduled at ``now``: the smallest of the three headrooms, never below 0. ``tasks`` are the
account's Tasks as ``CapTask(status, prepared_at)``; an invitation is scheduled at its Task's
``prepared_at`` when the status is in ``SCHEDULED_STATUSES`` (``pending``, ``sent``,
``accepted``).

- the weekly headroom counts scheduled Tasks prepared after ``now - 7×24 h`` (the window is
  ``(now - 7 d, now]``, and a Task dated after ``now`` counts too: the stricter reading);
- the daily headroom counts scheduled Tasks whose ``prepared_at`` falls on ``now``'s local date
  in ``tz``;
- the Task headroom counts every Task, whatever its status, prepared on that local date.

``now`` and every ``prepared_at`` must be aware ``datetime``s (``ValueError`` otherwise), and
``tz`` a ``tzinfo`` (``TypeError`` otherwise).

**Messages.** ``ONE_ACCOUNT`` refuses a second ``active`` account; ``TASKS_OFF`` is the health
issue's text while no account is ``active`` or the ``linkedin_owner_chat`` setting is blank.
``AccountRefused`` (code ``account_refused``) carries ``.problems``, each refused field (or
``form``) to its message; its own message names the fields, never a value.
"""

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Final

from nextron.domain.errors import DomainError

MAX_TEXT_LENGTH: Final = 100
DEFAULT_TASKS_PER_WEEKDAY: Final = 10
DEFAULT_DAILY_INVITES: Final = 10
DEFAULT_WEEKLY_INVITES: Final = 100
MAX_WEEKLY_INVITES: Final = 100
WEEK: Final = timedelta(days=7)
SCHEDULED_STATUSES: Final = frozenset({"pending", "sent", "accepted"})
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

# Field names, as the form and the table use them.
FORM_FIELD: Final = "form"
LABEL: Final = "label"
OWNER_NAME: Final = "owner_name"
OWNER_ROLE: Final = "owner_role"
STATUS: Final = "status"
TASKS_PER_WEEKDAY: Final = "tasks_per_weekday"
DAILY_INVITES: Final = "daily_invite_cap"
WEEKLY_INVITES: Final = "weekly_invite_cap"
TEXT_FIELDS: Final = (LABEL, OWNER_NAME, OWNER_ROLE)
CAP_FIELDS: Final = (TASKS_PER_WEEKDAY, DAILY_INVITES, WEEKLY_INVITES)

# Messages.
WEEKLY_CAP_TOO_HIGH: Final = "At most 100 invitations per account in a rolling week"
DAILY_OVER_WEEKLY: Final = "The daily invitation cap can't be more than the weekly cap"
NEGATIVE_CAP: Final = "Must be 0 or more"
NOT_WHOLE_NUMBER: Final = "Must be a whole number, such as 10"
BLANK_TEXT: Final = "Required"
LONG_TEXT: Final = f"At most {MAX_TEXT_LENGTH} characters"
BAD_TEXT: Final = "Must be one line of plain text"
DUPLICATE_LABEL: Final = "Another account already has this label"
UNKNOWN_ACCOUNT: Final = "This account no longer exists"
BAD_STATUS: Final = "Must be active or inactive"
ONE_ACCOUNT: Final = (
    "v1 supports one LinkedIn account; its Tasks go to linkedin_owner_chat (PRD OQ-5)"
)
TASKS_OFF: Final = "LinkedIn Tasks off: set up the LinkedIn account and linkedin_owner_chat"


class AccountStatus(StrEnum):
    """An account's status: Tasks are prepared only for the ``active`` one."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class AccountCaps:
    """An account's three caps; see the module docstring."""

    tasks_per_weekday: int = DEFAULT_TASKS_PER_WEEKDAY
    daily_invite_cap: int = DEFAULT_DAILY_INVITES
    weekly_invite_cap: int = DEFAULT_WEEKLY_INVITES


DEFAULT_CAPS: Final = AccountCaps()


@dataclass(frozen=True, slots=True)
class CapTask:
    """What the caps read of one Task: its status and when it was prepared."""

    status: str
    prepared_at: datetime


class AccountRefused(DomainError):
    """An Operator's account save refused: ``problems`` maps each refused field (or ``form``)
    to its message. The message names the fields, never a value."""

    code = "account_refused"

    def __init__(self, problems: Mapping[str, str]) -> None:
        self.problems: dict[str, str] = dict(problems)
        super().__init__(f"account refused: {', '.join(self.problems) or 'no field'}")


def check_text(value: object) -> str | None:
    """The problem with a label, owner name or owner role, or ``None`` when it is fine."""
    stripped = value.strip() if isinstance(value, str) else ""
    if not stripped:
        return BLANK_TEXT
    if len(stripped) > MAX_TEXT_LENGTH:
        return LONG_TEXT
    if any(unicodedata.category(char) in REFUSED_CATEGORIES for char in stripped):
        return BAD_TEXT
    return None


def cap_problems(caps: AccountCaps) -> dict[str, str]:
    """Each refused cap's field mapped to its message; empty when the caps are fine."""
    problems: dict[str, str] = {}
    values = {
        TASKS_PER_WEEKDAY: caps.tasks_per_weekday,
        DAILY_INVITES: caps.daily_invite_cap,
        WEEKLY_INVITES: caps.weekly_invite_cap,
    }
    for name, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool):
            problems[name] = NOT_WHOLE_NUMBER
        elif value < 0:
            problems[name] = NEGATIVE_CAP
    if WEEKLY_INVITES not in problems and caps.weekly_invite_cap > MAX_WEEKLY_INVITES:
        problems[WEEKLY_INVITES] = WEEKLY_CAP_TOO_HIGH
    if (
        DAILY_INVITES not in problems
        and WEEKLY_INVITES not in problems
        and caps.daily_invite_cap > caps.weekly_invite_cap
    ):
        problems[DAILY_INVITES] = DAILY_OVER_WEEKLY
    return problems


@dataclass(frozen=True, slots=True)
class AccountFields:
    """An account's checked fields: the stripped texts and the caps."""

    label: str
    owner_name: str
    owner_role: str
    caps: AccountCaps


CAP_DEFAULTS: Final[Mapping[str, int]] = {
    TASKS_PER_WEEKDAY: DEFAULT_TASKS_PER_WEEKDAY,
    DAILY_INVITES: DEFAULT_DAILY_INVITES,
    WEEKLY_INVITES: DEFAULT_WEEKLY_INVITES,
}
MAX_CAP_DIGITS: Final = 9


def parse_cap(name: str, text: object) -> int:
    """The cap ``name`` typed as ``text``: blank is its default, otherwise an optional minus
    and 1 to 9 digits; ``ValueError`` (``NOT_WHOLE_NUMBER``) for anything else."""
    if not isinstance(text, str):
        raise ValueError(NOT_WHOLE_NUMBER)
    stripped = text.strip()
    if not stripped:
        return CAP_DEFAULTS[name]
    digits = stripped.removeprefix("-")
    if not (digits.isascii() and digits.isdigit() and len(digits) <= MAX_CAP_DIGITS):
        raise ValueError(NOT_WHOLE_NUMBER)
    return int(stripped)


def check_fields(
    label: object, owner_name: object, owner_role: object, caps: Mapping[str, object]
) -> AccountFields:
    """The account's fields checked: each text by ``check_text``, each cap in ``CAP_FIELDS``
    by ``parse_cap`` (a missing one is blank), then the caps by ``cap_problems``. Raises
    ``AccountRefused`` with every problem found."""
    problems: dict[str, str] = {}
    texts = {LABEL: label, OWNER_NAME: owner_name, OWNER_ROLE: owner_role}
    for name, value in texts.items():
        problem = check_text(value)
        if problem is not None:
            problems[name] = problem
    numbers: dict[str, int] = {}
    for name in CAP_FIELDS:
        try:
            numbers[name] = parse_cap(name, caps.get(name, ""))
        except ValueError as refused:
            problems[name] = str(refused)
    if len(numbers) == len(CAP_FIELDS):
        problems.update(cap_problems(AccountCaps(**numbers)))
    if problems:
        raise AccountRefused(problems)
    return AccountFields(
        label=str(label).strip(),
        owner_name=str(owner_name).strip(),
        owner_role=str(owner_role).strip(),
        caps=AccountCaps(**numbers),
    )


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


def remaining(account: AccountCaps, tasks: Iterable[CapTask], now: datetime, tz: tzinfo) -> int:
    """How many more invitations may be scheduled at ``now``; see the module docstring.
    No Tasks are prepared on Saturday or Sunday in sender_timezone; that rule is Story 10.4's,
    not ``remaining()``'s."""
    _aware("now", now)
    if not isinstance(tz, tzinfo):
        raise TypeError("tz must be a tzinfo")
    today = now.astimezone(tz).date()
    since = now - WEEK
    week = day = prepared_today = 0
    for task in tasks:
        at = _aware("prepared_at", task.prepared_at)
        on_today = at.astimezone(tz).date() == today
        prepared_today += on_today
        if task.status in SCHEDULED_STATUSES:
            week += at > since
            day += on_today
    headroom = min(
        account.tasks_per_weekday - prepared_today,
        account.daily_invite_cap - day,
        account.weekly_invite_cap - week,
    )
    return max(headroom, 0)
