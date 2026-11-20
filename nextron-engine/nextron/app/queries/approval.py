"""Read API of the ``approval`` service (AD-3, AD-16; Stories 5.1 and 6.9).

Import rule: ``nextron.domain``, ``nextron.db.repos.approval`` and the other read APIs
(``nextron.app.queries.*``) only; never ``nextron.app.compliance`` (c6), so live Eligibility is
passed in as a callable.

**The due queue (FR-26).** ``due_queue(now, eligibility) -> DueQueue`` reads, and never writes,
the Drafts due for review:

- **Candidates.** ``ready`` Drafts with a current revision (``reviewable``). A cold Draft (kind
  ``step`` or ``recontact``) also needs ``due_at`` set, ``due_at <= now + 24 h`` (inclusive) and
  an ``active`` Enrollment. A responsive Draft (``reply``, ``rebook``, ``channel``) needs
  neither; it is resolved to its Contact and Lead through its ``enrollment_id``, and one that
  can't be is held back with ``no_contact_link``.
- **Live Eligibility (AD-16).** ``eligibility(contact_id) -> Decision`` is called once per
  Contact per build (the dashboard passes ``Compliance.evaluate_eligibility``, which itself asks
  ``assert_contactable``). A Draft whose Decision isn't eligible is held back with the
  Decision's reasons; one whose call raises ``LookupError`` with ``contact_unknown``, and one
  whose call raises ``ValueError`` (a Contact whose Lead was merged) with ``not_checkable``,
  fail closed. Scoring's stored Eligibility is never read.
- **Groups, in order** (``GROUP_ORDER``): ``replies`` (responsive kinds, oldest ``created_at``
  first); ``tier_a_step_1`` (kind ``step``, ``step_no`` 1, Lead Tier A: by the age in days of
  the Lead's newest live Event, ``domain.signals.age_days`` over its current Event-class Signals
  that ``is_live``, ascending, a Lead without one after every Lead with one, then by Lead Score
  descending); ``other_step_1`` (every other ``step_no`` 1 Draft: a non-A ``step`` or a
  ``recontact``, by Lead Score descending); ``follow_ups`` (``step_no`` above 1, by Lead Score
  descending). A missing score sorts last; the final tie-breaks are ``due_at`` (none last) and
  then the Draft id.
- **Result.** ``DueQueue(groups, held_back, total, next_due_at)``: every group (empty ones
  included) with its ordered ``QueueCard``s (``cards`` flattens them in queue order); the
  ``HeldBack`` cards with their ``HeldReason(code, text)``s, by Draft id; ``total`` N, the cards
  in the groups (held-back ones not counted); ``next_due_at``, the earliest ``due_at`` of any
  ``ready`` Draft that has one (any Enrollment status), else ``None``.
- **A card** (``QueueCard``) holds the Lead's name, Segment name, the Contact's Market code, the
  Lead's Tier and Score; the Contact's name, role and Address Class; ``message``, the exact
  ``domain.render.compose`` output of the current revision with its footer snapshot (``None``
  without one); and ``blocked``, ``render.incomplete_notice`` when the Market's footer check
  fails. A Segment or Market that no longer decodes reads as missing.

**The current approval (Story 6.9).**

- ``ApprovalView(id, draft_id, rev, approved_at, approved_hash)``: one approval of a Draft's
  revision, with the ``text_hash`` the Operator approved.
- ``current_approval(draft_id) -> ApprovalView | None``: the Draft's current approval, or
  ``None``. **A fail-closed seam (Story 6.9):** it answers ``None`` for every Draft until Story
  5.4 stores approvals, so the send gate's ``approval_live`` and ``hash_matches`` fail and no
  message can be sent.

**Binding and flags (Story 5.3, AD-6).** A card also holds ``enrollment_id`` (the Draft's),
``inbox_id`` (its Enrollment's bound ``sending_inbox_id``, ``None`` without an Enrollment),
``generic`` and ``stale`` (the Draft's columns), ``grounding_status``: the current revision's
``GroundingStatus`` (``pending`` when it has no grounding row), and ``shown_hash``:
``render.text_hash(message.to_address, inbox_id, message.subject, message.body)`` of exactly the
composed message shown (the inputs ``Sending.enqueue`` hashes), or ``""`` when the Draft can't be
bound: no composed message, an empty To address, no Enrollment, or a kind ``enqueue`` doesn't
take (it takes only ``step`` and ``recontact``, ``BINDABLE_KINDS``).

**A card's details (Story 5.3).** ``card_details(card) -> CardDetails`` reads, for one card only
(the page calls it for the focused card), what lies behind the message, from the revision
``card.rev``:

- ``evidence``: one ``CardEvidence`` per id of the revision's ``evidence_ids``, in order, from
  ``queries.research.evidence_history(lead_id)`` (superseded and retracted rows included, with
  ``state`` ``superseded`` or ``retracted``, retracted winning); an id not found gives a row
  with ``found`` false;
- ``facts``: one ``CardFact`` per ``(fact_id, version)`` of the revision's ``fact_versions``,
  that version from ``queries.playbook.fact`` (``retracted`` when the version is); a fact or
  version not found, or a fact that no longer decodes, gives a row with ``found`` false;
- ``playbook_version``: the revision's (``None`` when unrecorded);
- ``earlier_steps``: for a ``step`` Draft with ``step_no`` above 1 and an Enrollment
  (``has_earlier_steps(card)``, which the page also uses), the
  Enrollment's ``sent`` OutboundMessages with a lower ``step_no``, by ``step_no``
  (``EarlierStep(step_no, subject, body, sent_at)``); else ``()``;
- ``grounding``: ``queries.drafting.grounding_result(draft_id, rev)`` (``None`` without one);
- ``revision``: the ``RevisionView`` itself.

A card without a revision gets empty details.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import scoring as scoring_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.approval import drafts as drafts_repo
from nextron.domain import render
from nextron.domain.company_facts import InvalidCompanyFacts
from nextron.domain.draft import RESPONSIVE_KINDS, DraftKind
from nextron.domain.eligibility import Decision
from nextron.domain.grounding_rules import GroundingStatus
from nextron.domain.markets import InvalidMarkets
from nextron.domain.scoring import Tier
from nextron.domain.segments import InvalidSegments, SignalClass
from nextron.domain.signals import age_days, is_live

HORIZON: Final = timedelta(hours=24)
FIRST_STEP: Final = 1
BINDABLE_KINDS: Final = (DraftKind.STEP, DraftKind.RECONTACT)  # what Sending.enqueue takes

EVIDENCE_LIVE: Final = "live"
EVIDENCE_SUPERSEDED: Final = "superseded"
EVIDENCE_RETRACTED: Final = "retracted"

GROUP_REPLIES: Final = "replies"
GROUP_TIER_A: Final = "tier_a_step_1"
GROUP_OTHER_STEP_1: Final = "other_step_1"
GROUP_FOLLOW_UPS: Final = "follow_ups"
GROUP_ORDER: Final = (GROUP_REPLIES, GROUP_TIER_A, GROUP_OTHER_STEP_1, GROUP_FOLLOW_UPS)
GROUP_LABELS: Final = {
    GROUP_REPLIES: "Replies",
    GROUP_TIER_A: "Tier A Step 1",
    GROUP_OTHER_STEP_1: "Other Step 1 and recontact",
    GROUP_FOLLOW_UPS: "Follow-ups",
}

NO_CONTACT_LINK: Final = "no_contact_link"
CONTACT_UNKNOWN: Final = "contact_unknown"
NOT_ELIGIBLE: Final = "not_eligible"
NOT_CHECKABLE: Final = "not_checkable"
REASON_TEXTS: Final = {
    NO_CONTACT_LINK: "No Contact is linked to this Draft",
    CONTACT_UNKNOWN: "The Contact is unknown",
    NOT_ELIGIBLE: "Not eligible",
    NOT_CHECKABLE: "Eligibility couldn't be checked",
}

_NO_TIME: Final = datetime.min.replace(tzinfo=UTC)

type Eligibility = Callable[[str], Decision]


@dataclass(frozen=True, slots=True)
class QueueCard:
    """One Draft as the queue shows it; see the module docstring."""

    draft_id: str
    kind: DraftKind
    step_no: int | None
    rev: int | None
    due_at: datetime | None
    created_at: datetime
    lead_id: str | None = None
    lead_name: str | None = None
    segment_name: str | None = None
    market_code: str | None = None
    tier: Tier | None = None
    lead_score: int | None = None
    contact_id: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
    address_class: str | None = None
    message: render.Composed | None = None
    blocked: str | None = None
    enrollment_id: str | None = None
    inbox_id: str | None = None
    shown_hash: str = ""
    generic: bool = False
    stale: bool = False
    grounding_status: GroundingStatus = GroundingStatus.PENDING


@dataclass(frozen=True, slots=True)
class CardEvidence:
    """One Evidence id a revision cites; ``found`` false when no row of the Lead has it."""

    id: str
    found: bool
    type: str | None = None
    quote: str | None = field(default=None, repr=False)
    fact: str | None = None
    value: str | None = field(default=None, repr=False)
    sought: str | None = None
    number: int | None = None
    pages_checked: tuple[str, ...] = ()
    source_url: str | None = None
    retrieved_at: datetime | None = None
    ai_extracted: bool = False
    state: str = EVIDENCE_LIVE


@dataclass(frozen=True, slots=True)
class CardFact:
    """One ``(fact_id, version)`` a revision relies on; ``found`` false when it is unknown."""

    fact_id: str
    version: int
    found: bool
    type: str | None = None
    wording: str | None = None
    source_url: str | None = None
    retracted: bool = False


@dataclass(frozen=True, slots=True)
class EarlierStep:
    """One earlier Step already sent on the card's Enrollment."""

    step_no: int | None
    subject: str = field(repr=False)
    body: str = field(repr=False)
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class CardDetails:
    """What lies behind one card's message; see the module docstring."""

    evidence: tuple[CardEvidence, ...] = ()
    facts: tuple[CardFact, ...] = ()
    playbook_version: str | None = None
    earlier_steps: tuple[EarlierStep, ...] = ()
    grounding: drafting_queries.GroundingResultView | None = None
    revision: drafting_queries.RevisionView | None = None


@dataclass(frozen=True, slots=True)
class HeldReason:
    """Why a Draft is held back: a code and its Operator-facing text."""

    code: str
    text: str


@dataclass(frozen=True, slots=True)
class HeldBack:
    """A due Draft that failed live Eligibility, with its reasons."""

    card: QueueCard
    reasons: tuple[HeldReason, ...]


@dataclass(frozen=True, slots=True)
class QueueGroup:
    """One queue group: its key, its label and its cards in queue order."""

    key: str
    label: str
    cards: tuple[QueueCard, ...]


@dataclass(frozen=True, slots=True)
class DueQueue:
    """The due queue; see the module docstring."""

    groups: tuple[QueueGroup, ...]
    held_back: tuple[HeldBack, ...]
    total: int
    next_due_at: datetime | None

    @property
    def cards(self) -> tuple[QueueCard, ...]:
        """Every card of the groups, flattened in queue order."""
        return tuple(card for group in self.groups for card in group.cards)


def _reason(code: str) -> HeldReason:
    return HeldReason(code, REASON_TEXTS[code])


@dataclass(slots=True)
class _Reader:
    """One build's lookups, each read at most once."""

    now: datetime
    eligibility: Eligibility
    settings: render.FooterSettings
    decisions: dict[str, Decision | str] = field(default_factory=dict)
    contacts: dict[str, research_queries.StoredContact | None] = field(default_factory=dict)
    leads: dict[str, discovery_queries.StoredLead | None] = field(default_factory=dict)
    scores: dict[str, scoring_queries.StoredScore | None] = field(default_factory=dict)
    segments: dict[str, str | None] = field(default_factory=dict)
    markets: dict[str, setup_queries.SeededMarket | None] = field(default_factory=dict)
    event_ages: dict[str, int | None] = field(default_factory=dict)
    inboxes: dict[str, str | None] = field(default_factory=dict)

    def decision(self, contact_id: str) -> Decision | str:
        """The Contact's live Decision, or the held-back reason code when the call raised:
        ``contact_unknown`` for ``LookupError``, ``not_checkable`` for ``ValueError``."""
        if contact_id not in self.decisions:
            try:
                self.decisions[contact_id] = self.eligibility(contact_id)
            except LookupError:
                self.decisions[contact_id] = CONTACT_UNKNOWN
            except ValueError:  # e.g. the Contact's Lead was merged: fail closed
                self.decisions[contact_id] = NOT_CHECKABLE
        return self.decisions[contact_id]

    def contact(self, contact_id: str) -> research_queries.StoredContact | None:
        if contact_id not in self.contacts:
            self.contacts[contact_id] = research_queries.contact(contact_id)
        return self.contacts[contact_id]

    def lead(self, lead_id: str) -> discovery_queries.StoredLead | None:
        if lead_id not in self.leads:
            self.leads[lead_id] = discovery_queries.lead(lead_id)
        return self.leads[lead_id]

    def score(self, lead_id: str) -> scoring_queries.StoredScore | None:
        if lead_id not in self.scores:
            self.scores[lead_id] = scoring_queries.stored(lead_id)
        return self.scores[lead_id]

    def segment_name(self, segment_id: str) -> str | None:
        if segment_id not in self.segments:
            try:
                seeded = setup_queries.segment_by_id(segment_id)
            except InvalidSegments:  # unreadable: shown as missing
                seeded = None
            self.segments[segment_id] = None if seeded is None else seeded.definition.name
        return self.segments[segment_id]

    def market(self, market_id: str) -> setup_queries.SeededMarket | None:
        if market_id not in self.markets:
            try:
                self.markets[market_id] = setup_queries.market_by_id(market_id)
            except InvalidMarkets:  # unreadable: no Market code and no footer check
                self.markets[market_id] = None
        return self.markets[market_id]

    def inbox_of(self, enrollment_id: str) -> str | None:
        """The Enrollment's bound Sending Inbox id, or ``None`` when it is unknown."""
        if enrollment_id not in self.inboxes:
            found = sequencing_queries.enrollment(enrollment_id)
            self.inboxes[enrollment_id] = None if found is None else found.sending_inbox_id
        return self.inboxes[enrollment_id]

    def newest_live_event_age(self, lead_id: str) -> int | None:
        """The age in days of the Lead's newest live Event, or ``None`` without one."""
        if lead_id not in self.event_ages:
            ages = []
            for signal in research_queries.signals(lead_id):
                if signal.signal_class != SignalClass.EVENT:
                    continue
                age = age_days(signal.event_date, signal.first_seen_at, self.now)
                try:
                    live = is_live(SignalClass.EVENT, signal.tier, age)
                except (TypeError, ValueError):  # a tier outside 1..3 never counts
                    live = False
                if live:
                    ages.append(age)
            self.event_ages[lead_id] = min(ages) if ages else None
        return self.event_ages[lead_id]


def _card(
    reader: _Reader, candidate: drafts_repo.QueueCandidate, draft: drafting_queries.DraftView
) -> QueueCard:
    grounding = _grounding_status(draft)
    bare = QueueCard(
        draft_id=draft.id,
        kind=draft.kind,
        step_no=draft.step_no,
        rev=draft.current_rev,
        due_at=draft.due_at,
        created_at=draft.created_at,
        enrollment_id=draft.enrollment_id,
        generic=draft.generic,
        stale=draft.stale,
        grounding_status=grounding,
    )
    lead_id, contact_id = candidate.lead_id, candidate.contact_id
    if lead_id is None or contact_id is None:
        return bare
    lead = reader.lead(lead_id)
    score = reader.score(lead_id)
    contact = reader.contact(contact_id)
    market = (
        None if contact is None or contact.market_id is None else reader.market(contact.market_id)
    )
    message = None
    current = drafting_queries.current_revision(draft)
    if current is not None:
        footer = drafting_queries.footer_snapshot(current.footer_snapshot_id)
        if footer is not None:
            recipient = "" if contact is None or contact.email is None else contact.email
            message = render.compose(current, footer, recipient)
    blocked = None
    if market is not None:
        check = render.footer_complete(reader.settings, market.definition)
        if not check.complete:
            blocked = render.incomplete_notice(check)
    inbox_id = None if draft.enrollment_id is None else reader.inbox_of(draft.enrollment_id)
    return QueueCard(
        draft_id=draft.id,
        kind=draft.kind,
        step_no=draft.step_no,
        rev=draft.current_rev,
        due_at=draft.due_at,
        created_at=draft.created_at,
        lead_id=lead_id,
        lead_name=None if lead is None else lead.name,
        segment_name=None if lead is None else reader.segment_name(lead.segment_id),
        market_code=None if market is None else market.code,
        tier=None if score is None else score.tier,
        lead_score=None if score is None else score.lead,
        contact_id=contact_id,
        contact_name=None if contact is None else contact.name,
        contact_role=None if contact is None else contact.role,
        address_class=None if contact is None else contact.address_class,
        message=message,
        blocked=blocked,
        enrollment_id=draft.enrollment_id,
        inbox_id=inbox_id,
        shown_hash=_shown_hash(draft.kind, message, inbox_id),
        generic=draft.generic,
        stale=draft.stale,
        grounding_status=grounding,
    )


def _grounding_status(draft: drafting_queries.DraftView) -> GroundingStatus:
    """The current revision's grounding status; ``pending`` without a result for it."""
    if draft.current_rev is None:
        return GroundingStatus.PENDING
    result = drafting_queries.grounding_result(draft.id, draft.current_rev)
    if result is None:
        return GroundingStatus.PENDING
    return result.status


def _shown_hash(kind: DraftKind, message: render.Composed | None, inbox_id: str | None) -> str:
    """The ``text_hash`` of the message shown, or ``""`` when it can't be bound."""
    if kind not in BINDABLE_KINDS or message is None or not inbox_id:
        return ""
    if not message.to_address:  # enqueue refuses a Contact without an address
        return ""
    return render.text_hash(message.to_address, inbox_id, message.subject, message.body)


def _evidence_row(
    evidence_id: str, stored: research_queries.StoredEvidence | None
) -> CardEvidence:
    if stored is None:
        return CardEvidence(id=evidence_id, found=False)
    if stored.retracted_at is not None:
        state = EVIDENCE_RETRACTED
    elif stored.superseded_by is not None:
        state = EVIDENCE_SUPERSEDED
    else:
        state = EVIDENCE_LIVE
    return CardEvidence(
        id=evidence_id,
        found=True,
        type=stored.type,
        quote=stored.quote,
        fact=stored.fact,
        value=stored.value,
        sought=stored.sought,
        number=stored.number,
        pages_checked=tuple(stored.pages_checked),
        source_url=stored.source_url,
        retrieved_at=stored.retrieved_at,
        ai_extracted=stored.extracted_by.startswith("ai:"),
        state=state,
    )


def _fact_row(fact_id: str, version: int) -> CardFact:
    try:
        history = playbook_queries.fact(fact_id)
    except InvalidCompanyFacts:  # a stored fact that no longer decodes reads as not found
        history = None
    found = (
        None
        if history is None
        else next((each for each in history.versions if each.version == version), None)
    )
    if found is None:
        return CardFact(fact_id=fact_id, version=version, found=False)
    return CardFact(
        fact_id=fact_id,
        version=version,
        found=True,
        type=str(found.content.type),
        wording=found.content.wording,
        source_url=found.content.source_url,
        retracted=found.retracted_at is not None,
    )


def has_earlier_steps(card: QueueCard) -> bool:
    """Whether ``card`` can have earlier Steps: a ``step`` Draft with ``step_no`` above 1 and an
    Enrollment."""
    return (
        card.kind == DraftKind.STEP
        and card.step_no is not None
        and card.step_no > FIRST_STEP
        and card.enrollment_id is not None
    )


def _earlier_steps(card: QueueCard) -> tuple[EarlierStep, ...]:
    if not has_earlier_steps(card) or card.step_no is None or card.enrollment_id is None:
        return ()
    step_no = card.step_no
    return tuple(
        EarlierStep(
            step_no=sent.step_no, subject=sent.subject, body=sent.body, sent_at=sent.sent_at
        )
        for sent in sending_queries.sent_of_enrollment(card.enrollment_id)
        if sent.step_no is not None and sent.step_no < step_no
    )


def card_details(card: QueueCard) -> CardDetails:
    """The Evidence, facts, Playbook version, earlier Steps and grounding result behind
    ``card``'s revision; see the module docstring."""
    revision = None if card.rev is None else drafting_queries.revision(card.draft_id, card.rev)
    if revision is None:
        return CardDetails()
    history = (
        {}
        if card.lead_id is None or not revision.evidence_ids
        else {each.id: each for each in research_queries.evidence_history(card.lead_id)}
    )
    return CardDetails(
        evidence=tuple(
            _evidence_row(evidence_id, history.get(evidence_id))
            for evidence_id in revision.evidence_ids
        ),
        facts=tuple(_fact_row(fact_id, version) for fact_id, version in revision.fact_versions),
        playbook_version=revision.playbook_version,
        earlier_steps=_earlier_steps(card),
        grounding=drafting_queries.grounding_result(card.draft_id, revision.rev),
        revision=revision,
    )


def _group(card: QueueCard) -> str:
    if card.kind in RESPONSIVE_KINDS:
        return GROUP_REPLIES
    if card.step_no == FIRST_STEP:
        if card.kind == DraftKind.STEP and card.tier == Tier.A:
            return GROUP_TIER_A
        return GROUP_OTHER_STEP_1
    return GROUP_FOLLOW_UPS


def _tail(card: QueueCard) -> tuple[bool, datetime, str]:
    return (card.due_at is None, card.due_at or _NO_TIME, card.draft_id)


def _by_score(card: QueueCard) -> tuple[bool, int]:
    return (card.lead_score is None, -(card.lead_score or 0))


def _ordered(key: str, cards: list[QueueCard], reader: _Reader) -> tuple[QueueCard, ...]:
    if key == GROUP_REPLIES:
        return tuple(sorted(cards, key=lambda card: (card.created_at, *_tail(card))))
    if key == GROUP_TIER_A:

        def tier_a(card: QueueCard) -> tuple[object, ...]:
            age = None if card.lead_id is None else reader.newest_live_event_age(card.lead_id)
            return (age is None, age or 0, *_by_score(card), *_tail(card))

        return tuple(sorted(cards, key=tier_a))  # type: ignore[arg-type]
    return tuple(sorted(cards, key=lambda card: (*_by_score(card), *_tail(card))))


def _held_reasons(reader: _Reader, card: QueueCard) -> tuple[HeldReason, ...]:
    """Why ``card`` is held back; ``()`` when its Contact is eligible now."""
    if card.contact_id is None:
        return (_reason(NO_CONTACT_LINK),)
    decision = reader.decision(card.contact_id)
    if isinstance(decision, str):
        return (_reason(decision),)
    if decision.eligible:
        return ()
    found = tuple(HeldReason(str(reason.kind), reason.text) for reason in decision.reasons)
    return found or (_reason(NOT_ELIGIBLE),)


def due_queue(now: datetime, eligibility: Eligibility) -> DueQueue:
    """The Drafts due for review at ``now``, grouped and ordered; see the module docstring."""
    reader = _Reader(now, eligibility, setup_queries.footer_settings())
    grouped: dict[str, list[QueueCard]] = {key: [] for key in GROUP_ORDER}
    held: list[HeldBack] = []
    for candidate in drafts_repo.queue_candidates(now + HORIZON):
        draft = drafting_queries.draft(candidate.id)
        if draft is None or not draft.reviewable:
            continue
        card = _card(reader, candidate, draft)
        reasons = _held_reasons(reader, card)
        if reasons:
            held.append(HeldBack(card, reasons))
        else:
            grouped[_group(card)].append(card)
    groups = tuple(
        QueueGroup(key, GROUP_LABELS[key], _ordered(key, grouped[key], reader))
        for key in GROUP_ORDER
    )
    return DueQueue(
        groups=groups,
        held_back=tuple(held),
        total=sum(len(group.cards) for group in groups),
        next_due_at=drafts_repo.earliest_ready_due(),
    )


# The current approval (Story 6.9) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalView:
    """One approval of a Draft's revision."""

    id: str
    draft_id: str
    rev: int
    approved_at: datetime
    approved_hash: str


def current_approval(draft_id: str) -> ApprovalView | None:
    """The Draft's current approval, or ``None``; ``None`` always until Story 5.4."""
    return None
