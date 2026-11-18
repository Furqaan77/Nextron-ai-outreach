"""Public command API of the ``drafting`` service (AD-3, AD-4, AD-16, AD-21; Story 4.9).

Other services and the delivery layers change ``drafting`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Drafting(clock)`` owns the ``drafts`` content group (``nextron.db.repos.drafting.drafts``);
``approval`` owns the status group. A Draft is inserted at ``ready`` (the column's server
default) with one ``events`` row: entity ``drafts``, ``None`` to ``ready``, cause
``requested``, ``cause_ref`` the Enrollment, actor ``system:drafting``, at the clock's now.

**``@command request_steps(enrollment_id, steps, urgency) -> tuple[str, ...] | Blocked``**
creates an Enrollment's Draft slots, with no text yet (Story 4.12 drafts it) and no job
(Stories 4.15 and 4.18).

1. **Arguments**, checked before anything else: ``enrollment_id`` a ``str``, ``steps`` a
   non-empty ``tuple`` or ``list`` of ``int`` Step numbers (``bool`` refused) in strictly
   ascending order, and ``urgency`` an ``Urgency`` (``check_urgency``; validated, not stored).
   A bad type raises ``TypeError``, a bad value ``ValueError``. An unknown Enrollment, or one
   whose Sequence is missing or no longer at the version it was enrolled at, raises
   ``LookupError``; an ended one ``ValueError``; a Step outside its Sequence ``ValueError``.
   Nothing is written.
2. **The gate.** ``compliance_api.assert_contactable(ContactSubject(contact), "email",
   "cold")``, looked up at call time (AD-16), answers before any write; ``Blocked`` is
   returned as it is, with nothing written.
3. **Slots.** One slot per Step not already present, in **one** ``INSERT`` statement: kind
   ``recontact`` for an Enrollment of origin ``recontact``, else ``step``; ``day_offset`` the
   Step's Sequence day (its brief's ``delay_days``), snapshotted so a later Sequence version
   never changes a running Enrollment. Only a Step 1 slot is due at once: ``due_at =
   step_due_at(now, 0, the Contact's tz)`` (a Contact with no ``tz`` leaves it unset, logged).
   Every later Step waits for ``Sequencing.on_step_sent``. One ``events`` row per slot. It
   returns the new slots' ids, by Step (``()`` when every Step was already present).

**``@command set_due(draft_id, due_at) -> bool``** sets the due time (an aware ``datetime``,
always from ``nextron.domain.sending_policy.step_due_at``) of a ``ready`` or ``approved``
Draft; any other status is a no-op ``False``. A non-``str`` id or a naive or
non-``datetime`` time raises ``TypeError``, an unknown Draft ``LookupError``.

- ``ERASURE`` and ``@command erase_pii(n: ContactErased)`` (Story 3.13, AD-17): drafting's
  ``ContactErased`` subscriber writes nothing, because ``footer_snapshots`` (its one table with
  a personal-data column) holds the Sender's name and Nextron's postal address, never a
  Contact's data; ``drafts`` holds none at all.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.repos.drafting.drafts import NewSlot
from nextron.db.uow import command
from nextron.domain.ai import Urgency, check_urgency
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import CREATION, DraftKind
from nextron.domain.enrollment import LIVE_STATUSES, EnrollmentOrigin, EnrollmentStatus
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.notifications import ContactErased
from nextron.domain.sending_policy import step_due_at
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

ENTITY: Final = "drafts"
ACTOR: Final = "system:drafting"
CHANNEL: Final = "email"
PURPOSE: Final = "cold"
FIRST_STEP: Final = 1


def _checked_steps(steps: object) -> tuple[int, ...]:
    """``steps`` as a tuple; ``TypeError`` or ``ValueError`` when it isn't Step numbers."""
    if not isinstance(steps, tuple | list):
        raise TypeError(f"request_steps takes the steps as a tuple, not {type(steps).__name__}")
    if not all(isinstance(step, int) and not isinstance(step, bool) for step in steps):
        raise TypeError("request_steps takes the steps as int Step numbers")
    found = tuple(steps)
    if not found:
        raise ValueError("request_steps needs at least one Step")
    if any(later <= earlier for earlier, later in zip(found, found[1:], strict=False)):
        raise ValueError("request_steps takes the steps in ascending order, each once")
    return found


class Drafting:
    """Draft slots and their due times; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def request_steps(
        self, enrollment_id: str, steps: Sequence[int], urgency: Urgency
    ) -> tuple[str, ...] | Blocked:
        """Create the Enrollment's Draft slots for ``steps``, or answer the gate's ``Blocked``;
        see the module docstring."""
        if not isinstance(enrollment_id, str):
            raise TypeError("request_steps takes the Enrollment id as str")
        chosen = _checked_steps(steps)
        check_urgency(urgency)
        enrollment = sequencing_queries.enrollment(enrollment_id)
        if enrollment is None:
            raise LookupError("request_steps: the Enrollment is unknown")
        if EnrollmentStatus(enrollment.status) not in LIVE_STATUSES:
            raise ValueError("request_steps: the Enrollment has ended")
        sequence = setup_queries.sequence_by_id(enrollment.sequence_id)
        if sequence is None or sequence.version != enrollment.sequence_version:
            raise LookupError("request_steps: the Enrollment's Sequence version isn't stored")
        briefs = sequence.definition.steps
        if chosen[0] < FIRST_STEP or chosen[-1] > len(briefs):
            raise ValueError("request_steps: a Step isn't in the Enrollment's Sequence")

        # the gate, a global looked up at call time (AD-16), before any write
        answer = compliance_api.assert_contactable(
            ContactSubject(enrollment.contact_id), CHANNEL, PURPOSE
        )
        if isinstance(answer, Blocked):
            return answer

        present = {
            draft.step_no
            for draft in drafting_queries.drafts_of(enrollment.id)
            if draft.kind in (DraftKind.STEP, DraftKind.RECONTACT)
        }
        wanted = [step for step in chosen if step not in present]
        if not wanted:
            return ()
        kind = (
            DraftKind.RECONTACT
            if enrollment.origin == EnrollmentOrigin.RECONTACT
            else DraftKind.STEP
        )
        now = self._clock.now()
        first_due = None
        if FIRST_STEP in wanted:
            contact = research_queries.contact(enrollment.contact_id)
            tz = None if contact is None else contact.tz
            if tz is None:
                log.warning("step_1_without_timezone", extra={"enrollment_id": enrollment.id})
            else:
                first_due = step_due_at(now, 0, tz)
        slots = [
            NewSlot(
                kind=str(kind),
                enrollment_id=enrollment.id,
                step_no=step,
                day_offset=briefs[step - 1].delay_days,
                due_at=first_due if step == FIRST_STEP else None,
            )
            for step in wanted
        ]
        ids = tuple(drafts_repo.insert_slots(slots, now))
        for draft_id in ids:
            eventlog.append(
                entity=ENTITY,
                entity_id=draft_id,
                from_status=None,
                to_status=str(CREATION.to),
                cause=str(CREATION.cause),
                cause_ref=enrollment.id,
                actor=ACTOR,
                at=now,
            )
        return ids

    @command
    def set_due(self, draft_id: str, due_at: datetime) -> bool:
        """Set the due time of a ``ready`` or ``approved`` Draft; whether it was set."""
        if not isinstance(draft_id, str):
            raise TypeError("set_due takes the Draft id as str")
        if not isinstance(due_at, datetime) or due_at.utcoffset() is None:
            raise TypeError("set_due takes an aware datetime")
        if drafting_queries.draft(draft_id) is None:
            raise LookupError("set_due: the Draft is unknown")
        return drafts_repo.set_due(draft_id, due_at, self._clock.now())


# Erasure (Story 3.13) -------------------------------------------------------------------------

# What drafting's erase_pii does to each of its tables with a personal-data column: none of them
# ever holds a Contact's data.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "footer_snapshots": TableErasure(
            Coverage.NOT_HELD,
            "a Market footer's text: the Sender's name and Nextron's postal address, never a "
            "Contact's data",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``drafting``'s ``ContactErased`` subscriber (Story 3.13, AD-17): it writes nothing,
    because no ``drafting`` table holds a Contact's data (``ERASURE``)."""
