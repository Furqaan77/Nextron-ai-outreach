"""The Approval Queue page (``GET /queue``; FR-26, AD-6, AD-13, AD-16; Stories 5.1, 5.3).

``queue_context(queue, card_param, now, started, zone_name, *, draft_param=None,
details_of=None)`` turns ``nextron.app.queries.approval.DueQueue`` into what
``templates/queue.html`` shows:

- ``groups``: each group's label and count (``GroupRow``), in queue order;
- ``card``: the focused card (``CardRow``), card ``k`` of the flattened group order, where ``k``
  is ``focused_index(cards, card_param, draft_param)``: the card whose Draft id is the ``draft``
  query parameter when one of the cards has it (Story 5.3: the stable target), else
  ``card_index(card_param, N)``: the ``card`` query parameter, 1-based, default 1, clamped to
  ``1..N``, and 1 for a text that isn't a whole number; ``None`` when N is 0;
- ``position``: ``"k of N"``; ``previous_href`` and ``next_href``: plain GET links to the
  neighbouring cards by Draft id, ``/queue?draft=<id>`` (``None`` at either end);
- ``held_back``: each held-back Draft's name (the Lead's, else the Contact's, else
  ``"Unknown Lead"``) and its reasons' texts (``HeldRow``);
- ``elapsed``: the session's time since it started, ``"<H>h <MM>m"`` (``elapsed_text``), or
  ``None`` when the start is unknown;
- the empty state, when N is 0: ``"Queue clear"`` plus ``next_due``, the earliest due time of a
  ``ready`` Draft in the Sender's timezone (``"YYYY-MM-DD HH:MM <zone>"``; UTC when the setting
  is unset or unreadable), or, when ``next_due`` is ``None``, ``"Nothing scheduled"``.

**What lies behind the card (Story 5.3).** ``details_of(card)`` (the route passes
``queries.approval.card_details``) is called for the focused card only, and ``card_row`` adds:

- ``rev`` and ``shown_hash``: the hidden binding fields (``""`` when absent);
- ``grounding`` and ``grounding_flags``: ``leads_page.grounding_of`` of the revision and its
  result (``pending`` without one); ``failing`` when it is ``fail``;
- ``badges``: the badge row, the grounding badge first, then ``generic`` and ``stale`` when those
  Draft flags are set;
- ``subject_segments`` and ``body_segments``: the shown subject and body as ``Segment(text,
  marked)`` runs (``highlight``): for a ``fail`` result, the first occurrence of each non-empty
  flag span, in flag order, in the subject, else in the body, is marked; a span whose
  occurrence overlaps one already marked is skipped;
- ``approve_disabled`` and ``approve_notices``: approval is disabled when grounding is ``fail``
  ("Edit this Draft to remove the failing claims before approving") or ``pending``
  ("Grounding check pending"), the card has no ``shown_hash`` ("This Draft can't be approved
  yet"), or the card is ``blocked`` (its footer notice joins the notices); otherwise the page
  renders the enabled Approve button (Story 5.4);
- ``evidence`` (``EvidenceRow``s: type label, text, the ``fact: value`` line of a Quote, the
  pages an Absence checked, the source URL, the retrieval time, "AI-extracted" and
  "superseded"/"retracted"; "Evidence not found" with the id when missing), ``facts``
  (``FactRow``s: wording, type, source URL, "retracted"; "Fact not found" when missing),
  ``playbook_version`` ("—" when unrecorded) and ``earlier_steps`` (``EarlierStepRow``s:
  ``Step <n>``, subject, the send date in the Sender's timezone and the body), with
  ``has_earlier_steps`` from ``queries.approval.has_earlier_steps`` (a Step Draft above Step 1
  with an Enrollment; the page then says "No earlier Step sent yet" when there are none).

Every value is plain text; the template autoescapes it inside an ``hx-disable`` container and
renders every source URL through ``safe_link``.

**Approve (Story 5.4).** ``queue_context(..., notices=())`` also gives ``notices`` (the decision's
outcome texts, shown above the card; when the ``draft`` named isn't a due card, e.g. approved
already or held back, the first is prefixed "The Draft you approved: ", so it doesn't read as about the card shown)
and ``approve_action``, ``/queue/approve?card=<k>`` for the
focused card ``k`` (after an approval the page focuses card ``k`` again: the next one). The
template puts the hidden ``draft_id``, ``rev`` and ``shown_hash`` and ``csrf_field()`` in a
``<form method="post" hx-post=... class="queue-approve-form">`` outside the ``hx-disable``
article, with the enabled ``Approve`` submit button whenever ``approve_disabled`` is false.
``submitted_approval(fields) -> (draft_id, rev, shown_hash)`` reads that form: exactly those
fields (plus ``csrf_token``), each once, at most ``MAX_FIELD_LENGTH`` characters, ``rev`` 1 to 9
digits; anything else raises ``MalformedForm`` (400).

**Edit then approve (Story 5.6).** ``card_row`` also gives:

- ``editable``: the Draft is ``ready`` or ``approved`` and the card has a message and a
  ``shown_hash``; the page then renders the editor, a native ``<details class="queue-edit">``
  holding a POST form to ``edit_action`` (``/queue/edit?card=<k>``) with the hidden
  ``draft_id``, ``rev`` and ``shown_hash``, an ``<input name="subject">`` and a
  ``<textarea name="body">`` holding ``edit_subject`` and ``edit_body`` (the current revision's
  own subject and body), and ``footer_text`` (the signature, footer and UK privacy line) shown
  read-only below them;
- the pending approval's state: when ``pending_rev`` is the card's ``rev`` and grounding is
  ``pending``, a "Recheck pending" badge and approve notice (approval stays disabled); when
  ``pending_rev`` is older than ``rev``, the ``stale`` badge and ``pending_notice`` "This Draft
  changed since you opened it" (the card shows the newest text);
- an ``approved`` Draft's approval is disabled with "Already approved".

``submitted_edit(fields) -> (draft_id, rev, shown_hash, subject, body)`` reads the edit form:
exactly those fields (plus ``csrf_token``), each once; ``draft_id``, ``rev`` and
``shown_hash`` as in ``submitted_approval``; ``subject`` at most ``MAX_EDIT_SUBJECT`` and
``body`` at most ``MAX_EDIT_BODY`` characters (looser than drafting's own 200 and 10,000, which
answer with a message instead of a 400). The body's line breaks (``\r\n`` or ``\r``, as a
browser sends a textarea) become ``\n``.

``queue_context(..., focus=None, approved=(), submitted_prefix=SUBMITTED_PREFIX)``: ``focus``
is a card to show when ``draft_param`` names no due card (the Draft just edited, or one from
the approved list): it is shown with ``position`` "Outside the due queue" and no neighbour
links, and the notices are not prefixed. ``approved`` (``ApprovedUnsent`` rows) becomes
``approved_rows`` (``ApprovedRow``: name, Step, OM status, ``/queue?draft=<id>``), the
"Approved, not yet sent" list. ``submitted_prefix`` is the prefix of a refusal's first notice
when the Draft named is no longer a due card.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.app.queries.approval import (
    EVIDENCE_RETRACTED,
    EVIDENCE_SUPERSEDED,
    ApprovedUnsent,
    CardDetails,
    CardEvidence,
    CardFact,
    DueQueue,
    EarlierStep,
    QueueCard,
    has_earlier_steps,
)
from nextron.domain.draft import DraftKind, DraftStatus
from nextron.domain.grounding_rules import GroundingStatus
from nextron.domain.render import Composed
from nextron.domain.segments import EvidenceType
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm
from nextron.web.leads_page import (
    EVIDENCE_TYPE_LABELS,
    FAIL_GROUNDING,
    PENDING_GROUNDING,
    GroundingFlagRow,
    grounding_of,
)

TEMPLATE: Final = "queue.html"
QUEUE_PATH: Final = "/queue"
APPROVE_PATH: Final = "/queue/approve"
APPROVE_FIELDS: Final = ("draft_id", "rev", "shown_hash")
EDIT_PATH: Final = "/queue/edit"
EDIT_FIELDS: Final = ("draft_id", "rev", "shown_hash", "subject", "body")
MAX_EDIT_SUBJECT: Final = 1_000  # drafting refuses above 200 with a message
MAX_EDIT_BODY: Final = 40_000  # drafting refuses above 10,000 with a message
CSRF_FIELD: Final = "csrf_token"
MAX_FIELD_LENGTH: Final = 128
REV_PATTERN: Final = re.compile(r"[0-9]{1,9}")  # matched with fullmatch
CARD_PARAM: Final = "card"
DRAFT_PARAM: Final = "draft"
SUBMITTED_PREFIX: Final = "The Draft you approved: "  # a refusal for a Draft no longer due
EDITED_PREFIX: Final = "The Draft you edited: "  # Story 5.6: the same for an edit
OUTSIDE_QUEUE: Final = "Outside the due queue"
ALREADY_APPROVED_NOTICE: Final = "Already approved"
RECHECK_NOTICE: Final = "Recheck pending"
CHANGED_NOTICE: Final = "This Draft changed since you opened it"
NO_OUTBOUND: Final = "no message held"
NO_VALUE: Final = "—"
UNKNOWN_NAME: Final = "Unknown Lead"
NOTHING_SCHEDULED: Final = "Nothing scheduled"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M"
FAIL_NOTICE: Final = "Edit this Draft to remove the failing claims before approving"
PENDING_NOTICE: Final = "Grounding check pending"
UNBOUND_NOTICE: Final = "This Draft can't be approved yet"
EVIDENCE_NOT_FOUND: Final = "Evidence not found"
FACT_NOT_FOUND: Final = "Fact not found"
AI_EXTRACTED: Final = "AI-extracted"
GENERIC_BADGE: Final = "generic"
STALE_BADGE: Final = "stale"
NO_EARLIER_STEP: Final = "No earlier Step sent yet"
STATE_LABELS: Final = {EVIDENCE_SUPERSEDED: "superseded", EVIDENCE_RETRACTED: "retracted"}
KIND_LABELS: Final = {
    DraftKind.STEP: "Sequence Step",
    DraftKind.RECONTACT: "Re-contact",
    DraftKind.REPLY: "Reply",
    DraftKind.REBOOK: "Rebook",
    DraftKind.CHANNEL: "Channel message",
}


@dataclass(frozen=True, slots=True)
class GroupRow:
    """One group header: its label and how many cards it holds."""

    key: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class Segment:
    """A run of the shown subject or body; ``marked`` runs render inside ``<mark>``."""

    text: str
    marked: bool = False


@dataclass(frozen=True, slots=True)
class Badge:
    """One badge of the card's badge row: its CSS classes and its text."""

    classes: str
    text: str


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One cited Evidence id as the card shows it; ``found`` false: "Evidence not found"."""

    evidence_id: str
    found: bool
    type: str = NO_VALUE
    text: str = NO_VALUE
    detail: str | None = None  # a Quote's "fact: value", a Count's sought text
    pages_checked: tuple[str, ...] = ()
    source_url: str | None = None
    retrieved: str = NO_VALUE
    ai_extracted: bool = False
    state: str | None = None  # "superseded" or "retracted"


@dataclass(frozen=True, slots=True)
class FactRow:
    """One fact version relied on; ``found`` false: "Fact not found"."""

    fact_id: str
    version: int
    found: bool
    type: str = NO_VALUE
    wording: str = NO_VALUE
    source_url: str | None = None
    retracted: bool = False


@dataclass(frozen=True, slots=True)
class EarlierStepRow:
    """One earlier Step already sent."""

    step: str
    subject: str
    sent: str
    body: str


@dataclass(frozen=True, slots=True)
class CardRow:
    """The focused card as the page shows it."""

    draft_id: str
    kind: str
    step: str | None
    lead_name: str
    segment: str
    market: str
    tier: str
    score: str
    contact_name: str
    contact_role: str
    address_class: str
    message: Composed | None
    blocked: str | None
    rev: str = ""
    shown_hash: str = ""
    grounding: str = PENDING_GROUNDING
    grounding_flags: tuple[GroundingFlagRow, ...] = ()
    badges: tuple[Badge, ...] = ()
    subject_segments: tuple[Segment, ...] = ()
    body_segments: tuple[Segment, ...] = ()
    approve_disabled: bool = True
    approve_notices: tuple[str, ...] = ()
    evidence: tuple[EvidenceRow, ...] = ()
    facts: tuple[FactRow, ...] = ()
    playbook_version: str = NO_VALUE
    has_earlier_steps: bool = False
    earlier_steps: tuple[EarlierStepRow, ...] = ()
    # Story 5.6: the editor and the pending approval
    editable: bool = False
    edit_subject: str = ""
    edit_body: str = ""
    footer_text: str = ""
    pending_notice: str | None = None

    @property
    def failing(self) -> bool:
        """Whether the revision's grounding result is ``fail``."""
        return self.grounding == FAIL_GROUNDING


@dataclass(frozen=True, slots=True)
class ApprovedRow:
    """One approved Draft whose message isn't sent yet (Story 5.6)."""

    name: str
    step: str | None
    om_status: str
    href: str


@dataclass(frozen=True, slots=True)
class HeldRow:
    """One held-back Draft: a name and the reasons."""

    name: str
    reasons: tuple[str, ...]


def card_index(raw: str | None, total: int) -> int:
    """The 1-based card to show of ``total``; see the module docstring."""
    try:
        wanted = 1 if raw is None else int(raw)
    except ValueError:
        wanted = 1
    return max(1, min(wanted, max(total, 1)))


def elapsed_text(started: datetime, now: datetime) -> str:
    """The time from ``started`` to ``now`` as ``<H>h <MM>m`` (never negative)."""
    minutes = max(0, int((now - started).total_seconds()) // 60)
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m"


def zone_of(name: str | None) -> tuple[tzinfo, str]:
    """The timezone ``name`` and its label; UTC for ``None`` or a name that isn't known."""
    if name:
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError, OSError):  # e.g. a tzdata directory
            pass
    return UTC, "UTC"


def local_text(at: datetime, zone_name: str | None) -> str:
    """``at`` in the timezone ``zone_name`` as ``YYYY-MM-DD HH:MM <zone>``."""
    zone, label = zone_of(zone_name)
    return f"{at.astimezone(zone).strftime(TIME_FORMAT)} {label}"


def _text(value: object) -> str:
    return NO_VALUE if value is None or value == "" else str(value)


def _marks(text: str, spans: Iterable[str], taken: list[tuple[int, int]]) -> list[str]:
    """Mark in ``taken`` the first occurrence in ``text`` of each span; the spans not found."""
    missing = []
    for span in spans:
        start = text.find(span)
        if start < 0:
            missing.append(span)
            continue
        end = start + len(span)
        if any(start < other_end and other_start < end for other_start, other_end in taken):
            continue  # overlaps a span already marked: the later span is skipped
        taken.append((start, end))
    return missing


def _segments(text: str, taken: list[tuple[int, int]]) -> tuple[Segment, ...]:
    found: list[Segment] = []
    cursor = 0
    for start, end in sorted(taken):
        if start > cursor:
            found.append(Segment(text[cursor:start]))
        found.append(Segment(text[start:end], marked=True))
        cursor = end
    if cursor < len(text) or not found:
        found.append(Segment(text[cursor:]))
    return tuple(found)


def highlight(
    subject: str, body: str, spans: Iterable[str]
) -> tuple[tuple[Segment, ...], tuple[Segment, ...]]:
    """The subject and body as ``Segment`` runs with each span marked; see the module
    docstring."""
    unique: list[str] = []
    for span in spans:
        if span and span not in unique:
            unique.append(span)
    in_subject: list[tuple[int, int]] = []
    in_body: list[tuple[int, int]] = []
    rest = _marks(subject, unique, in_subject)
    _marks(body, rest, in_body)
    return _segments(subject, in_subject), _segments(body, in_body)


def evidence_row(evidence: CardEvidence, zone_name: str | None) -> EvidenceRow:
    """One cited Evidence id as the card shows it."""
    if not evidence.found:
        return EvidenceRow(evidence_id=evidence.id, found=False)
    kind = evidence.type
    detail = None
    pages: tuple[str, ...] = ()
    if kind == EvidenceType.QUOTE:
        text = _text(evidence.quote)
        if evidence.fact:
            detail = f"{evidence.fact}: {_text(evidence.value)}"
    elif kind == EvidenceType.ABSENCE:
        text = _text(evidence.sought)
        pages = evidence.pages_checked
    elif kind == EvidenceType.COUNT:
        text = _text(evidence.number)
        detail = evidence.sought
    else:
        text = _text(evidence.quote or evidence.sought)
    return EvidenceRow(
        evidence_id=evidence.id,
        found=True,
        type=EVIDENCE_TYPE_LABELS.get(str(kind), _text(kind)),
        text=text,
        detail=detail,
        pages_checked=pages,
        source_url=evidence.source_url,
        retrieved=(
            NO_VALUE
            if evidence.retrieved_at is None
            else local_text(evidence.retrieved_at, zone_name)
        ),
        ai_extracted=evidence.ai_extracted,
        state=STATE_LABELS.get(evidence.state),
    )


def fact_row(fact: CardFact) -> FactRow:
    """One fact version as the card shows it."""
    if not fact.found:
        return FactRow(fact_id=fact.fact_id, version=fact.version, found=False)
    return FactRow(
        fact_id=fact.fact_id,
        version=fact.version,
        found=True,
        type=_text(fact.type),
        wording=_text(fact.wording),
        source_url=fact.source_url,
        retracted=fact.retracted,
    )


def earlier_step_row(step: EarlierStep, zone_name: str | None) -> EarlierStepRow:
    """One earlier sent Step as the card shows it."""
    return EarlierStepRow(
        step=NO_VALUE if step.step_no is None else f"Step {step.step_no}",
        subject=step.subject,
        sent=NO_VALUE if step.sent_at is None else local_text(step.sent_at, zone_name),
        body=step.body,
    )


def _approval_block(
    grounding: str,
    shown_hash: str,
    blocked: str | None,
    *,
    approved: bool = False,
    rechecking: bool = False,
) -> tuple[bool, tuple[str, ...]]:
    notices = []
    if approved:
        notices.append(ALREADY_APPROVED_NOTICE)
    if grounding == FAIL_GROUNDING:
        notices.append(FAIL_NOTICE)
    elif grounding != GroundingStatus.PASS:
        notices.append(RECHECK_NOTICE if rechecking else PENDING_NOTICE)
    if not shown_hash:
        notices.append(UNBOUND_NOTICE)
    if blocked:
        notices.append(blocked)
    return bool(notices), tuple(notices)


def card_row(
    card: QueueCard, details: CardDetails | None = None, zone_name: str | None = None
) -> CardRow:
    """The ``CardRow`` of ``card`` with its ``details``; see the module docstring."""
    found = details if details is not None else CardDetails()
    if found.revision is None:  # no details read: the card's own status, without flags
        grounding, flags = str(card.grounding_status), ()
    else:
        grounding, flags = grounding_of(found.revision, found.grounding)
    badges = [Badge(f"grounding-badge grounding-{grounding}", grounding)]
    rechecking = (
        card.pending_rev is not None
        and card.pending_rev == card.rev
        and grounding == PENDING_GROUNDING
    )
    superseded = (
        card.pending_rev is not None and card.rev is not None and card.pending_rev < card.rev
    )
    if rechecking:
        badges.append(Badge("badge-recheck", RECHECK_NOTICE))
    if card.generic:
        badges.append(Badge(f"badge-{GENERIC_BADGE}", GENERIC_BADGE))
    if card.stale or superseded:
        badges.append(Badge(f"badge-{STALE_BADGE}", STALE_BADGE))
    subject_segments: tuple[Segment, ...] = ()
    body_segments: tuple[Segment, ...] = ()
    if card.message is not None:
        spans = (flag.span for flag in flags) if grounding == FAIL_GROUNDING else ()
        subject_segments, body_segments = highlight(
            card.message.subject, card.message.body, spans
        )
    disabled, notices = _approval_block(
        grounding,
        card.shown_hash,
        card.blocked,
        approved=card.status == DraftStatus.APPROVED,
        rechecking=rechecking,
    )
    return CardRow(
        draft_id=card.draft_id,
        kind=KIND_LABELS.get(card.kind, str(card.kind)),
        step=None if card.step_no is None else f"Step {card.step_no}",
        lead_name=_text(card.lead_name),
        segment=_text(card.segment_name),
        market=_text(None if card.market_code is None else card.market_code.upper()),
        tier=_text(None if card.tier is None else str(card.tier)),
        score=_text(card.lead_score),
        contact_name=_text(card.contact_name),
        contact_role=_text(card.contact_role),
        address_class=_text(card.address_class),
        message=card.message,
        blocked=card.blocked,
        rev="" if card.rev is None else str(card.rev),
        shown_hash=card.shown_hash,
        grounding=grounding,
        grounding_flags=flags,
        badges=tuple(badges),
        subject_segments=subject_segments,
        body_segments=body_segments,
        approve_disabled=disabled,
        approve_notices=notices,
        evidence=tuple(evidence_row(each, zone_name) for each in found.evidence),
        facts=tuple(fact_row(each) for each in found.facts),
        playbook_version=_text(found.playbook_version),
        has_earlier_steps=has_earlier_steps(card),
        earlier_steps=tuple(earlier_step_row(each, zone_name) for each in found.earlier_steps),
        editable=(
            card.status in (DraftStatus.READY, DraftStatus.APPROVED)
            and card.message is not None
            and bool(card.shown_hash)
        ),
        edit_subject=card.edit_subject,
        edit_body=card.edit_body,
        footer_text=card.footer_text,
        pending_notice=CHANGED_NOTICE if superseded else None,
    )


def submitted_approval(fields: FormFields) -> tuple[str, int, str]:
    """The Approve form's ``draft_id``, ``rev`` and ``shown_hash`` (Story 5.4); anything else
    raises ``MalformedForm``: other fields, a repeated one, a ``rev`` that isn't 1 to 9 digits,
    or a value longer than ``MAX_FIELD_LENGTH``."""
    if set(fields) - {CSRF_FIELD} != set(APPROVE_FIELDS):
        raise MalformedForm(WRONG_FIELDS)
    values = []
    for name in APPROVE_FIELDS:
        found = fields[name]
        if len(found) != 1:
            raise MalformedForm(REPEATED_FIELD)
        if len(found[0]) > MAX_FIELD_LENGTH:
            raise MalformedForm(WRONG_FIELDS)
        values.append(found[0])
    draft_id, rev, shown_hash = values
    if not REV_PATTERN.fullmatch(rev):
        raise MalformedForm(WRONG_FIELDS)
    return draft_id, int(rev), shown_hash


def _one_value(fields: FormFields, name: str, limit: int) -> str:
    found = fields[name]
    if len(found) != 1:
        raise MalformedForm(REPEATED_FIELD)
    if len(found[0]) > limit:
        raise MalformedForm(WRONG_FIELDS)
    return found[0]


def submitted_edit(fields: FormFields) -> tuple[str, int, str, str, str]:
    """The edit form's ``draft_id``, ``rev``, ``shown_hash``, ``subject`` and ``body`` (Story
    5.6); see the module docstring. Anything else raises ``MalformedForm``."""
    if set(fields) - {CSRF_FIELD} != set(EDIT_FIELDS):
        raise MalformedForm(WRONG_FIELDS)
    draft_id = _one_value(fields, "draft_id", MAX_FIELD_LENGTH)
    rev = _one_value(fields, "rev", MAX_FIELD_LENGTH)
    shown_hash = _one_value(fields, "shown_hash", MAX_FIELD_LENGTH)
    subject = _one_value(fields, "subject", MAX_EDIT_SUBJECT)
    body = _one_value(fields, "body", MAX_EDIT_BODY)
    if not REV_PATTERN.fullmatch(rev):
        raise MalformedForm(WRONG_FIELDS)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    return draft_id, int(rev), shown_hash, subject, body


def approved_row(row: ApprovedUnsent) -> ApprovedRow:
    """One "Approved, not yet sent" row (Story 5.6)."""
    return ApprovedRow(
        name=row.name or UNKNOWN_NAME,
        step=None if row.step_no is None else f"Step {row.step_no}",
        om_status=NO_OUTBOUND if row.om_status is None else str(row.om_status),
        href=f"{QUEUE_PATH}?{DRAFT_PARAM}={row.draft_id}",
    )


def focused_index(
    cards: tuple[QueueCard, ...], card_param: str | None, draft_param: str | None
) -> int:
    """The 1-based card to show: the one whose Draft id is ``draft_param``, else
    ``card_index(card_param, N)``."""
    if draft_param:
        for index, card in enumerate(cards, start=1):
            if card.draft_id == draft_param:
                return index
    return card_index(card_param, len(cards))


def _href(card: QueueCard) -> str:
    return f"{QUEUE_PATH}?{DRAFT_PARAM}={card.draft_id}"


def queue_context(
    queue: DueQueue,
    card_param: str | None,
    now: datetime,
    started: datetime | None,
    zone_name: str | None,
    *,
    draft_param: str | None = None,
    details_of: Callable[[QueueCard], CardDetails] | None = None,
    notices: tuple[str, ...] = (),
    focus: QueueCard | None = None,
    approved: tuple[ApprovedUnsent, ...] = (),
    submitted_prefix: str = SUBMITTED_PREFIX,
) -> dict[str, object]:
    """What ``queue.html`` renders; see the module docstring."""
    cards = queue.cards
    total = len(cards)
    index = focused_index(cards, card_param, draft_param)
    outside = (
        focus is not None
        and focus.draft_id == draft_param
        and all(card.draft_id != draft_param for card in cards)
    )
    if draft_param and not outside and all(card.draft_id != draft_param for card in cards):
        # the Draft just submitted isn't a due card: the notices are about it, not the card shown
        notices = tuple(
            f"{submitted_prefix}{notice}" if at == 0 else notice
            for at, notice in enumerate(notices)
        )
    focused = None
    shown: QueueCard | None = None
    if outside:
        shown = focus
    elif total:
        shown = cards[index - 1]
    if shown is not None:
        details = None if details_of is None else details_of(shown)
        focused = card_row(shown, details, zone_name)
    if outside:
        position: str | None = OUTSIDE_QUEUE
    else:
        position = f"{index} of {total}" if total else None
    return {
        "groups": tuple(GroupRow(group.key, group.label, len(group.cards)) for group in queue.groups),
        "card": focused,
        "total": total,
        "notices": notices,
        "approve_action": f"{APPROVE_PATH}?{CARD_PARAM}={index}",
        "edit_action": f"{EDIT_PATH}?{CARD_PARAM}={index}",
        "position": position,
        "previous_href": _href(cards[index - 2]) if total and index > 1 and not outside else None,
        "next_href": _href(cards[index]) if total and index < total and not outside else None,
        "approved_rows": tuple(approved_row(row) for row in approved),
        "no_earlier_step": NO_EARLIER_STEP,
        "evidence_not_found": EVIDENCE_NOT_FOUND,
        "fact_not_found": FACT_NOT_FOUND,
        "ai_extracted": AI_EXTRACTED,
        "held_back": tuple(
            HeldRow(
                name=held.card.lead_name or held.card.contact_name or UNKNOWN_NAME,
                reasons=tuple(reason.text for reason in held.reasons),
            )
            for held in queue.held_back
        ),
        "elapsed": None if started is None else elapsed_text(started, now),
        "next_due": (
            None if queue.next_due_at is None else local_text(queue.next_due_at, zone_name)
        ),
        "nothing_scheduled": NOTHING_SCHEDULED,
    }
