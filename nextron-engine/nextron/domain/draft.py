"""The Draft machine (AD-4, AD-6, AD-21; Story 4.9). Standard library only, plus
``nextron.domain``.

A Draft is one message slot: ``drafting`` inserts it at ``ready`` and owns its content group
(``kind``, the Enrollment, ``step_no``, ``day_offset``, ``current_rev``, ``due_at``, ``generic``,
``stale``); ``approval`` owns its status group (``status``, ``reapproval_required``,
``event_expired``, ``skipped_at``, ``status_at``) and moves the status. Every status change
appends one ``events`` row (entity ``drafts``).

**Statuses** (``DraftStatus``): ``ready``, ``approved``, ``sent``, ``rejected``, ``superseded``
and ``cancelled``. ``PENDING`` (not yet sent or ended) is ``ready`` and ``approved``;
``TERMINAL`` is the other four.

**Kinds** (``DraftKind``): ``step`` (a Sequence Step), ``recontact`` (a re-contact's Step),
``reply``, ``rebook`` and ``channel`` (a LinkedIn or other channel message). ``STEP_KINDS`` are
``step`` and ``recontact``: the kinds that belong to an Enrollment and carry a Step number.
``RESPONSIVE_KINDS`` (Story 5.1) are ``reply``, ``rebook`` and ``channel``: the kinds the
Approval Queue shows whatever their due time, in its Replies group.

**Transitions.** ``transition(current, event, *, kind=None)`` follows ``TRANSITIONS``
(read-only) or raises ``IllegalTransition``:

======================  ==================  ==========  ================
Event                   From                To          Cause
======================  ==================  ==========  ================
approve                 ready               approved    approved
return_edited           approved            ready       edited
return_expired          approved            ready       expired
return_event_expired    approved            ready       event_expired
reject                  ready, approved     rejected    rejected
send                    approved            sent        sent
supersede               ready, approved     superseded  superseded
cancel                  ready, approved     cancelled   sequence_stopped
log_touch               ready               sent        touch_logged
======================  ==================  ==========  ================

``log_touch`` is allowed only for kind ``channel`` (``kind`` must be given and be ``channel``;
otherwise ``IllegalTransition``). **Creation.** ``CREATION`` is ``Transition(ready,
requested)``: the creation ``events`` row has no ``from`` status.

**Revision origins** (``RevisionOrigin``, Story 4.12): why a Draft revision was written:
``generated`` (revision 1, the AI's original text), ``regenerated``, ``operator_edit`` and
``footer_change``.

**Purpose (Story 5.4).** ``contact_purpose(kind, step_no)``: AD-16's purpose for emailing a
Draft: ``cold`` (Step 1), ``follow_up`` (a later Step), ``recontact``, ``reply``, ``rebook``;
``None`` for ``channel``.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.contactability import Purpose
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "draft"


class DraftStatus(StrEnum):
    READY = "ready"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class DraftKind(StrEnum):
    STEP = "step"
    RECONTACT = "recontact"
    REPLY = "reply"
    REBOOK = "rebook"
    CHANNEL = "channel"


class RevisionOrigin(StrEnum):
    GENERATED = "generated"
    REGENERATED = "regenerated"
    OPERATOR_EDIT = "operator_edit"
    FOOTER_CHANGE = "footer_change"


class DraftEvent(StrEnum):
    APPROVE = "approve"
    RETURN_EDITED = "return_edited"
    RETURN_EXPIRED = "return_expired"
    RETURN_EVENT_EXPIRED = "return_event_expired"
    REJECT = "reject"
    SEND = "send"
    SUPERSEDE = "supersede"
    CANCEL = "cancel"
    LOG_TOUCH = "log_touch"


class DraftCause(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    EDITED = "edited"
    EXPIRED = "expired"
    EVENT_EXPIRED = "event_expired"
    REJECTED = "rejected"
    SENT = "sent"
    SUPERSEDED = "superseded"
    SEQUENCE_STOPPED = "sequence_stopped"
    TOUCH_LOGGED = "touch_logged"


CREATION: Final = Transition(DraftStatus.READY, DraftCause.REQUESTED)
PENDING: Final = (DraftStatus.READY, DraftStatus.APPROVED)
TERMINAL: Final = (
    DraftStatus.SENT,
    DraftStatus.REJECTED,
    DraftStatus.SUPERSEDED,
    DraftStatus.CANCELLED,
)
STEP_KINDS: Final = (DraftKind.STEP, DraftKind.RECONTACT)
RESPONSIVE_KINDS: Final = (DraftKind.REPLY, DraftKind.REBOOK, DraftKind.CHANNEL)

_S = DraftStatus
_E = DraftEvent
_C = DraftCause


def _rows(
    sources: Iterable[DraftStatus], event: DraftEvent, to: DraftStatus, cause: DraftCause
) -> dict[tuple[DraftStatus, DraftEvent], Transition[DraftStatus, DraftCause]]:
    return {(source, event): Transition(to, cause) for source in sources}


TRANSITIONS: Final[
    Mapping[tuple[DraftStatus, DraftEvent], Transition[DraftStatus, DraftCause]]
] = MappingProxyType(
    {
        **_rows((_S.READY,), _E.APPROVE, _S.APPROVED, _C.APPROVED),
        **_rows((_S.APPROVED,), _E.RETURN_EDITED, _S.READY, _C.EDITED),
        **_rows((_S.APPROVED,), _E.RETURN_EXPIRED, _S.READY, _C.EXPIRED),
        **_rows((_S.APPROVED,), _E.RETURN_EVENT_EXPIRED, _S.READY, _C.EVENT_EXPIRED),
        **_rows(PENDING, _E.REJECT, _S.REJECTED, _C.REJECTED),
        **_rows((_S.APPROVED,), _E.SEND, _S.SENT, _C.SENT),
        **_rows(PENDING, _E.SUPERSEDE, _S.SUPERSEDED, _C.SUPERSEDED),
        **_rows(PENDING, _E.CANCEL, _S.CANCELLED, _C.SEQUENCE_STOPPED),
        **_rows((_S.READY,), _E.LOG_TOUCH, _S.SENT, _C.TOUCH_LOGGED),
    }
)


def transition(
    current: DraftStatus, event: DraftEvent, *, kind: DraftKind | None = None
) -> Transition[DraftStatus, DraftCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row, or for ``log_touch`` on a Draft whose ``kind`` isn't ``channel``."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    if event == DraftEvent.LOG_TOUCH and kind != DraftKind.CHANNEL:
        raise IllegalTransition(MACHINE, event, current)
    return found


FIRST_STEP: Final = 1


def contact_purpose(kind: DraftKind | str, step_no: int | None) -> Purpose | None:
    """AD-16's purpose for emailing a Draft of ``kind`` (Story 5.4; the one definition approval
    and sending use): ``cold`` for a Step 1 ``step``, ``follow_up`` for a later Step,
    ``recontact``, ``reply`` and ``rebook`` for those kinds; ``None`` for ``channel`` (never
    emailed) or a kind that isn't one."""
    if kind == DraftKind.STEP:
        return Purpose.COLD if step_no == FIRST_STEP else Purpose.FOLLOW_UP
    return _PURPOSES.get(str(kind))


_PURPOSES: Final[Mapping[str, Purpose]] = MappingProxyType(
    {
        DraftKind.RECONTACT: Purpose.RECONTACT,
        DraftKind.REPLY: Purpose.REPLY,
        DraftKind.REBOOK: Purpose.REBOOK,
    }
)
