"""Public command API of the ``drafting`` service (AD-3, AD-4, AD-16, AD-21; Story 4.9).

Other services and the delivery layers change ``drafting`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Drafting(clock, queue=None)`` owns the ``drafts`` content group
(``nextron.db.repos.drafting.drafts``); ``approval`` owns the status group. A Draft is inserted
at ``ready`` (the column's server default) with one ``events`` row: entity ``drafts``, ``None``
to ``ready``, cause ``requested``, ``cause_ref`` the Enrollment, actor ``system:drafting``, at
the clock's now.

**``@command request_steps(enrollment_id, steps, urgency) -> tuple[str, ...] | Blocked``**
creates an Enrollment's Draft slots, with no text yet, and (Story 4.15) the job that drafts Step
1. A ``Drafting`` built without a ``queue`` refuses with ``RuntimeError`` right after the
argument checks, before any read or write.

1. **Arguments**, checked before anything else: ``enrollment_id`` a ``str``, ``steps`` a
   non-empty ``tuple`` or ``list`` of ``int`` Step numbers (``bool`` refused) in strictly
   ascending order, and ``urgency`` an ``Urgency`` (``check_urgency``; not stored: it picks
   the Step 1 job's lane). A bad type raises ``TypeError``, a bad value ``ValueError``. An
   unknown Enrollment raises
   ``LookupError``; an ended one ``ValueError``; a Step outside its Sequence ``ValueError``.
   Nothing is written. The Step briefs (and each slot's ``day_offset``) come from the
   Enrollment's bound version (Story 11.7: ``sequence_version_id``, read through
   ``queries.setup.sequence_version``), whatever the Sequence's current version is. An
   Enrollment with no bound version row (one made before 11.7 at a version that was never kept)
   uses the current Sequence while it is still at the version it was enrolled at; otherwise,
   or when its bound row or Sequence is missing, ``LookupError``.
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
4. **The Step 1 job** (Story 4.15): when it created a Step 1 slot (kind ``step`` or
   ``recontact``), in the same unit of work, one ``draft_step`` job
   (``nextron.domain.step_drafting.DRAFT_STEP_JOB``; scope the Enrollment id, discriminator
   ``1``, payload ``{"v": 1, "enrollment_id": <id>, "step": 1}``) on the lane of ``urgency``:
   ``bulk`` on the bulk lane, else the interactive lane (``enroll`` passes ``soon``). The job
   drafts at ``bulk`` on the bulk lane and at ``soon`` on the interactive lane.
   ``StepDrafts`` (``nextron.app.drafting.generation``) runs it.

**``@command set_due(draft_id, due_at) -> bool``** sets the due time (an aware ``datetime``,
always from ``nextron.domain.sending_policy.step_due_at``) of a ``ready`` or ``approved``
Draft; any other status is a no-op ``False``. A non-``str`` id or a naive or
non-``datetime`` time raises ``TypeError``, an unknown Draft ``LookupError``.

**A Draft's text** (FR-20, FR-23, AD-6; Story 4.12) lives in immutable ``draft_revisions``
(``nextron.db.repos.drafting.revisions``); ``drafts.current_rev`` names the latest. Every
revision is appended by one private writer, ``_append(draft, subject, body, origin,
traceability, at, claims=None)``, which checks before any write, then writes in the caller's
unit:

0. The text, whatever the origin (``_check_text``): a subject of 1 to ``MAX_SUBJECT`` (200)
   characters after strip, with no line break and no control character (Unicode category
   ``Cc``; it becomes one header line), and a body of at most ``MAX_BODY`` (10,000) characters
   (``ValueError``).
1. The Draft's Enrollment and its Contact must exist (``LookupError``), and the Contact must
   have an email and not be erased (``ValueError``). The Contact's Market (``market_id``, read
   with ``queries.setup.market_by_id``) must be stored (``LookupError``).
2. The footer is ``render.footer(queries.setup.footer_settings(), the Market, source)``, where
   ``source`` is the ``source_kind`` of the Contact's earliest Provenance record with subject
   ``contact`` (``None`` without one). A Market that needs a privacy line and a source with no
   phrase raises ``ValueError`` (``render.NO_PHRASE``): a UK footer never states a false source.
   A blank footer raises ``ValueError``.
3. ``render.compose`` of the subject and body with that footer for the Contact's email, and
   ``render.text_hash`` of the result with the Enrollment's ``sending_inbox_id``.
4. **The grounding rules** (Story 4.13): ``domain.grounding_rules.check`` over the subject, the
   body, ``claims`` (the drafting output's claims; ``None`` for an edit or a footer change, so
   only the text rules run) and the traceability's citation set, with the Step's brief (the
   Sequence version the Enrollment is bound to, Story 11.7's ``_bound_briefs``, combined at
   the merge; the Draft's ``step_no``) and a context of the Settings
   ``sender_signature`` and ``privacy_notice_url``, the kinds of all the Lead's Evidence (live or
   not, so a claim on superseded Absence Evidence is still checked) and its current
   Signals (with their live Evidence ids), the strongest live Event
   (``domain.signals.strongest_event`` with the Segment's weights, at ``at``), the facts
   ``queries.playbook.allowed_facts`` allows for the Campaign's Segment and Market, and the
   current revisions of the Enrollment's earlier Step Drafts. An unknown Campaign, Sequence or
   Segment, or a Step without a brief, raises ``LookupError``.
5. ``footer_snapshots.ensure`` (identical footer text reuses its row), one revision at
   ``current_rev + 1`` (1 when there is none) with the traceability given, its one
   ``grounding_results`` row (``fail`` with the flags, each ``source`` ``rules``, when any rule
   flags, else ``pending``; the ``RULES_VERSION``; checked at ``at``), and
   ``drafts.current_rev`` moved to it (``updated_at`` the clock's now).
6. **The AI check** (Story 4.14): in the same unit of work, a ``check_grounding`` job
   (``nextron.domain.grounding_check.CHECK_GROUNDING_JOB``; scope the Draft id, discriminator
   the new ``rev``, payload ``{"v": 1, "draft_id": <id>, "rev": <rev>}``) on the lane of
   ``_append``'s ``urgency``: ``bulk`` on the bulk lane, ``interactive`` and ``soon`` on the
   interactive lane (the job type's default). ``revise`` passes ``interactive`` and
   ``recompose_ready`` ``bulk``; a generated revision passes its caller's (Step 1: ``soon``).
   ``_append`` refuses with ``RuntimeError`` before any write when the ``Drafting`` was built
   without a ``queue``. ``GroundingChecks`` (``nextron.app.drafting.grounding``) runs the job.
7. **Generic** (Story 4.19, FR-22): ``_append`` takes ``generic: bool | None = None``. ``True``
   (a revision filled from a Segment fallback template, ``nextron.app.drafting.fallback``) and
   ``False`` (a route-made revision) set ``drafts.generic`` when it differs
   (``drafts_repo.set_generic``); ``None`` keeps the Draft's flag, except that an origin
   ``regenerated`` with a ``route_tuple_id`` resolves to ``False`` (a route-made regenerate
   clears it). The resolved flag also decides the rules' waiver: a generic revision's
   ``RulesContext.waived_rules`` is ``{"signal_count"}`` (``_grounding``'s ``waived_rules``),
   and the ``grounding_results`` row stores ``RulesResult.waived`` as ``waived_rules``. So
   ``revise`` and ``recompose_ready`` keep both the flag and the waiver.

**``@command revise(draft_id, subject, body) -> int``** (the Operator's edit; Epic 5 calls it
through ``approval``). ``draft_id``, ``subject`` and ``body`` must be ``str`` (``TypeError``).
``subject`` is stored stripped; both are checked against step 0's rules before the Draft is
looked up (``ValueError``; the body may be empty). An unknown Draft raises ``LookupError``; one that
isn't ``ready`` or has no revision yet ``ValueError``. It appends origin ``operator_edit`` with
the previous revision's traceability (evidence, facts, cited Signal, route tuple, Playbook,
prompt version) and no ``instruction``, and returns the new ``rev``. Its grounding result comes
from the text rules (no claims) until its ``check_grounding`` job (interactive) records the AI
check.

**``@command recompose_ready(n: FooterChanged)``**, ``FooterChanged``'s subscriber
(``main.build_subscribers``): for each ``ready`` Draft with a revision whose Contact's
``market_id`` is ``n.market_id``, by ``id``, it appends origin ``footer_change`` with the same
subject, body and traceability, only when the new ``text_hash`` differs from the current one
(so a repeat is a no-op). A Draft whose footer can't be composed (``ValueError``,
``LookupError`` or ``InvalidMarkets`` from the checks) is logged (``recompose_skipped``, with
the Draft id and the error type) and skipped, before any write. ``approved``, ``sent`` and every
other status are untouched. Each new revision's ``check_grounding`` job runs on the bulk lane.

- ``ERASURE`` and ``@command erase_pii(n: ContactErased)`` (Story 3.13, AD-17): ``draft_revisions``
  is tombstoned: every revision of every Draft of each of the Contact's Enrollments gets
  ``subject``, ``body`` and a set ``instruction`` ``[erased]`` (rows, hashes and traceability
  kept; the triggers allow only that). ``grounding_results`` is tombstoned too (Story 4.13):
  each flag's non-empty ``span`` becomes ``[erased]`` (an empty span, a whole-revision flag,
  stays empty), and each AI flag's explanation (model text, Story 4.14) too; the rule, the
  rules' explanations, status and rows kept.
  ``footer_snapshots`` holds the Sender's name and
  Nextron's postal address, never a Contact's data; ``drafts`` holds none at all.
"""

import logging
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.drafting import DraftView, RevisionView
from nextron.app.queries.sequencing import StoredEnrollment
from nextron.db import eventlog
from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.repos.drafting import footer_snapshots as footer_snapshots_repo
from nextron.db.repos.drafting import grounding_results as grounding_results_repo
from nextron.db.repos.drafting import revisions as revisions_repo
from nextron.db.repos.drafting.drafts import NewSlot
from nextron.db.repos.drafting.grounding_results import NewGroundingResult, StoredFlag
from nextron.db.repos.drafting.revisions import NewRevision
from nextron.db.uow import command
from nextron.domain import render
from nextron.domain.ai import Urgency, check_urgency
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.contacts import ProvenanceSubject
from nextron.domain.draft import CREATION, STEP_KINDS, DraftKind, DraftStatus, RevisionOrigin
from nextron.domain.enrollment import LIVE_STATUSES, EnrollmentOrigin, EnrollmentStatus
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.grounding_check import (
    CHECK_GROUNDING_JOB,
    DRAFT_ID_FIELD,
    REV_FIELD,
    FlagSource,
)
from nextron.domain.grounding_rules import (
    SIGNAL_COUNT,
    Claim,
    EarlierStep,
    LeadSignal,
    RevisionInput,
    RulesContext,
    RulesResult,
)
from nextron.domain.grounding_rules import check as check_grounding
from nextron.domain.job import VERSION_FIELD, Lane
from nextron.domain.markets import InvalidMarkets
from nextron.domain.notifications import ContactErased, FooterChanged
from nextron.domain.segments import SignalClass
from nextron.domain.sending_policy import step_due_at
from nextron.domain.sequences import StepBrief
from nextron.domain.signals import EventSignal, strongest_event
from nextron.domain.step_drafting import DRAFT_STEP_JOB, ENROLLMENT_ID_FIELD, STEP_FIELD
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

ENTITY: Final = "drafts"
ACTOR: Final = "system:drafting"
CHANNEL: Final = "email"
PURPOSE: Final = "cold"
FIRST_STEP: Final = 1
MAX_SUBJECT: Final = 200
MAX_BODY: Final = 10_000
LINE_BREAKS: Final = frozenset("\r\n\x0b\x0c\x1c\x1d\x1e\x85  ")
RECOMPOSE_SKIPPED: Final = "recompose_skipped"
PAYLOAD_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Traceability:
    """What a revision records of how its text was made (copied by an edit or a footer
    change)."""

    evidence_ids: tuple[str, ...]
    fact_versions: tuple[tuple[str, int], ...]
    cited_signal_id: str | None
    route_tuple_id: str | None
    playbook_sha256: str | None
    playbook_version: str | None
    prompt_version: str | None
    instruction: str | None


@dataclass(frozen=True, slots=True)
class _Text:
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class _Footer:
    text: str


def _copied(revision: RevisionView) -> Traceability:
    """The traceability of ``revision``, without its regenerate instruction."""
    return Traceability(
        evidence_ids=revision.evidence_ids,
        fact_versions=revision.fact_versions,
        cited_signal_id=revision.cited_signal_id,
        route_tuple_id=revision.route_tuple_id,
        playbook_sha256=revision.playbook_sha256,
        playbook_version=revision.playbook_version,
        prompt_version=revision.prompt_version,
        instruction=None,
    )


def _checked_text(subject: object, body: object) -> tuple[str, str]:
    """``revise``'s subject (stripped) and body; ``TypeError`` or ``ValueError``."""
    if not isinstance(subject, str) or not isinstance(body, str):
        raise TypeError("revise takes the subject and the body as str")
    chosen = subject.strip()
    _check_text(chosen, body)
    return chosen, body


def _check_text(subject: str, body: str) -> None:
    """Every revision's text rules, whatever its origin: a subject of 1 to ``MAX_SUBJECT``
    characters after strip with no control character or line break (it becomes one header
    line), and a body of at most ``MAX_BODY`` characters; else ``ValueError``."""
    if not 1 <= len(subject.strip()) <= MAX_SUBJECT:
        raise ValueError(f"the subject must be 1 to {MAX_SUBJECT} characters")
    if any(
        character in LINE_BREAKS or unicodedata.category(character) == "Cc"
        for character in subject
    ):
        raise ValueError("the subject must be one line without control characters")
    if len(body) > MAX_BODY:
        raise ValueError(f"the body must be at most {MAX_BODY:,} characters")


@dataclass(frozen=True, slots=True)
class _Prepared:
    """What ``_append`` checked and computed before any write."""

    market_id: str
    footer_text: str
    text_hash: str
    grounding: RulesResult
    generic: bool = False  # Story 4.19: the resolved flag the rules ran with


def _earlier_steps(draft: DraftView, enrollment_id: str) -> tuple[EarlierStep, ...]:
    """The current revisions of the Enrollment's Step Drafts before ``draft``'s Step."""
    earlier = []
    for other in drafting_queries.drafts_of(enrollment_id):
        if (
            other.kind not in STEP_KINDS
            or other.step_no is None
            or draft.step_no is None
            or other.step_no >= draft.step_no
        ):
            continue
        found = drafting_queries.current_revision(other)
        if found is not None:
            earlier.append(
                EarlierStep(
                    step=other.step_no,
                    fact_ids=frozenset(fact_id for fact_id, _ in found.fact_versions),
                    cited_signal_id=found.cited_signal_id,
                )
            )
    return tuple(earlier)


def generic_waiver(generic: bool) -> frozenset[str]:
    """The rules a revision waives: ``signal_count`` for a generic one (Story 4.19)."""
    return frozenset({SIGNAL_COUNT}) if generic else frozenset()


def _resolved_generic(
    draft: DraftView, origin: RevisionOrigin, traceability: Traceability, generic: bool | None
) -> bool:
    """Whether the new revision is generic; see the module docstring (Story 4.19)."""
    if generic is not None:
        return bool(generic)
    if origin is RevisionOrigin.REGENERATED and traceability.route_tuple_id is not None:
        return False
    return draft.generic


def _grounding(
    draft: DraftView,
    enrollment: StoredEnrollment,
    revision: RevisionInput,
    now: datetime,
    waived_rules: frozenset[str] = frozenset(),
) -> RulesResult:
    """The deterministic grounding rules over ``revision`` (Story 4.13); ``LookupError`` when
    the context can't be built. ``waived_rules`` (Story 4.19) is a generic revision's waiver.
    See the module docstring."""
    campaign = setup_queries.campaign(enrollment.campaign_id)
    if campaign is None:
        raise LookupError("the Draft's Campaign is unknown")
    try:  # Story 11.7: the Enrollment's bound Sequence version, never the current one
        steps = _bound_briefs(enrollment)
    except LookupError:
        raise LookupError("the Draft's Sequence is unknown") from None
    if draft.step_no is None or not FIRST_STEP <= draft.step_no <= len(steps):
        raise LookupError("the Draft's Step has no brief")
    segment = setup_queries.segment_by_id(campaign.segment_id)
    if segment is None:
        raise LookupError("the Draft's Segment is unknown")
    settings = setup_queries.footer_settings()
    lead_id = enrollment.lead_id
    linked = research_queries.signal_evidence_ids(lead_id)
    stored = research_queries.signals(lead_id)
    weights = {kind.key: kind.weight for kind in segment.definition.signal_types}
    strongest = strongest_event(
        (
            EventSignal(
                signal.id,
                signal.signal_type,
                signal.tier,
                weights[signal.signal_type],
                signal.event_date,
                signal.first_seen_at,
            )
            for signal in stored
            if signal.signal_class == SignalClass.EVENT and signal.signal_type in weights
        ),
        now,
    )
    context = RulesContext(
        step=draft.step_no,
        contact_details=tuple(
            value for value in (settings.sender_signature, settings.privacy_notice_url) if value
        ),
        evidence_kinds=MappingProxyType(
            {row.id: row.type for row in research_queries.evidence_history(lead_id)}
        ),
        signals=tuple(
            LeadSignal(
                id=signal.id,
                signal_type=signal.signal_type,
                signal_class=SignalClass(signal.signal_class),
                tier=signal.tier,
                score_only=bool(signal.score_only),
                evidence_ids=linked.get(signal.id, ()),
            )
            for signal in stored
        ),
        strongest_event_id=None if strongest is None else strongest.signal_id,
        allowed_facts=frozenset(
            (found.fact_id, found.version)
            for found in playbook_queries.allowed_facts(campaign.segment_id, campaign.market_id)
        ),
        earlier_steps=_earlier_steps(draft, enrollment.id),
        waived_rules=frozenset(waived_rules),
    )
    return check_grounding(revision, steps[draft.step_no - 1], context)


def _prepare(
    draft: DraftView,
    subject: str,
    body: str,
    traceability: Traceability,
    now: datetime,
    claims: tuple[Claim, ...] | None = None,
    generic: bool = False,
) -> _Prepared:
    """``_append``'s checks and computation, before any write; ``generic`` (Story 4.19) is the
    resolved flag of the new revision. See the module docstring."""
    _check_text(subject, body)
    if draft.enrollment_id is None:
        raise LookupError("the Draft has no Enrollment")
    enrollment = sequencing_queries.enrollment(draft.enrollment_id)
    if enrollment is None:
        raise LookupError("the Draft's Enrollment is unknown")
    contact = research_queries.contact(enrollment.contact_id)
    if contact is None:
        raise LookupError("the Draft's Contact is unknown")
    if contact.erased_at is not None or not contact.email:
        raise ValueError("the Draft's Contact has no email address")
    market = None if contact.market_id is None else setup_queries.market_by_id(contact.market_id)
    if market is None:
        raise LookupError("the Draft's Contact has no stored Market")
    source = next(
        (
            record.source_kind
            for record in research_queries.provenance(contact.id)
            if record.subject == ProvenanceSubject.CONTACT
        ),
        None,
    )
    footer_text = render.footer(setup_queries.footer_settings(), market.definition, source)
    if not footer_text.strip():
        raise ValueError("the footer is blank")
    composed = render.compose(_Text(subject, body), _Footer(footer_text), contact.email)
    digest = render.text_hash(
        composed.to_address, enrollment.sending_inbox_id, composed.subject, composed.body
    )
    grounding = _grounding(
        draft,
        enrollment,
        RevisionInput(
            subject=subject,
            body=body,
            claims=claims,
            cited_signal_id=traceability.cited_signal_id,
            evidence_ids=traceability.evidence_ids,
            fact_versions=traceability.fact_versions,
        ),
        now,
        generic_waiver(generic),
    )
    return _Prepared(market.id, footer_text, digest, grounding, generic)


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


def _bound_briefs(enrollment: StoredEnrollment) -> tuple[StepBrief, ...]:
    """The Step briefs of the Sequence version the Enrollment is bound to (Story 11.7); see
    the module docstring."""
    if enrollment.sequence_version_id is not None:
        bound = setup_queries.sequence_version(enrollment.sequence_version_id)
        if bound is None or bound.sequence_id != enrollment.sequence_id:
            raise LookupError("request_steps: the Enrollment's Sequence version isn't stored")
        return bound.definition.steps
    sequence = setup_queries.sequence_by_id(enrollment.sequence_id)
    if sequence is None or sequence.version != enrollment.sequence_version:
        raise LookupError("request_steps: the Enrollment's Sequence version isn't stored")
    return sequence.definition.steps


class Drafting:
    """Draft slots and their due times; see the module docstring."""

    def __init__(self, clock: Clock, queue: JobQueue | None = None) -> None:
        self._clock = clock
        self._queue = queue

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
        queue = self._queue
        if queue is None:  # Story 4.15: the Step 1 job needs it; refuse before any write
            raise RuntimeError("this Drafting was built without a job queue")
        enrollment = sequencing_queries.enrollment(enrollment_id)
        if enrollment is None:
            raise LookupError("request_steps: the Enrollment is unknown")
        if EnrollmentStatus(enrollment.status) not in LIVE_STATUSES:
            raise ValueError("request_steps: the Enrollment has ended")
        briefs = _bound_briefs(enrollment)
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
        if FIRST_STEP in wanted:  # Story 4.15: draft Step 1 now
            queue.enqueue(
                DRAFT_STEP_JOB,
                scope=enrollment.id,
                discriminator=str(FIRST_STEP),
                payload={
                    VERSION_FIELD: PAYLOAD_VERSION,
                    ENROLLMENT_ID_FIELD: enrollment.id,
                    STEP_FIELD: FIRST_STEP,
                },
                lane=Lane.BULK if urgency is Urgency.BULK else Lane.INTERACTIVE,
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

    def _append(
        self,
        draft: DraftView,
        subject: str,
        body: str,
        origin: RevisionOrigin,
        traceability: Traceability,
        at: datetime,
        prepared: _Prepared | None = None,
        claims: tuple[Claim, ...] | None = None,
        *,
        urgency: Urgency,
        generic: bool | None = None,
    ) -> int:
        """Append a revision of ``draft`` and its grounding result, point the Draft at it and
        enqueue its AI check at ``urgency``; its ``rev``. ``prepared`` is ``_prepare``'s result
        for this text when the caller already has it; ``claims`` are the drafting output's
        claims (``None`` without a claims array); ``generic`` (Story 4.19) as in the module
        docstring. See the module docstring."""
        check_urgency(urgency)
        queue = self._queue
        if queue is None:
            raise RuntimeError("this Drafting was built without a job queue")
        resolved = _resolved_generic(draft, origin, traceability, generic)
        if prepared is None or prepared.generic != resolved:
            prepared = _prepare(draft, subject, body, traceability, at, claims, resolved)
        snapshot_id = footer_snapshots_repo.ensure(prepared.market_id, prepared.footer_text, at)
        rev = (draft.current_rev or 0) + 1
        revisions_repo.insert(
            NewRevision(
                draft_id=draft.id,
                rev=rev,
                subject=subject,
                body=body,
                footer_snapshot_id=snapshot_id,
                evidence_ids=traceability.evidence_ids,
                fact_versions=traceability.fact_versions,
                cited_signal_id=traceability.cited_signal_id,
                origin=str(origin),
                route_tuple_id=traceability.route_tuple_id,
                playbook_sha256=traceability.playbook_sha256,
                playbook_version=traceability.playbook_version,
                prompt_version=traceability.prompt_version,
                instruction=traceability.instruction,
                text_hash=prepared.text_hash,
            ),
            at,
        )
        grounding = prepared.grounding
        grounding_results_repo.insert(
            NewGroundingResult(
                draft_id=draft.id,
                rev=rev,
                status=str(grounding.status),
                flags=tuple(
                    StoredFlag(flag.rule, flag.span, flag.explanation, FlagSource.RULES.value)
                    for flag in grounding.flags
                ),
                rules_version=grounding.rules_version,
                waived_rules=grounding.waived,
            ),
            at,
        )
        drafts_repo.set_current_rev(draft.id, rev, at)
        if resolved != draft.generic:
            drafts_repo.set_generic(draft.id, resolved, at)
        queue.enqueue(
            CHECK_GROUNDING_JOB,
            scope=draft.id,
            discriminator=str(rev),
            payload={VERSION_FIELD: PAYLOAD_VERSION, DRAFT_ID_FIELD: draft.id, REV_FIELD: rev},
            lane=Lane.BULK if urgency is Urgency.BULK else Lane.INTERACTIVE,
        )
        return rev

    @command
    def revise(self, draft_id: str, subject: str, body: str) -> int:
        """Append the Operator's edit of a ``ready`` Draft; the new ``rev``. See the module
        docstring."""
        if not isinstance(draft_id, str):
            raise TypeError("revise takes the Draft id as str")
        chosen_subject, chosen_body = _checked_text(subject, body)
        draft = drafting_queries.draft(draft_id)
        if draft is None:
            raise LookupError("revise: the Draft is unknown")
        if draft.status is not DraftStatus.READY:
            raise ValueError("revise: only a ready Draft can be edited")
        previous = drafting_queries.current_revision(draft)
        if previous is None:
            raise ValueError("revise: the Draft has no text yet")
        return self._append(
            draft,
            chosen_subject,
            chosen_body,
            RevisionOrigin.OPERATOR_EDIT,
            _copied(previous),
            self._clock.now(),
            urgency=Urgency.INTERACTIVE,
        )

    @command
    def recompose_ready(self, n: FooterChanged) -> None:
        """``FooterChanged``'s subscriber: recompose the Market's ``ready`` Drafts; see the
        module docstring."""
        now = self._clock.now()
        for draft in drafting_queries.ready_with_text():
            if draft.enrollment_id is None:
                continue
            enrollment = sequencing_queries.enrollment(draft.enrollment_id)
            contact = (
                None if enrollment is None else research_queries.contact(enrollment.contact_id)
            )
            if contact is None or contact.market_id != n.market_id:
                continue
            previous = drafting_queries.current_revision(draft)
            if previous is None:
                continue
            try:
                prepared = _prepare(
                    draft,
                    previous.subject,
                    previous.body,
                    _copied(previous),
                    now,
                    generic=draft.generic,
                )
            except (ValueError, LookupError, InvalidMarkets) as error:  # before any write
                log.warning(
                    RECOMPOSE_SKIPPED,
                    extra={"draft_id": draft.id, "error_type": type(error).__name__},
                )
                continue
            if prepared.text_hash == previous.text_hash:
                continue  # the same message: a repeat is a no-op
            self._append(
                draft,
                previous.subject,
                previous.body,
                RevisionOrigin.FOOTER_CHANGE,
                _copied(previous),
                now,
                prepared,
                urgency=Urgency.BULK,
            )


# Erasure (Story 3.13) -------------------------------------------------------------------------

# What drafting's erase_pii does to each of its tables with a personal-data column.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "footer_snapshots": TableErasure(
            Coverage.NOT_HELD,
            "a Market footer's text: the Sender's name and Nextron's postal address, never a "
            "Contact's data",
        ),
        "draft_revisions": TableErasure(
            Coverage.TOMBSTONED,
            "the text of every revision of the Contact's Drafts becomes [erased]; the rows and "
            "their hashes stay (Story 4.12)",
        ),
        "grounding_results": TableErasure(
            Coverage.TOMBSTONED,
            "each flag's span (a quote of Draft text) and each AI flag's explanation become "
            "[erased]; the rule, the rules' explanations, status and rows stay (Stories 4.13, "
            "4.14)",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``drafting``'s ``ContactErased`` subscriber (Story 3.13, AD-17; Story 4.12): tombstone
    the text of every revision of the Contact's Drafts, and every non-empty span of their
    grounding results (Story 4.13) (``ERASURE``)."""
    draft_ids = [
        draft.id
        for enrollment in sequencing_queries.enrollments_of_contact(n.contact_id)
        for draft in drafting_queries.drafts_of(enrollment.id)
    ]
    revisions_repo.tombstone(draft_ids)
    grounding_results_repo.tombstone(draft_ids)
