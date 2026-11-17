"""The SuppressionEntry machine (AD-4, AD-16, FR-50, FR-63, Story 3.3). Standard library only,
plus ``nextron.domain``.

**Statuses** (``EntryStatus``): ``active`` and ``voided``. ``TERMINAL_STATUSES`` is ``voided``.
``compliance`` owns ``suppression_entries.status`` and appends one ``events`` row (entity
``suppression_entries``) per entry it writes or voids. An entry never expires and is never
deleted.

**Sources** (``SuppressionSource``), the reasons an entry exists: ``reply_rule``,
``reply_class``, ``one_click``, ``mailto``, ``operator``, ``import`` and ``dsr``.
``OPT_OUT_SOURCES`` (``reply_rule``, ``reply_class``, ``one_click``, ``mailto``) are the
person's own opt-outs: they close the Lead ``lost`` (``pipeline.close_suppressed``). The others
leave the Pipeline Stage as it is. ``VOIDABLE_SOURCES`` is ``reply_rule``: only an entry a reply
rule wrote can be voided, by the Operator, with a reason (Story 3.5).

**Creation.** ``CREATION[source]`` is ``Transition(active, <source>)``: every entry starts
``active``, and its creation ``events`` row's cause is its source.

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

=====  ======  ======  =================
Event  From    To      Cause
=====  ======  ======  =================
void   active  voided  operator_reversal
=====  ======  ======  =================

Nothing leaves ``voided``. Story 3.5 adds the command that voids an entry (``reverse_optout``).
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "suppression_entry"


class EntryStatus(StrEnum):
    ACTIVE = "active"
    VOIDED = "voided"


class SuppressionSource(StrEnum):
    REPLY_RULE = "reply_rule"
    REPLY_CLASS = "reply_class"
    ONE_CLICK = "one_click"
    MAILTO = "mailto"
    OPERATOR = "operator"
    IMPORT = "import"
    DSR = "dsr"


class EntryEvent(StrEnum):
    VOID = "void"


class EntryCause(StrEnum):
    REPLY_RULE = "reply_rule"
    REPLY_CLASS = "reply_class"
    ONE_CLICK = "one_click"
    MAILTO = "mailto"
    OPERATOR = "operator"
    IMPORT = "import"
    DSR = "dsr"
    OPERATOR_REVERSAL = "operator_reversal"


TERMINAL_STATUSES: Final = (EntryStatus.VOIDED,)
OPT_OUT_SOURCES: Final = (
    SuppressionSource.REPLY_RULE,
    SuppressionSource.REPLY_CLASS,
    SuppressionSource.ONE_CLICK,
    SuppressionSource.MAILTO,
)
VOIDABLE_SOURCES: Final = (SuppressionSource.REPLY_RULE,)

CREATION: Final[Mapping[SuppressionSource, Transition[EntryStatus, EntryCause]]] = (
    MappingProxyType(
        {source: Transition(EntryStatus.ACTIVE, EntryCause(source)) for source in SuppressionSource}
    )
)

TRANSITIONS: Final[Mapping[tuple[EntryStatus, EntryEvent], Transition[EntryStatus, EntryCause]]] = (
    MappingProxyType(
        {
            (EntryStatus.ACTIVE, EntryEvent.VOID): Transition(
                EntryStatus.VOIDED, EntryCause.OPERATOR_REVERSAL
            ),
        }
    )
)


def transition(current: EntryStatus, event: EntryEvent) -> Transition[EntryStatus, EntryCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found
