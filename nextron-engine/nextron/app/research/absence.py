"""The "contact form only" Absence on a Lead (FR-10, AD-3, AD-7, AD-18; Story 2.9): part of the
``research`` service, the sole writer of Evidence and Signals. ``WebsiteCrawls`` (in
``nextron.app.research.api``) calls it each time it writes the Lead's ``contact_methods``, by
``enrich_lead``'s apply or ``render_pages``' apply, so it never waits for the AI extraction. The
rule and the reconciliation are pure and live in ``nextron.domain.absence``.

``apply_contact_form_only(lead_id, segment, methods, pages, now) -> AbsenceApplied`` runs in the
caller's transaction (it begins, commits and logs nothing):

1. ``contact_form_only(segment, methods, pages)`` gives the reason.
2. **The live rows in scope** are the Lead's live Evidence of ``type`` ``absence`` and
   ``extracted_by`` ``rule`` linked to any Signal of type ``CONTACT_FORM_ONLY``.
   ``reconcile_absence`` plans them, with ``holds`` when the reason is ``holds`` and
   ``methods.pages_checked``.
3. **The writes.** A confirmed row's ``last_confirmed_at`` becomes ``now``. A new row (inserted,
   or the one that supersedes) has ``type`` ``absence``, ``sought`` ``CONTACT_FORM_ONLY_SOUGHT``,
   ``pages_checked`` ``methods.pages_checked``, ``source`` ``website``, no ``source_url``,
   ``retrieved_at`` (and so ``last_confirmed_at``) ``now`` and ``extracted_by`` ``rule``; it is
   linked to the Lead's current Signal of the type (the first by ``first_seen_at``, then id),
   or creates one: an Attribute with the Segment's Tier and ``score_only``, no event date,
   ``first_seen_at`` ``now``. A superseded row gets ``superseded_by``, a retracted one
   ``retracted_at`` (``now``).
4. **Then** ``retract_unsupported(lead_id, now)`` retracts every current Signal of the Lead left
   with no live Evidence, and ``EvidenceChanged(lead_id, evidence_id)`` is published for the row
   superseded, then for each row retracted.

``AbsenceApplied(reason, confirmed, inserted, superseded, retracted, signals_created,
signals_retracted)`` counts the plan's parts (``inserted``: a new row that supersedes nothing;
``superseded``: a row a new row replaced) and the Signals created and retracted. No ``events``
row is written: the caller logs ``absence_checked`` with these counts after its transaction
commits.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import current
from nextron.domain.absence import (
    CONTACT_FORM_ONLY,
    CONTACT_FORM_ONLY_SOUGHT,
    AbsencePlan,
    AbsenceReason,
    CheckedPage,
    LiveAbsence,
    contact_form_only,
    reconcile_absence,
)
from nextron.domain.extraction import EXTRACTED_BY_RULE, EvidenceSource
from nextron.domain.notifications import EvidenceChanged
from nextron.domain.segments import EvidenceType, SegmentDef, SignalClass
from nextron.domain.widgets import ContactMethods


@dataclass(frozen=True, slots=True)
class AbsenceApplied:
    """What ``apply_contact_form_only`` found and wrote."""

    reason: AbsenceReason
    confirmed: int = 0
    inserted: int = 0
    superseded: int = 0
    retracted: int = 0
    signals_created: int = 0
    signals_retracted: int = 0


def _in_scope(lead_id: str) -> list[LiveAbsence]:
    """The Lead's live rule-made Absence rows that support a ``CONTACT_FORM_ONLY`` Signal."""
    typed = {
        signal.id
        for signal in signals_repo.signals(lead_id, current_only=False)
        if signal.signal_type == CONTACT_FORM_ONLY
    }
    linked = {link.evidence_id for link in signals_repo.links(lead_id) if link.signal_id in typed}
    return [
        LiveAbsence(row.id, row.sought, row.pages_checked, row.created_at)
        for row in signals_repo.evidence(lead_id, live_only=True)
        if row.type == EvidenceType.ABSENCE
        and row.extracted_by == EXTRACTED_BY_RULE
        and row.sought is not None
        and row.id in linked
    ]


def _insert(lead_id: str, pages_checked: tuple[str, ...], now: datetime) -> str:
    return signals_repo.insert_evidence(
        lead_id=lead_id,
        evidence_type=str(EvidenceType.ABSENCE),
        quote=None,
        sought=CONTACT_FORM_ONLY_SOUGHT,
        number=None,
        fact=None,
        value=None,
        pages_checked=pages_checked,
        source=str(EvidenceSource.WEBSITE),
        source_url=None,
        retrieved_at=now,
        extracted_by=EXTRACTED_BY_RULE,
        person_contact_id=None,
        at=now,
    )


def _link(lead_id: str, segment: SegmentDef, evidence_id: str, now: datetime) -> int:
    """Link the new row to the Lead's current Signal of the type, or create one; how many
    Signals were created."""
    found = next(
        (
            signal
            for signal in signals_repo.signals(lead_id, current_only=True)
            if signal.signal_type == CONTACT_FORM_ONLY
        ),
        None,
    )
    if found is not None:
        signals_repo.link(found.id, evidence_id)
        return 0
    # the rule held, so the Segment has the type
    signal_type = next(each for each in segment.signal_types if each.key == CONTACT_FORM_ONLY)
    signals_repo.insert_signal(
        lead_id=lead_id,
        signal_type=CONTACT_FORM_ONLY,
        signal_class=str(SignalClass.ATTRIBUTE),
        tier=signal_type.tier,
        score_only=signal_type.score_only,
        event_date=None,
        first_seen_at=now,
        evidence_ids=[evidence_id],
    )
    return 1


def _carry_out(
    lead_id: str,
    segment: SegmentDef,
    plan: AbsencePlan,
    pages_checked: tuple[str, ...],
    now: datetime,
) -> int:
    """Write the plan; how many Signals were created."""
    if plan.confirm is not None:
        signals_repo.confirm_evidence(plan.confirm, now)
    created = 0
    if plan.insert or plan.supersede is not None:
        new_id = _insert(lead_id, pages_checked, now)
        if plan.supersede is not None:
            signals_repo.supersede_evidence(plan.supersede, new_id)
        created = _link(lead_id, segment, new_id, now)
    for evidence_id in plan.retract:
        signals_repo.retract_evidence(evidence_id, now)
    return created


def apply_contact_form_only(
    lead_id: str,
    segment: SegmentDef,
    methods: ContactMethods,
    pages: Iterable[CheckedPage],
    now: datetime,
) -> AbsenceApplied:
    """Reconcile the Lead's "contact form only" Absence with its latest contact methods, in the
    caller's transaction; see the module docstring."""
    reason = contact_form_only(segment, methods, pages)
    plan = reconcile_absence(
        _in_scope(lead_id), reason is AbsenceReason.HOLDS, methods.pages_checked
    )
    created = _carry_out(lead_id, segment, plan, methods.pages_checked, now)
    signals_retracted = signals_repo.retract_unsupported(lead_id, now)
    changed = [*(() if plan.supersede is None else (plan.supersede,)), *plan.retract]
    for evidence_id in changed:
        current().publish(EvidenceChanged(lead_id=lead_id, evidence_id=evidence_id))
    return AbsenceApplied(
        reason=reason,
        confirmed=0 if plan.confirm is None else 1,
        inserted=1 if plan.insert else 0,
        superseded=0 if plan.supersede is None else 1,
        retracted=len(plan.retract),
        signals_created=created,
        signals_retracted=len(signals_retracted),
    )
