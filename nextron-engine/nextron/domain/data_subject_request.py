"""The DataSubjectRequest machine (AD-4, AD-17, FR-52, Story 3.13). Standard library only, plus
``nextron.domain``.

A data subject request is a person's request to erase (``erase``) or export (``export``,
Story 3.15) what Nextron holds on them. ``compliance`` owns ``data_subject_requests.status`` and
appends one ``events`` row (entity ``data_subject_requests``) per request it opens or completes.
A request is never deleted: after an erasure it is the proof that the erasure ran.

- ``RequestKind``: ``erase`` and ``export``.
- ``RequestStatus``: ``open`` and ``completed``; ``TERMINAL_STATUSES`` is ``completed``.
- ``CREATION`` is ``Transition(open, opened)``: every request starts ``open``.
- ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
  ``IllegalTransition``:

  ========  ====  =========  =========
  Event     From  To         Cause
  ========  ====  =========  =========
  complete  open  completed  completed
  ========  ====  =========  =========

  Nothing leaves ``completed``.

**The request reference** (``check_request_ref(ref, *, name)``) is the Operator's ticket or
letter reference. The request is kept and shown after the erasure, so the reference must not
identify the person: it raises ``ValueError`` unless ``ref`` is a ``str`` of 1 to
``MAX_REF_LENGTH`` (200) characters, equal to its own strip, with no control character and no
``@`` (an address). When ``name`` (the Contact's name, or ``None``) is set and has a word, it
also raises when every word of ``normalize_name(name)`` is a whole word of
``normalize_name(ref)``. A ``name`` that isn't a ``str`` or ``None`` raises ``TypeError``. No
message repeats a value.
"""

import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.identity import IdentityError, normalize_name
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "data_subject_request"
MAX_REF_LENGTH: Final = 200
ADDRESS_MARK: Final = "@"


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


def _words(text: str) -> frozenset[str]:
    """The ``normalize_name`` words of ``text``; none when it has no letter or digit."""
    try:
        return frozenset(normalize_name(text).split(" "))
    except IdentityError:
        return frozenset()


def check_request_ref(ref: object, *, name: str | None) -> None:
    """Refuse a request reference that is malformed or identifies the person; see the module
    docstring."""
    if name is not None and not isinstance(name, str):
        raise TypeError(f"name must be a str or None, not {type(name).__name__}")
    if (
        not isinstance(ref, str)
        or not 1 <= len(ref) <= MAX_REF_LENGTH
        or ref != ref.strip()
        or any(unicodedata.category(character) == "Cc" for character in ref)
    ):
        raise ValueError(
            f"The request reference must be 1 to {MAX_REF_LENGTH} characters on one line, "
            "with no space at either end."
        )
    if ADDRESS_MARK in ref:
        raise ValueError("The request reference must not hold an email address (no @).")
    named = _words(name) if name is not None else frozenset()
    if named and named <= _words(ref):
        raise ValueError("The request reference must not hold the person's name.")
