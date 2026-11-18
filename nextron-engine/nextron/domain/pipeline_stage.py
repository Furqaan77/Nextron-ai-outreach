"""The Lead Pipeline Stage machine (AD-4, FR-9, FR-45). Standard library only.

**Stages**, in AD-4 order: ``new``, ``queued``, ``contacted``, ``replied``, ``interested``,
``call_booked``, ``call_held``, ``audit_proposed``, ``audit_won``, ``build_proposed``, ``won``,
``not_now``, ``lost`` and ``disqualified``. ``OPEN_STAGES`` are the first eleven,
``PARKED_STAGES`` is ``not_now``, and ``CLOSED_STAGES`` (terminal; only the Operator reopens
one) are ``lost`` and ``disqualified``. ``pipeline`` owns the ``leads.pipeline_stage`` column
and appends one ``events`` row (entity ``leads``, actor ``system:pipeline``) per change.

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``. "open<X" means every open stage before X; "every open" includes ``won``.

==================  ==================================  ==============  ===================
Event               From                                To              Cause
==================  ==================================  ==============  ===================
enroll              new                                 queued          enrolled
send                new, queued                         contacted       message_sent
reply               new, queued, contacted              replied         reply_received
hot_reply           open<interested, not_now            interested      hot_reply
park                open<won                            not_now         not_now_reply
recontact           not_now                             queued          recontact_due
book_call           replied, interested, not_now        call_booked     operator_move
hold_call           call_booked                         call_held       call_outcome
no_show             call_held                           call_booked     call_outcome
propose_audit       call_held                           audit_proposed  operator_move
win_audit           call_held, audit_proposed           audit_won       operator_move
propose_build       call_held, audit_proposed,          build_proposed  operator_move
                    audit_won
win                 call_held ... build_proposed        won             operator_move
close_lost          every open stage, not_now           lost            closed_lost
close_disqualified  every open stage, not_now           disqualified    closed_disqualified
==================  ==================================  ==============  ===================

Later stories add the commands that apply these moves; they add no status.

**Creation.** ``CREATION[source]`` is ``Transition(new, created_from_<source>)`` for each
``LeadSource``: the creation ``events`` row (no ``from`` status) records where the Lead came
from.

**Reopening.** ``reopen(current, previous)`` is ``Transition(previous, reopened)`` when
``current`` is closed and ``previous`` is open or parked; otherwise it raises
``IllegalTransition(MACHINE, "reopen", current)``.

**Merging.** ``merged(survivor, loser)`` is the survivor's change when ``loser`` is merged into
it, or ``None`` when it keeps its stage:

1. a closed survivor keeps its stage, so two closed stages keep the survivor's;
2. otherwise a closed loser wins (``Transition(loser, leads_merged)``), so a merge never makes
   an organization contactable again;
3. otherwise the later stage in ``ADVANCEMENT`` wins: ``new``, ``queued``, ``contacted``,
   ``replied``, ``not_now``, ``interested``, ``call_booked``, ``call_held``,
   ``audit_proposed``, ``audit_won``, ``build_proposed``, ``won`` (Not Now is a Reply outcome
   that ``hot_reply`` and ``book_call`` move on from);
4. a tie keeps the survivor's stage.

**Segment disqualifiers (Story 2.15).** ``segment_disqualifier`` is a cause no transition gives:
the ``scoring`` service closes a Lead whose Segment disqualifier matched through
``Pipeline.close(lead, disqualified, cause_ref, cause=segment_disqualifier,
actor="system:scoring")``, which applies ``close_disqualified`` and records that cause instead.

``reopen`` and ``merged`` are functions, not table rows, because their target depends on more
than the event.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.leads import LeadSource
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "pipeline_stage"


class PipelineStage(StrEnum):
    NEW = "new"
    QUEUED = "queued"
    CONTACTED = "contacted"
    REPLIED = "replied"
    INTERESTED = "interested"
    CALL_BOOKED = "call_booked"
    CALL_HELD = "call_held"
    AUDIT_PROPOSED = "audit_proposed"
    AUDIT_WON = "audit_won"
    BUILD_PROPOSED = "build_proposed"
    WON = "won"
    NOT_NOW = "not_now"
    LOST = "lost"
    DISQUALIFIED = "disqualified"


OPEN_STAGES: Final = (
    PipelineStage.NEW,
    PipelineStage.QUEUED,
    PipelineStage.CONTACTED,
    PipelineStage.REPLIED,
    PipelineStage.INTERESTED,
    PipelineStage.CALL_BOOKED,
    PipelineStage.CALL_HELD,
    PipelineStage.AUDIT_PROPOSED,
    PipelineStage.AUDIT_WON,
    PipelineStage.BUILD_PROPOSED,
    PipelineStage.WON,
)
PARKED_STAGES: Final = (PipelineStage.NOT_NOW,)
CLOSED_STAGES: Final = (PipelineStage.LOST, PipelineStage.DISQUALIFIED)

# How far a Lead has got, for a merge: Not Now ranks just after replied.
ADVANCEMENT: Final = (
    PipelineStage.NEW,
    PipelineStage.QUEUED,
    PipelineStage.CONTACTED,
    PipelineStage.REPLIED,
    PipelineStage.NOT_NOW,
    PipelineStage.INTERESTED,
    PipelineStage.CALL_BOOKED,
    PipelineStage.CALL_HELD,
    PipelineStage.AUDIT_PROPOSED,
    PipelineStage.AUDIT_WON,
    PipelineStage.BUILD_PROPOSED,
    PipelineStage.WON,
)


class StageEvent(StrEnum):
    ENROLL = "enroll"
    SEND = "send"
    REPLY = "reply"
    HOT_REPLY = "hot_reply"
    PARK = "park"
    RECONTACT = "recontact"
    BOOK_CALL = "book_call"
    HOLD_CALL = "hold_call"
    NO_SHOW = "no_show"
    PROPOSE_AUDIT = "propose_audit"
    WIN_AUDIT = "win_audit"
    PROPOSE_BUILD = "propose_build"
    WIN = "win"
    CLOSE_LOST = "close_lost"
    CLOSE_DISQUALIFIED = "close_disqualified"


class StageCause(StrEnum):
    CREATED_FROM_CSV = "created_from_csv"
    CREATED_FROM_SIGNAL_INBOX = "created_from_signal_inbox"
    CREATED_FROM_PLACES = "created_from_places"
    CREATED_FROM_COMPANIES_HOUSE = "created_from_companies_house"
    CREATED_FROM_JOB_AGGREGATOR = "created_from_job_aggregator"
    ENROLLED = "enrolled"
    MESSAGE_SENT = "message_sent"
    REPLY_RECEIVED = "reply_received"
    HOT_REPLY = "hot_reply"
    NOT_NOW_REPLY = "not_now_reply"
    RECONTACT_DUE = "recontact_due"
    OPERATOR_MOVE = "operator_move"
    CALL_OUTCOME = "call_outcome"
    CLOSED_LOST = "closed_lost"
    CLOSED_DISQUALIFIED = "closed_disqualified"
    REOPENED = "reopened"
    LEADS_MERGED = "leads_merged"
    SEGMENT_DISQUALIFIER = "segment_disqualifier"  # Story 2.15: the scoring service's close


CREATION: Final[Mapping[LeadSource, Transition[PipelineStage, StageCause]]] = MappingProxyType(
    {
        source: Transition(PipelineStage.NEW, StageCause(f"created_from_{source}"))
        for source in LeadSource
    }
)


def _rows(
    sources: tuple[PipelineStage, ...], event: StageEvent, to: PipelineStage, cause: StageCause
) -> dict[tuple[PipelineStage, StageEvent], Transition[PipelineStage, StageCause]]:
    return {(source, event): Transition(to, cause) for source in sources}


def _before(stage: PipelineStage) -> tuple[PipelineStage, ...]:
    """Every open stage before ``stage``."""
    return OPEN_STAGES[: OPEN_STAGES.index(stage)]


_S = PipelineStage
_E = StageEvent
_C = StageCause

TRANSITIONS: Final[
    Mapping[tuple[PipelineStage, StageEvent], Transition[PipelineStage, StageCause]]
] = MappingProxyType(
    {
        **_rows((_S.NEW,), _E.ENROLL, _S.QUEUED, _C.ENROLLED),
        **_rows((_S.NEW, _S.QUEUED), _E.SEND, _S.CONTACTED, _C.MESSAGE_SENT),
        **_rows((_S.NEW, _S.QUEUED, _S.CONTACTED), _E.REPLY, _S.REPLIED, _C.REPLY_RECEIVED),
        **_rows(
            (*_before(_S.INTERESTED), _S.NOT_NOW), _E.HOT_REPLY, _S.INTERESTED, _C.HOT_REPLY
        ),
        **_rows(_before(_S.WON), _E.PARK, _S.NOT_NOW, _C.NOT_NOW_REPLY),
        **_rows((_S.NOT_NOW,), _E.RECONTACT, _S.QUEUED, _C.RECONTACT_DUE),
        **_rows(
            (_S.REPLIED, _S.INTERESTED, _S.NOT_NOW), _E.BOOK_CALL, _S.CALL_BOOKED, _C.OPERATOR_MOVE
        ),
        **_rows((_S.CALL_BOOKED,), _E.HOLD_CALL, _S.CALL_HELD, _C.CALL_OUTCOME),
        **_rows((_S.CALL_HELD,), _E.NO_SHOW, _S.CALL_BOOKED, _C.CALL_OUTCOME),
        **_rows((_S.CALL_HELD,), _E.PROPOSE_AUDIT, _S.AUDIT_PROPOSED, _C.OPERATOR_MOVE),
        **_rows(
            (_S.CALL_HELD, _S.AUDIT_PROPOSED), _E.WIN_AUDIT, _S.AUDIT_WON, _C.OPERATOR_MOVE
        ),
        **_rows(
            (_S.CALL_HELD, _S.AUDIT_PROPOSED, _S.AUDIT_WON),
            _E.PROPOSE_BUILD,
            _S.BUILD_PROPOSED,
            _C.OPERATOR_MOVE,
        ),
        **_rows(
            (_S.CALL_HELD, _S.AUDIT_PROPOSED, _S.AUDIT_WON, _S.BUILD_PROPOSED),
            _E.WIN,
            _S.WON,
            _C.OPERATOR_MOVE,
        ),
        **_rows((*OPEN_STAGES, *PARKED_STAGES), _E.CLOSE_LOST, _S.LOST, _C.CLOSED_LOST),
        **_rows(
            (*OPEN_STAGES, *PARKED_STAGES),
            _E.CLOSE_DISQUALIFIED,
            _S.DISQUALIFIED,
            _C.CLOSED_DISQUALIFIED,
        ),
    }
)

_REOPENABLE: Final = frozenset((*OPEN_STAGES, *PARKED_STAGES))


def transition(
    current: PipelineStage, event: StageEvent
) -> Transition[PipelineStage, StageCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no stage or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


def _stage(value: object) -> PipelineStage | None:
    """``value`` as a ``PipelineStage``, or ``None`` when it is none."""
    if isinstance(value, PipelineStage):
        return value
    if isinstance(value, str):
        try:
            return PipelineStage(value)
        except ValueError:
            return None
    return None


def reopen(
    current: PipelineStage, previous: PipelineStage
) -> Transition[PipelineStage, StageCause]:
    """The Operator reopening a closed Lead at the open or parked stage it had before."""
    closed = _stage(current)
    target = _stage(previous)
    if closed is None or closed not in CLOSED_STAGES or target is None or target not in _REOPENABLE:
        raise IllegalTransition(MACHINE, "reopen", current)
    return Transition(target, StageCause.REOPENED)


def merged(
    survivor: PipelineStage, loser: PipelineStage
) -> Transition[PipelineStage, StageCause] | None:
    """The survivor's change when ``loser`` is merged into it, or ``None``; see the module
    docstring. A value that is no stage raises ``IllegalTransition``."""
    kept = _stage(survivor)
    if kept is None:
        raise IllegalTransition(MACHINE, "merge", survivor)
    other = _stage(loser)
    if other is None:
        raise IllegalTransition(MACHINE, "merge", loser)
    if kept in CLOSED_STAGES:
        return None
    if other in CLOSED_STAGES:
        return Transition(other, StageCause.LEADS_MERGED)
    if ADVANCEMENT.index(other) > ADVANCEMENT.index(kept):
        return Transition(other, StageCause.LEADS_MERGED)
    return None
