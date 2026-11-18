"""Read API of the ``pipeline`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.pipeline`` only. All read through the
current unit of work, read-only ones included.

- ``stage(lead_id)``: the Lead's ``PipelineStage``, or ``None`` when no Lead has that id (a
  text that isn't a ULID included).
- ``board(*, segment_id=None, market_id=None, entered_from=None, entered_to=None, pages=None)``
  (Story 9.7): the Pipeline board as a ``PipelineBoard`` of one ``StageGroup`` per
  ``PipelineStage``, in machine order, an empty one included. Each group has its ``stage``, the
  ``total`` of matching Leads that aren't merged, the ``page`` shown, the number of ``pages``
  (``max(1, ceil(total / 50))``), at most ``BOARD_PAGE_SIZE`` (50) ``BoardLead`` rows (newest
  first, by ``created_at`` then ``id``, each with its ``entered_at``: when it entered its
  current stage, or ``None``; a same-stage ``events`` row never counts) and ``deal_value``, the
  total of the matching Leads' deal values in micro-dollars, or ``None`` when none has one
  (Story 9.1). ``segment_id`` keeps the Leads in that Segment; ``market_id`` those with
  at least one Contact in that Market; ``entered_from`` and ``entered_to`` (UTC days, both
  inclusive) those whose entry time falls in that range (a Lead without one is left out while
  either is set). ``pages`` maps a stage to the page asked for (1 when absent); a page past the
  last shows the last. One ``GROUP BY`` counts every group, then one statement per non-empty
  group reads its page. A ``segment_id`` or ``market_id`` that isn't a ``str``, a date that
  isn't a ``date`` (a ``datetime`` included), or a ``pages`` that isn't a mapping of
  ``PipelineStage`` to ``int`` (a ``bool`` included) raises ``TypeError``; a page below 1, or
  ``entered_from`` after ``entered_to``, raises ``ValueError``.
- ``lead_pipeline(lead_id, *, now=None)`` (Story 9.1): the Lead page's Pipeline panel as a
  ``LeadPipeline``: its ``stage``, ``deal_value`` (micro-dollars) and ``notes`` (each ``None``
  when unset), its ``booking`` (the newest pending ``call_outcomes`` row, a ``StoredBooking``,
  or ``None``; a booking with a recorded outcome is no longer pending), its ``moves``
  (``operator_moves(stage)``) and ``close_ref``, the ``cause_ref`` of its latest close
  ``events`` row (``stages.last_close``), whether it is still closed or reopened since, or
  ``None``. Story 9.4: ``outcome_booking`` is the booking the Call Outcome form is for: given
  ``now`` (an aware ``datetime``), the Lead's oldest pending booking scheduled at or before it
  (``bookings.oldest_open_due``), on any stage; otherwise its newest recorded booking
  (``bookings.latest_recorded``), to correct; otherwise ``None`` (always ``None`` without
  ``now``). ``None`` for an unknown Lead (a text that isn't a ULID included).
- ``call_outcomes_due(cutoff)`` (Story 9.4): every pending booking scheduled at or before
  ``cutoff`` whose Lead is at ``call_booked`` and not merged (``bookings.due``), oldest first,
  each a ``DueBooking`` (``booking_id``, ``lead_id``, ``lead_name``). ``cutoff`` must be an
  aware ``datetime`` (``TypeError``/``ValueError``).
- ``call_report_facts()`` (Story 9.6): the Calls report's inputs, ``(facts, accepted)``:
  ``facts`` one ``offer_validation.CallFact`` per unsuperseded ``call_outcomes`` row of an
  engine-sourced Lead (``bookings.report_rows`` with ``offer_validation.engine_creation_causes()``),
  oldest first; ``accepted`` the Leads with a held call among them that have an ``events`` row
  into ``audit_won`` (``stages.leads_reached``), either under its own id or under a Lead merged
  into it (``stages.merged_refs``: a merge moves the bookings to the survivor but leaves the
  merged Lead's ``events`` rows under its own id). It reads only ``call_outcomes`` and ``events``.
- ``export_pii(contact_id, lead_ids, terms) -> {section: tuple[dict, ...]}`` (Story 9.1, the
  subject access export): ``call_bookings``, every ``call_outcomes`` row of the Contact (all
  its columns); ``lead_notes``, one row ``{"lead_id", "notes_excerpts"}`` for each of
  ``lead_ids`` (the Contact's Lead and every Lead merged into it) whose notes mention one of
  the Contact's ``terms`` (``contact_export.excerpts``: only the lines that mention them).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Final

from nextron.db.repos.pipeline import board as board_repo
from nextron.db.repos.pipeline import bookings as bookings_repo
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.repos.pipeline.board import BoardLead
from nextron.db.repos.pipeline.bookings import DueBooking, StoredBooking
from nextron.domain.call_outcomes import OBJECTIONS_SEPARATOR, CallResult, PriceReaction
from nextron.domain.contact_export import MentionTerm, excerpts
from nextron.domain.ids import is_ulid
from nextron.domain.offer_validation import CallFact, engine_creation_causes
from nextron.domain.pipeline_stage import PipelineStage, operator_moves
from nextron.domain.reply_classes import ObjectionType

__all__ = [
    "BOARD_PAGE_SIZE",
    "BoardLead",
    "DueBooking",
    "LeadPipeline",
    "PipelineBoard",
    "StageGroup",
    "StoredBooking",
    "board",
    "call_outcomes_due",
    "call_report_facts",
    "export_pii",
    "lead_pipeline",
    "stage",
]

BOARD_PAGE_SIZE: Final = 50


@dataclass(frozen=True, slots=True)
class StageGroup:
    """One stage's group on the board: its count, the page shown, the number of pages, its
    rows and its deal-value total in micro-dollars (``None`` when no Lead has one)."""

    stage: PipelineStage
    total: int
    page: int
    pages: int
    leads: tuple[BoardLead, ...]
    deal_value: int | None = None


@dataclass(frozen=True, slots=True)
class LeadPipeline:
    """The Lead page's Pipeline panel; see the module docstring."""

    stage: PipelineStage
    deal_value: int | None
    notes: str | None
    booking: StoredBooking | None
    moves: tuple[str, ...]
    close_ref: str | None
    outcome_booking: StoredBooking | None = None  # Story 9.4


@dataclass(frozen=True, slots=True)
class PipelineBoard:
    """The Pipeline board: one ``StageGroup`` per ``PipelineStage``, in machine order."""

    groups: tuple[StageGroup, ...]


def stage(lead_id: str) -> PipelineStage | None:
    """The Pipeline Stage of the Lead ``lead_id``, or ``None``."""
    if not is_ulid(lead_id):
        return None
    stored = stages_repo.stage(lead_id)
    return None if stored is None else PipelineStage(stored)


def _check_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime, not {type(value).__name__}")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be aware")


def lead_pipeline(lead_id: str, *, now: datetime | None = None) -> LeadPipeline | None:
    """The Lead's Pipeline panel facts, or ``None``; see the module docstring."""
    if now is not None:
        _check_aware("now", now)
    current = stage(lead_id)
    deal = stages_repo.deal(lead_id) if current is not None else None
    if current is None or deal is None:
        return None
    pending = bookings_repo.pending(lead_id)
    closing = stages_repo.last_close(lead_id)
    outcome_booking = None
    if now is not None:
        outcome_booking = bookings_repo.oldest_open_due(
            lead_id, now
        ) or bookings_repo.latest_recorded(lead_id)
    return LeadPipeline(
        stage=current,
        deal_value=deal.deal_value,
        notes=deal.notes,
        booking=pending[-1] if pending else None,
        moves=operator_moves(current),
        close_ref=None if closing is None else closing.cause_ref,
        outcome_booking=outcome_booking,
    )


def call_outcomes_due(cutoff: datetime) -> tuple[DueBooking, ...]:
    """The bookings whose Call Outcome is due by ``cutoff``; see the module docstring."""
    _check_aware("cutoff", cutoff)
    return bookings_repo.due(cutoff)


def _fact(row: bookings_repo.ReportRow) -> CallFact:
    codes = () if row.objections is None else row.objections.split(OBJECTIONS_SEPARATOR)
    return CallFact(
        lead_id=row.lead_id,
        outcome=None if row.outcome is None else CallResult(row.outcome),
        qualified=row.qualified,
        audit_proposed=row.audit_proposed,
        audit_price=row.audit_price,
        objections=tuple(ObjectionType(code) for code in codes if code),
        price_reaction=None if row.price_reaction is None else PriceReaction(row.price_reaction),
    )


def call_report_facts() -> tuple[tuple[CallFact, ...], frozenset[str]]:
    """The Calls report's facts and accepted Leads; see the module docstring."""
    facts = tuple(_fact(row) for row in bookings_repo.report_rows(engine_creation_causes()))
    held = {fact.lead_id for fact in facts if fact.outcome is CallResult.HELD}
    merged = stages_repo.merged_refs(held)
    reached = stages_repo.leads_reached(
        held.union(*merged.values()), str(PipelineStage.AUDIT_WON)
    )
    accepted = frozenset(
        lead for lead in held if lead in reached or not reached.isdisjoint(merged.get(lead, ()))
    )
    return facts, accepted


def export_pii(
    contact_id: str, lead_ids: Sequence[str], terms: Sequence[MentionTerm]
) -> dict[str, tuple[dict[str, object], ...]]:
    """The Contact's bookings and the lines of its Leads' notes that mention it."""
    notes: list[dict[str, object]] = []
    for lead_id in lead_ids:
        deal = stages_repo.deal(lead_id) if is_ulid(lead_id) else None
        lines = () if deal is None else excerpts(deal.notes, terms)
        if lines:
            notes.append({"lead_id": lead_id, "notes_excerpts": list(lines)})
    return {"call_bookings": bookings_repo.about(contact_id), "lead_notes": tuple(notes)}


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


def _check_board_arguments(
    segment_id: object,
    market_id: object,
    entered_from: object,
    entered_to: object,
    pages: object,
) -> None:
    for name, value in (("segment_id", segment_id), ("market_id", market_id)):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    for name, value in (("entered_from", entered_from), ("entered_to", entered_to)):
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise TypeError(f"{name} must be a date, not {type(value).__name__}")
    if pages is None:
        return
    if not isinstance(pages, Mapping):
        raise TypeError(f"pages must be a mapping, not {type(pages).__name__}")
    for key, value in pages.items():
        if not isinstance(key, PipelineStage):
            raise TypeError(f"pages keys must be PipelineStage, not {type(key).__name__}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"pages values must be int, not {type(value).__name__}")
        if value < 1:
            raise ValueError("a page must be at least 1")


def board(
    *,
    segment_id: str | None = None,
    market_id: str | None = None,
    entered_from: date | None = None,
    entered_to: date | None = None,
    pages: Mapping[PipelineStage, int] | None = None,
) -> PipelineBoard:
    """The Pipeline board with the filters; see the module docstring."""
    _check_board_arguments(segment_id, market_id, entered_from, entered_to, pages)
    if entered_from is not None and entered_to is not None and entered_from > entered_to:
        raise ValueError("entered_from must be on or before entered_to")
    where = board_repo.BoardFilter(
        segment_id=segment_id,
        market_id=market_id,
        entered_from=None if entered_from is None else _day_start(entered_from),
        entered_before=(  # the last representable day has no upper bound
            None
            if entered_to is None or entered_to == date.max
            else _day_start(entered_to) + timedelta(days=1)
        ),
    )
    asked = {} if pages is None else dict(pages)
    counts = board_repo.stage_counts(where)
    groups = []
    for each in PipelineStage:
        found = counts.get(each.value)
        total = 0 if found is None else found.count
        last = max(1, ceil(total / BOARD_PAGE_SIZE))
        shown = min(asked.get(each, 1), last)
        rows = (
            board_repo.stage_rows(
                each.value,
                where,
                limit=BOARD_PAGE_SIZE,
                offset=(shown - 1) * BOARD_PAGE_SIZE,
            )
            if total
            else ()
        )
        groups.append(
            StageGroup(
                stage=each,
                total=total,
                page=shown,
                pages=last,
                leads=rows,
                deal_value=None if found is None else found.deal_value,
            )
        )
    return PipelineBoard(groups=tuple(groups))
