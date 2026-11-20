"""The Enrollment machine and the rules of enrolling a Contact (AD-4, AD-16, AD-21, FR-3, FR-63;
Story 4.8). Standard library only, plus ``nextron.domain``.

**Statuses** (``EnrollmentStatus``): ``active``, ``paused``, ``stopped`` and ``completed``.
``LIVE_STATUSES`` are ``active`` and ``paused`` (at most one live Enrollment per Lead);
``ENDED_STATUSES`` (terminal) are ``stopped`` and ``completed``. ``sequencing`` owns the
``enrollments`` table and appends one ``events`` row (entity ``enrollments``) per change.

**Origins** (``EnrollmentOrigin``): ``selection``, ``operator_pick``, ``referral``,
``recontact`` and ``restart``.

**Pause causes** (``PauseCause``): ``inbound_pending``, ``uncertain_class`` and ``operator``.
**Stop causes** (``StopCause``): ``reply``, ``manual_reply``, ``hard_bounce``, ``suppressed``,
``operator_stop``, ``operator_rejected``, ``lead_closed``, ``inbox_removed`` and
``contact_erased``. ``REPLY_CAUSES`` (``reply``, ``manual_reply``) are the stops that mean the
Sequence ended with a Reply (FR-63).

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

=============  ================  =========  ==========================
Event          From              To         Cause
=============  ================  =========  ==========================
pause_<cause>  active            paused     <cause> (the pause cause)
resume         paused            active     resumed
stop_<cause>   active, paused    stopped    <cause> (the stop cause)
complete       active, paused    completed  completed
=============  ================  =========  ==========================

``PAUSE_EVENTS`` and ``STOP_EVENTS`` map each cause to its event. **Creation.** ``CREATION`` is
``Transition(active, enrolled)``: the creation ``events`` row has no ``from`` status.

**Refusals.** ``Sequencing.enroll`` answers a refusal with ``EnrollRefusal(code, reasons=(),
blocks=())``: ``code`` a ``RefusalCode`` (in the order ``enroll`` checks: ``blocked``,
``restart_not_allowed`` (Story 8.11, origin ``restart`` only, straight after the gate),
``campaign_not_active``, ``missing_tz_or_market``, ``not_eligible``, ``campaign_mismatch``,
``lead_already_replied``, ``lead_already_enrolled``, ``no_active_variant``,
``no_sending_inbox``), ``reasons`` the Eligibility ``Reason``\\s of ``not_eligible`` and
``blocks`` the gate's ``BlockReason``\\s of ``blocked``; both are empty for every other code. A
success is ``NewEnrollment(enrollment_id, lead_id, contact_id, campaign_id, variant_id,
sending_inbox_id)``.

**Variants.** ``next_variant(variants, last_variant_id) -> str | None`` walks ``variants`` (each
with an ``id`` and a ``status``, in the Campaign's name order), starting just after the one with
id ``last_variant_id`` and wrapping round, and returns the id of the first ``active`` one. With
no ``last_variant_id``, or one no longer among ``variants``, the walk starts at the first. With
no active Variant it is ``None``. So two active Variants alternate, and an inactive one is
skipped.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from nextron.domain.campaign import VariantStatus
from nextron.domain.contactability import BlockReason
from nextron.domain.eligibility import Reason
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "enrollment"


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class EnrollmentOrigin(StrEnum):
    SELECTION = "selection"
    OPERATOR_PICK = "operator_pick"
    REFERRAL = "referral"
    RECONTACT = "recontact"
    RESTART = "restart"


class PauseCause(StrEnum):
    INBOUND_PENDING = "inbound_pending"
    UNCERTAIN_CLASS = "uncertain_class"
    OPERATOR = "operator"


class StopCause(StrEnum):
    REPLY = "reply"
    MANUAL_REPLY = "manual_reply"
    HARD_BOUNCE = "hard_bounce"
    SUPPRESSED = "suppressed"
    OPERATOR_STOP = "operator_stop"
    OPERATOR_REJECTED = "operator_rejected"
    LEAD_CLOSED = "lead_closed"
    INBOX_REMOVED = "inbox_removed"
    CONTACT_ERASED = "contact_erased"


class EnrollmentEvent(StrEnum):
    PAUSE_INBOUND_PENDING = "pause_inbound_pending"
    PAUSE_UNCERTAIN_CLASS = "pause_uncertain_class"
    PAUSE_OPERATOR = "pause_operator"
    RESUME = "resume"
    STOP_REPLY = "stop_reply"
    STOP_MANUAL_REPLY = "stop_manual_reply"
    STOP_HARD_BOUNCE = "stop_hard_bounce"
    STOP_SUPPRESSED = "stop_suppressed"
    STOP_OPERATOR_STOP = "stop_operator_stop"
    STOP_OPERATOR_REJECTED = "stop_operator_rejected"
    STOP_LEAD_CLOSED = "stop_lead_closed"
    STOP_INBOX_REMOVED = "stop_inbox_removed"
    STOP_CONTACT_ERASED = "stop_contact_erased"
    COMPLETE = "complete"


class EnrollmentCause(StrEnum):
    ENROLLED = "enrolled"
    INBOUND_PENDING = "inbound_pending"
    UNCERTAIN_CLASS = "uncertain_class"
    OPERATOR = "operator"
    RESUMED = "resumed"
    REPLY = "reply"
    MANUAL_REPLY = "manual_reply"
    HARD_BOUNCE = "hard_bounce"
    SUPPRESSED = "suppressed"
    OPERATOR_STOP = "operator_stop"
    OPERATOR_REJECTED = "operator_rejected"
    LEAD_CLOSED = "lead_closed"
    INBOX_REMOVED = "inbox_removed"
    CONTACT_ERASED = "contact_erased"
    COMPLETED = "completed"


CREATION: Final = Transition(EnrollmentStatus.ACTIVE, EnrollmentCause.ENROLLED)
LIVE_STATUSES: Final = (EnrollmentStatus.ACTIVE, EnrollmentStatus.PAUSED)
ENDED_STATUSES: Final = (EnrollmentStatus.STOPPED, EnrollmentStatus.COMPLETED)
REPLY_CAUSES: Final = frozenset({StopCause.REPLY, StopCause.MANUAL_REPLY})

PAUSE_EVENTS: Final[Mapping[PauseCause, EnrollmentEvent]] = MappingProxyType(
    {cause: EnrollmentEvent(f"pause_{cause}") for cause in PauseCause}
)
STOP_EVENTS: Final[Mapping[StopCause, EnrollmentEvent]] = MappingProxyType(
    {cause: EnrollmentEvent(f"stop_{cause}") for cause in StopCause}
)


def _rows(
    sources: Iterable[EnrollmentStatus],
    event: EnrollmentEvent,
    to: EnrollmentStatus,
    cause: EnrollmentCause,
) -> dict[
    tuple[EnrollmentStatus, EnrollmentEvent], Transition[EnrollmentStatus, EnrollmentCause]
]:
    return {(source, event): Transition(to, cause) for source in sources}


_S = EnrollmentStatus
_E = EnrollmentEvent
_C = EnrollmentCause

TRANSITIONS: Final[
    Mapping[
        tuple[EnrollmentStatus, EnrollmentEvent], Transition[EnrollmentStatus, EnrollmentCause]
    ]
] = MappingProxyType(
    {
        **{
            key: row
            for cause, event in PAUSE_EVENTS.items()
            for key, row in _rows((_S.ACTIVE,), event, _S.PAUSED, _C(cause)).items()
        },
        **_rows((_S.PAUSED,), _E.RESUME, _S.ACTIVE, _C.RESUMED),
        **{
            key: row
            for cause, event in STOP_EVENTS.items()
            for key, row in _rows(LIVE_STATUSES, event, _S.STOPPED, _C(cause)).items()
        },
        **_rows(LIVE_STATUSES, _E.COMPLETE, _S.COMPLETED, _C.COMPLETED),
    }
)


def transition(
    current: EnrollmentStatus, event: EnrollmentEvent
) -> Transition[EnrollmentStatus, EnrollmentCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


# Enrolling ------------------------------------------------------------------------------------


class RefusalCode(StrEnum):
    """Why ``enroll`` refused, in the order it checks."""

    BLOCKED = "blocked"
    RESTART_NOT_ALLOWED = "restart_not_allowed"  # Story 8.11
    CAMPAIGN_NOT_ACTIVE = "campaign_not_active"
    MISSING_TZ_OR_MARKET = "missing_tz_or_market"
    NOT_ELIGIBLE = "not_eligible"
    CAMPAIGN_MISMATCH = "campaign_mismatch"
    LEAD_ALREADY_REPLIED = "lead_already_replied"
    LEAD_ALREADY_ENROLLED = "lead_already_enrolled"
    NO_ACTIVE_VARIANT = "no_active_variant"
    NO_SENDING_INBOX = "no_sending_inbox"


@dataclass(frozen=True, slots=True)
class EnrollRefusal:
    """``enroll`` wrote nothing: ``code``, with the Eligibility ``reasons`` of ``not_eligible``
    and the gate's ``blocks`` of ``blocked``."""

    code: RefusalCode
    reasons: tuple[Reason, ...] = ()
    blocks: tuple[BlockReason, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, RefusalCode):
            raise TypeError("EnrollRefusal.code must be a RefusalCode")
        if not isinstance(self.reasons, tuple) or not all(
            isinstance(reason, Reason) for reason in self.reasons
        ):
            raise TypeError("EnrollRefusal.reasons must be a tuple of Reason values")
        if not isinstance(self.blocks, tuple) or not all(
            isinstance(block, BlockReason) for block in self.blocks
        ):
            raise TypeError("EnrollRefusal.blocks must be a tuple of BlockReason values")
        if self.reasons and self.code is not RefusalCode.NOT_ELIGIBLE:
            raise ValueError("only a not_eligible refusal has reasons")
        if self.blocks and self.code is not RefusalCode.BLOCKED:
            raise ValueError("only a blocked refusal has blocks")


@dataclass(frozen=True, slots=True)
class NewEnrollment:
    """The Enrollment ``enroll`` created, with what it bound."""

    enrollment_id: str
    lead_id: str
    contact_id: str
    campaign_id: str
    variant_id: str
    sending_inbox_id: str


class VariantLike(Protocol):
    """A Campaign's Variant as ``next_variant`` reads it."""

    @property
    def id(self) -> str: ...

    @property
    def status(self) -> VariantStatus | str: ...


def next_variant(variants: Sequence[VariantLike], last_variant_id: str | None) -> str | None:
    """The id of the next ``active`` Variant after ``last_variant_id``, wrapping round, or
    ``None``; see the module docstring."""
    ordered = list(variants)
    ids = [variant.id for variant in ordered]
    start = ids.index(last_variant_id) + 1 if last_variant_id in ids else 0
    for offset in range(len(ordered)):
        variant = ordered[(start + offset) % len(ordered)]
        if variant.status == VariantStatus.ACTIVE:
            return variant.id
    return None
