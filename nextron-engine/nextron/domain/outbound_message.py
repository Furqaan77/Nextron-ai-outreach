"""The OutboundMessage machine (AD-4, AD-5, AD-6; Story 5.2). Standard library only, plus
``nextron.domain``.

An OutboundMessage (OM) is one approved message waiting for, or past, delivery: ``sending``
owns it, inserts it at ``queued`` (``sending.enqueue``, from an approval) and moves its status.
Every status change appends one ``events`` row (entity ``outbound_messages``, actor
``system:sending`` or ``operator``).

**Statuses** (``OmStatus``): ``queued``, ``sending``, ``sent``, ``unknown`` and ``cancelled``.
``TERMINAL`` is ``sent`` and ``cancelled``; ``LIVE`` (the statuses the partial unique index
``uq_outbound_messages_live_draft`` counts) is every status but ``cancelled``.

**Kinds** (``OmKind``): ``step``, ``recontact``, ``reply`` and ``rebook``, the Draft kinds that
become email (a ``channel`` Draft never does).

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

=================  ================  ==========  ==================
Event              From              To          Cause
=================  ================  ==========  ==================
claim              queued            sending     gate_passed
confirm_sent       sending, unknown  sent        sent
mark_unknown       sending           unknown     not_found
restore            queued, sending   unknown     restored
requeue            unknown           queued      operator_requeued
operator_cancel    unknown           cancelled   operator_cancelled
cancel             queued            cancelled   unapproved
=================  ================  ==========  ==================

**Creation.** ``CREATION`` is ``Transition(queued, enqueued)``: the creation ``events`` row has
no ``from`` status.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "outbound_message"


class OmStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class OmKind(StrEnum):
    STEP = "step"
    RECONTACT = "recontact"
    REPLY = "reply"
    REBOOK = "rebook"


class OmEvent(StrEnum):
    CLAIM = "claim"
    CONFIRM_SENT = "confirm_sent"
    MARK_UNKNOWN = "mark_unknown"
    RESTORE = "restore"
    REQUEUE = "requeue"
    OPERATOR_CANCEL = "operator_cancel"
    CANCEL = "cancel"


class OmCause(StrEnum):
    ENQUEUED = "enqueued"
    GATE_PASSED = "gate_passed"
    SENT = "sent"
    NOT_FOUND = "not_found"
    RESTORED = "restored"
    OPERATOR_REQUEUED = "operator_requeued"
    OPERATOR_CANCELLED = "operator_cancelled"
    UNAPPROVED = "unapproved"


CREATION: Final = Transition(OmStatus.QUEUED, OmCause.ENQUEUED)
TERMINAL: Final = (OmStatus.SENT, OmStatus.CANCELLED)
LIVE: Final = (OmStatus.QUEUED, OmStatus.SENDING, OmStatus.SENT, OmStatus.UNKNOWN)

_S = OmStatus
_E = OmEvent
_C = OmCause


def _rows(
    sources: Iterable[OmStatus], event: OmEvent, to: OmStatus, cause: OmCause
) -> dict[tuple[OmStatus, OmEvent], Transition[OmStatus, OmCause]]:
    return {(source, event): Transition(to, cause) for source in sources}


TRANSITIONS: Final[Mapping[tuple[OmStatus, OmEvent], Transition[OmStatus, OmCause]]] = (
    MappingProxyType(
        {
            **_rows((_S.QUEUED,), _E.CLAIM, _S.SENDING, _C.GATE_PASSED),
            **_rows((_S.SENDING, _S.UNKNOWN), _E.CONFIRM_SENT, _S.SENT, _C.SENT),
            **_rows((_S.SENDING,), _E.MARK_UNKNOWN, _S.UNKNOWN, _C.NOT_FOUND),
            **_rows((_S.QUEUED, _S.SENDING), _E.RESTORE, _S.UNKNOWN, _C.RESTORED),
            **_rows((_S.UNKNOWN,), _E.REQUEUE, _S.QUEUED, _C.OPERATOR_REQUEUED),
            **_rows((_S.UNKNOWN,), _E.OPERATOR_CANCEL, _S.CANCELLED, _C.OPERATOR_CANCELLED),
            **_rows((_S.QUEUED,), _E.CANCEL, _S.CANCELLED, _C.UNAPPROVED),
        }
    )
)


def transition(current: OmStatus, event: OmEvent) -> Transition[OmStatus, OmCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found
