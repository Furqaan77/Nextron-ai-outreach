"""The data subject request machine (FR-52, AD-4, AD-17, Stories 3.13 and 3.15). Standard library
only, plus ``nextron.domain``.

A data subject request is a person's ask to export (Story 3.15) or erase (Story 3.13) everything
the engine holds on them. ``compliance`` owns ``data_subject_requests`` and appends one ``events``
row (entity ``data_subject_requests``) per request it opens or completes.

**Kinds** (``RequestKind``): ``erase`` and ``export``. **Statuses** (``RequestStatus``): ``open``
and ``completed``; ``TERMINAL_STATUSES`` is ``completed``.

**Creation.** ``CREATION`` is ``Transition(open, opened)``: every request starts ``open``, and its
creation ``events`` row's cause is ``opened``.

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

========  ====  =========  =========
Event     From  To         Cause
========  ====  =========  =========
complete  open  completed  completed
========  ====  =========  =========

Nothing leaves ``completed``.

**The request reference.** ``checked_request_ref(text)`` is the reference the Operator types (how
the person asked: "Email of 18 Nov 2026", a ticket number), stripped: 1 to
``MAX_REQUEST_REF_LENGTH`` (200) characters with no control, format, line separator or
paragraph separator character (Unicode categories ``Cc``, ``Cf``, ``Zl`` and ``Zp``, so no line
break of any kind and no invisible character). A text that isn't a
``str`` raises ``TypeError``; otherwise ``RequestRefused`` (``request_refused``), whose
``message`` is ``REF_REQUIRED`` ("Enter the request reference") when it is blank and
``REF_INVALID`` ("A request reference is at most 200 characters, with no control characters")
otherwise. Neither message repeats the text.
"""

import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "subject_request"
MAX_REQUEST_REF_LENGTH: Final = 200
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
REF_REQUIRED: Final = "Enter the request reference"
REF_INVALID: Final = "A request reference is at most 200 characters, with no control characters"


class RequestKind(StrEnum):
    ERASE = "erase"
    EXPORT = "export"


class RequestStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"


class RequestEvent(StrEnum):
    COMPLETE = "complete"


class RequestCause(StrEnum):
    OPENED = "opened"
    COMPLETED = "completed"


TERMINAL_STATUSES: Final = (RequestStatus.COMPLETED,)

CREATION: Final[Transition[RequestStatus, RequestCause]] = Transition(
    RequestStatus.OPEN, RequestCause.OPENED
)

TRANSITIONS: Final[
    Mapping[tuple[RequestStatus, RequestEvent], Transition[RequestStatus, RequestCause]]
] = MappingProxyType(
    {
        (RequestStatus.OPEN, RequestEvent.COMPLETE): Transition(
            RequestStatus.COMPLETED, RequestCause.COMPLETED
        ),
    }
)


class RequestRefused(DomainError):
    """A request reference the Operator typed that can't be stored; ``message`` says why and
    never repeats it."""

    code = "request_refused"


def transition(
    current: RequestStatus, event: RequestEvent
) -> Transition[RequestStatus, RequestCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


def checked_request_ref(text: str) -> str:
    """The request reference, stripped; see the module docstring."""
    if not isinstance(text, str):
        raise TypeError(f"a request reference is a str, not {type(text).__name__}")
    reference = text.strip()
    if not reference:
        raise RequestRefused(REF_REQUIRED)
    if len(reference) > MAX_REQUEST_REF_LENGTH or any(
        unicodedata.category(character) in REFUSED_CATEGORIES for character in reference
    ):
        raise RequestRefused(REF_INVALID)
    return reference
