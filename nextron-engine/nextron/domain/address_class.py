"""The Address Class machine (AD-4, FR-15). Standard library only.

**Classes.** A Contact's email address is ``published`` (found on the organization's own
website), ``guessed`` (from a CSV, a pattern or a finder, and not yet checked), ``verified`` (a
verification said it is valid) or ``invalid``. ``TERMINAL`` is ``invalid``: nothing leaves it.
``research`` owns ``contacts.address_class`` and appends one ``events`` row (entity
``contacts``, actor ``system:research``) per assignment or change.

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

============  ===================================  =========  ===================
Event         From                                 To         Cause
============  ===================================  =========  ===================
verify        guessed                              verified   verification_valid
find_on_site  guessed                              published  found_on_site
fail_check    published, guessed, verified         invalid    check_failed
hard_bounce   published, guessed, verified         invalid    hard_bounce
============  ===================================  =========  ===================

**Assignment.** An address's first class depends on its source: ``assign(source)`` is
``ASSIGNMENT[source]``, ``Transition(guessed, assigned_from_<source>)`` for ``csv``,
``pattern`` and ``finder``, and ``Transition(published, assigned_from_website)`` for
``website``. Any other source raises ``IllegalTransition(MACHINE, "assign", source)``. Its
``events`` row has no ``from`` status.

Story 2.3 assigns classes; later stories (2.13, 2.14, 6.x) add the commands that verify, find
and invalidate addresses. ``AddressCause`` holds exactly the eight causes above.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.contacts import ProvenanceSource
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "address_class"


class AddressClass(StrEnum):
    PUBLISHED = "published"
    GUESSED = "guessed"
    VERIFIED = "verified"
    INVALID = "invalid"


TERMINAL: Final = (AddressClass.INVALID,)


class AddressEvent(StrEnum):
    VERIFY = "verify"
    FIND_ON_SITE = "find_on_site"
    FAIL_CHECK = "fail_check"
    HARD_BOUNCE = "hard_bounce"


class AddressCause(StrEnum):
    VERIFICATION_VALID = "verification_valid"
    FOUND_ON_SITE = "found_on_site"
    CHECK_FAILED = "check_failed"
    HARD_BOUNCE = "hard_bounce"
    ASSIGNED_FROM_CSV = "assigned_from_csv"
    ASSIGNED_FROM_PATTERN = "assigned_from_pattern"
    ASSIGNED_FROM_FINDER = "assigned_from_finder"
    ASSIGNED_FROM_WEBSITE = "assigned_from_website"


_C = AddressClass
_LIVE: Final = (_C.PUBLISHED, _C.GUESSED, _C.VERIFIED)

TRANSITIONS: Final[
    Mapping[tuple[AddressClass, AddressEvent], Transition[AddressClass, AddressCause]]
] = MappingProxyType(
    {
        (_C.GUESSED, AddressEvent.VERIFY): Transition(
            _C.VERIFIED, AddressCause.VERIFICATION_VALID
        ),
        (_C.GUESSED, AddressEvent.FIND_ON_SITE): Transition(
            _C.PUBLISHED, AddressCause.FOUND_ON_SITE
        ),
        **{
            (live, AddressEvent.FAIL_CHECK): Transition(_C.INVALID, AddressCause.CHECK_FAILED)
            for live in _LIVE
        },
        **{
            (live, AddressEvent.HARD_BOUNCE): Transition(_C.INVALID, AddressCause.HARD_BOUNCE)
            for live in _LIVE
        },
    }
)

ASSIGNMENT: Final[Mapping[ProvenanceSource, Transition[AddressClass, AddressCause]]] = (
    MappingProxyType(
        {
            ProvenanceSource.CSV: Transition(_C.GUESSED, AddressCause.ASSIGNED_FROM_CSV),
            ProvenanceSource.PATTERN: Transition(_C.GUESSED, AddressCause.ASSIGNED_FROM_PATTERN),
            ProvenanceSource.FINDER: Transition(_C.GUESSED, AddressCause.ASSIGNED_FROM_FINDER),
            ProvenanceSource.WEBSITE: Transition(
                _C.PUBLISHED, AddressCause.ASSIGNED_FROM_WEBSITE
            ),
        }
    )
)


def transition(
    current: AddressClass, event: AddressEvent
) -> Transition[AddressClass, AddressCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no class or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


def assign(source: ProvenanceSource) -> Transition[AddressClass, AddressCause]:
    """The first class of an address from ``source``; raises ``IllegalTransition`` for a source
    that never yields an address."""
    try:
        found = ASSIGNMENT.get(source)
    except TypeError:  # an unhashable argument is no source
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, "assign", source)
    return found
