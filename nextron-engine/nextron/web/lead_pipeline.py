"""The Lead page's Pipeline panel (Story 9.1, FR-45, FR-37, FR-63, AD-4, AD-13): the view for
``create_app``'s three POSTs and the panel inside ``lead.html``. This module reads nothing itself
and writes nothing; it imports from ``nextron.app`` only ``nextron.app.queries.*``.

**The panel** (``lead_pipeline.html``, ``<section id="lead-pipeline">``) renders from
``panel_context``: the stage's label, then one form per Operator move
(``queries.pipeline.lead_pipeline``'s ``moves``, which is ``operator_moves(stage)``; never an
automatic stage): "Audit proposed", "Audit won", "Build proposed", "Won", "Close lost", "Close
disqualified" and "Reopen" (``MOVE_LABELS``), each posting its ``move`` to
``/leads/{lead_id}/stage``. While ``call_booked`` is a legal move, or the Lead is at
``call_booked``, the call form ("Book call", or "Rebook call" at ``call_booked``) posts
``contact_id`` (a select of the Lead's Contacts that aren't erased), ``date``, ``time`` and
``timezone`` (blank: the Contact's ``tz``) to ``/leads/{lead_id}/call``; the pending booking
shows (only while the Lead is at ``call_booked``) as "Call booked for <YYYY-MM-DD HH:MM> <zone> (<YYYY-MM-DD HH:MM> UTC)". The deal form
posts ``deal_value`` (whole US dollars) and ``notes`` to ``/leads/{lead_id}/deal``; the panel
shows "Deal value: $12,000" ("Deal value: —" when unset) and the notes. Every stored text (the
notes, a Contact's name) renders through ``|plain`` inside an element carrying ``hx-disable``;
each form sits outside them (HTMX reads the form's fields with ``FormData``, the notes'
``<textarea>`` included). "Opted out: no outreach on any channel" (``OPTED_OUT``) shows when the
Lead's latest close names a suppression entry whose source is an opt-out
(``OPT_OUT_SOURCES``), whether the Lead is still closed or was reopened.

**The forms.** Each parser takes the POST's fields (``csrf_token`` aside) and raises
``MalformedForm`` (400 "Request refused: malformed form") for a missing, unknown or repeated
field:

- ``submitted_move(fields)``: exactly ``move``, a ``PipelineStage`` value or ``reopen``
  (anything else is malformed);
- ``submitted_call(fields)``: exactly ``contact_id``, ``date``, ``time`` and ``timezone`` as a
  ``CallForm`` (stripped);
- ``submitted_deal(fields)``: exactly ``deal_value`` and ``notes`` as a ``DealForm``.

``call_details(form, contact)`` turns a ``CallForm`` and the chosen Contact (``None`` when it
isn't the Lead's) into the call's UTC time and zone name, raising ``StageRefused``: a blank
Contact, date or time ``booking_incomplete``; no such Contact ``bad_contact``; no zone typed and
none on the Contact ``no_timezone``; then ``check_timezone`` and ``call_time``.

**Notices.** ``refused(reason)`` is the alert of a refusal (``REFUSALS``: "This stage is set
automatically", "That move isn't available at this stage", "Enter the call's date, time and
Contact", "Enter a valid timezone, such as Europe/London", "Enter the deal value in whole US
dollars", "Notes can be at most 5,000 characters", ...); ``moved``, ``booked``, ``reopened``
and ``deal_saved`` are the status lines of what was done. A refused call or deal form renders
again with what was typed (``panel_context``'s ``typed_call`` and ``typed_deal``: the chosen
Contact, date, time and timezone, or the deal value and notes; autoescaped, the notes through
``|plain`` inside ``hx-disable``); the notice itself never repeats it. "Close lost", "Close
disqualified" and "Won" ask for confirmation first (``hx-confirm``, ``CONFIRMS``).

``dollars(micros)`` is "$12,000" (whole dollars, thousands separated) or "—" for ``None``;
``deal_line(micros)`` is "Deal value: <that>", used by this panel and the Pipeline board.

**The Call Outcome form (Story 9.4, FR-69).** ``lead_call_outcome.html`` (included by the panel,
``<div id="call-outcome">``) renders ``panel_context``'s ``outcome_form`` (an ``OutcomeView``)
for ``LeadPipeline.outcome_booking``: "Call Outcome" for a pending booking, or a prefilled
"Correct Call Outcome" for a recorded one, with the call's time ("Call on <local> <zone>
(<utc> UTC)"). It posts the hidden ``booking`` id, ``outcome`` (Held, No-show), ``qualified``
and ``audit_proposed`` (Yes, No), ``reason``, ``audit_price`` (whole US dollars),
``price_reaction`` (Accepted, Pushed back, Not discussed), ``objections`` (one checkbox per
``ObjectionType``, ``OBJECTION_LABELS``), ``next_step`` and ``next_step_on`` (a date) to
``/leads/{lead_id}/outcome``. Typed texts render through ``|plain`` inside ``hx-disable``.
``submitted_outcome(fields)`` reads it: exactly one ``booking`` (a ULID) and at most one of
each other field but ``objections`` (each a distinct ``ObjectionType`` code); a missing field
is blank; an unknown or repeated field, a ``booking`` that isn't a ULID, or a value that isn't
one of the field's codes (blank aside) is ``MalformedForm``. A refusal
(``call_outcomes.OutcomeRefused``) renders the form again with what was typed, the posted
``booking`` id and the error next to its field (``outcome_error``: ``OUTCOME_ERRORS``), and the
panel alert "The Call Outcome wasn't saved."; the saved notices are "Call Outcome recorded.",
"Call Outcome corrected. The stage wasn't changed; move it by hand if needed." and "Nothing
changed.".
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from nextron.app.queries.compliance import ListedEntry
from nextron.app.queries.pipeline import LeadPipeline, StoredBooking
from nextron.app.queries.research import StoredContact
from nextron.domain.call_outcomes import (
    ANSWERS,
    AUDIT_PRICE,
    AUDIT_PROPOSED,
    NEXT_STEP,
    NEXT_STEP_ON,
    OBJECTIONS,
    OUTCOME,
    PRICE_REACTION,
    QUALIFIED,
    REASON,
    NO,
    YES,
    CallResult,
    Outcome,
    OutcomeForm,
    OutcomeRefusal,
    OutcomeRefused,
    PriceReaction,
)
from nextron.domain.ids import is_ulid
from nextron.domain.manual_stages import (
    MICROS_PER_DOLLAR,
    StageRefusal,
    StageRefused,
    call_time,
    check_timezone,
)
from nextron.domain.pipeline_stage import REOPEN, PipelineStage
from nextron.domain.reply_classes import ObjectionType
from nextron.domain.suppression_entry import OPT_OUT_SOURCES
from nextron.web.credentials_page import Notice, Role
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm
from nextron.web.leads_page import STAGE_LABELS, stage_label

PANEL_TEMPLATE: Final = "lead_pipeline.html"
STAGE_ROUTE: Final = "/leads/{lead_id}/stage"
CALL_ROUTE: Final = "/leads/{lead_id}/call"
DEAL_ROUTE: Final = "/leads/{lead_id}/deal"
MOVE_FIELD: Final = "move"
CONTACT_FIELD: Final = "contact_id"
DATE_FIELD: Final = "date"
TIME_FIELD: Final = "time"
TIMEZONE_FIELD: Final = "timezone"
DEAL_FIELD: Final = "deal_value"
NOTES_FIELD: Final = "notes"
CALL_FIELDS: Final = frozenset({CONTACT_FIELD, DATE_FIELD, TIME_FIELD, TIMEZONE_FIELD})
DEAL_FIELDS: Final = frozenset({DEAL_FIELD, NOTES_FIELD})

NO_VALUE: Final = "—"
DEAL_LINE: Final = "Deal value: {value}"
OPTED_OUT: Final = "Opted out: no outreach on any channel"
BOOK_LABEL: Final = "Book call"
REBOOK_LABEL: Final = "Rebook call"
BOOKING_LINE: Final = "Call booked for {local} {zone} ({utc} UTC)"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M"
UNNAMED_CONTACT: Final = "Unnamed Contact"
NO_NOTES: Final = "No notes yet."

MOVE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        PipelineStage.AUDIT_PROPOSED: "Audit proposed",
        PipelineStage.AUDIT_WON: "Audit won",
        PipelineStage.BUILD_PROPOSED: "Build proposed",
        PipelineStage.WON: "Won",
        PipelineStage.LOST: "Close lost",
        PipelineStage.DISQUALIFIED: "Close disqualified",
        REOPEN: "Reopen",
    }
)
CONFIRMS: Final[Mapping[str, str]] = MappingProxyType(
    {
        PipelineStage.WON: "Mark this Lead as won?",
        PipelineStage.LOST: "Close this Lead as lost?",
        PipelineStage.DISQUALIFIED: "Close this Lead as disqualified?",
    }
)
REFUSALS: Final[Mapping[StageRefusal, str]] = MappingProxyType(
    {
        StageRefusal.AUTOMATIC: "This stage is set automatically",
        StageRefusal.ILLEGAL: "That move isn't available at this stage",
        StageRefusal.BOOKING_INCOMPLETE: "Enter the call's date, time and Contact",
        StageRefusal.BAD_DATE_TIME: "Enter the call's date, time and Contact",
        StageRefusal.BAD_CONTACT: "Choose one of this Lead's Contacts",
        StageRefusal.NO_TIMEZONE: "Enter the call's timezone, such as Europe/London",
        StageRefusal.BAD_TIMEZONE: "Enter a valid timezone, such as Europe/London",
        StageRefusal.ALREADY_CLOSED: "This Lead is already closed",
        StageRefusal.NOT_REOPENABLE: "This Lead can't be reopened",
        StageRefusal.BAD_DEAL_VALUE: "Enter the deal value in whole US dollars",
        StageRefusal.NOTES_TOO_LONG: "Notes can be at most 5,000 characters",
    }
)
MOVED: Final = "Stage set to {label}."
BOOKED: Final = "Call booked."
REBOOKED: Final = "Call rebooked."
REOPENED: Final = "Lead reopened at {label}."
DEAL_SAVED: Final = "Deal details saved."

# Story 9.4: the Call Outcome form
OUTCOME_ROUTE: Final = "/leads/{lead_id}/outcome"
OUTCOME_ANCHOR: Final = "call-outcome"
# The HX-Trigger event a saved outcome answers with; the layout's notification area re-fetches
# on it, so the due line clears at once.
OUTCOME_SAVED_EVENT: Final = "call-outcome-saved"
BOOKING_FIELD: Final = "booking"
OUTCOME_TEXT_FIELDS: Final = frozenset(
    {OUTCOME, QUALIFIED, REASON, AUDIT_PROPOSED, AUDIT_PRICE, PRICE_REACTION, NEXT_STEP, NEXT_STEP_ON}
)
OUTCOME_FIELDS: Final = OUTCOME_TEXT_FIELDS | {BOOKING_FIELD, OBJECTIONS}
CODES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        OUTCOME: frozenset(CallResult),
        QUALIFIED: frozenset(ANSWERS),
        AUDIT_PROPOSED: frozenset(ANSWERS),
        PRICE_REACTION: frozenset(PriceReaction),
    }
)
RESULT_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {CallResult.HELD: "Held", CallResult.NO_SHOW: "No-show"}
)
ANSWER_LABELS: Final[Mapping[str, str]] = MappingProxyType({YES: "Yes", NO: "No"})
REACTION_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        PriceReaction.ACCEPTED: "Accepted",
        PriceReaction.PUSHED_BACK: "Pushed back",
        PriceReaction.NOT_DISCUSSED: "Not discussed",
    }
)
OBJECTION_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        ObjectionType.SEND_INFORMATION: "Send me some information",
        ObjectionType.NO_BUDGET: "No budget",
        ObjectionType.USES_OTHER_TOOL: "We already use another tool",
    }
)
MISSING_ERRORS: Final[Mapping[str, str]] = MappingProxyType(
    {
        OUTCOME: "Choose whether the call was held or a no-show",
        QUALIFIED: "Choose whether the Lead is qualified",
        REASON: "Enter why the Lead isn't qualified",
        AUDIT_PROPOSED: "Choose whether the audit was proposed",
        AUDIT_PRICE: "Enter the audit price in whole US dollars",
        PRICE_REACTION: "Choose the price reaction",
    }
)
OUTCOME_ERRORS: Final[Mapping[OutcomeRefusal, str]] = MappingProxyType(
    {
        OutcomeRefusal.BAD_PRICE: "Enter the audit price in whole US dollars",
        OutcomeRefusal.BAD_DATE: "Enter the next step's date as YYYY-MM-DD",
        OutcomeRefusal.TOO_LONG: "At most 500 characters",
    }
)
OUTCOME_HEADING: Final = "Call Outcome"
CORRECT_HEADING: Final = "Correct Call Outcome"
OUTCOME_SUBMIT: Final = "Save Call Outcome"
CORRECT_SUBMIT: Final = "Save correction"
CALL_LINE: Final = "Call on {local} {zone} ({utc} UTC)"
OUTCOME_REFUSED: Final = "The Call Outcome wasn't saved."
OUTCOME_SAVED: Final[Mapping[str, str]] = MappingProxyType(
    {
        "recorded": "Call Outcome recorded.",
        "corrected": (
            "Call Outcome corrected. The stage wasn't changed; move it by hand if needed."
        ),
        "unchanged": "Nothing changed.",
    }
)
DUE_LINE: Final = "Call Outcome due: {name}"


@dataclass(frozen=True, slots=True)
class CallForm:
    """The call form's fields, stripped."""

    contact_id: str
    date: str
    time: str
    timezone: str


@dataclass(frozen=True, slots=True)
class DealForm:
    """The deal form's fields, as typed."""

    deal_value: str
    notes: str


@dataclass(frozen=True, slots=True)
class MoveButton:
    value: str
    label: str
    confirm: str | None = None


@dataclass(frozen=True, slots=True)
class ContactOption:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class Choice:
    """One option of a select (or one checkbox)."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class OutcomeView:
    """What ``lead_call_outcome.html`` renders."""

    booking_id: str
    heading: str
    submit: str
    call_line: str
    form: OutcomeForm
    errors: Mapping[str, str]
    results: tuple[Choice, ...]
    qualified: tuple[Choice, ...]
    audit_proposed: tuple[Choice, ...]
    reactions: tuple[Choice, ...]
    objections: tuple[Choice, ...]


@dataclass(frozen=True, slots=True)
class DueItem:
    """One "Call Outcome due: <Lead name>" line of the notification area."""

    text: str
    href: str


def _fields(fields: FormFields, names: frozenset[str]) -> dict[str, str]:
    if set(fields) - {CSRF_FIELD} != names:
        raise MalformedForm(WRONG_FIELDS)
    found = {}
    for name in names:
        values = fields[name]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        found[name] = values[0]
    return found


def submitted_move(fields: FormFields) -> str:
    """The ``move`` a stage form posts: a ``PipelineStage`` value or ``reopen``."""
    move = _fields(fields, frozenset({MOVE_FIELD}))[MOVE_FIELD]
    if move != REOPEN and move not in STAGE_LABELS:
        raise MalformedForm(WRONG_FIELDS)
    return move


def submitted_call(fields: FormFields) -> CallForm:
    """The call form's fields."""
    found = _fields(fields, CALL_FIELDS)
    return CallForm(
        contact_id=found[CONTACT_FIELD].strip(),
        date=found[DATE_FIELD].strip(),
        time=found[TIME_FIELD].strip(),
        timezone=found[TIMEZONE_FIELD].strip(),
    )


def submitted_deal(fields: FormFields) -> DealForm:
    """The deal form's fields."""
    found = _fields(fields, DEAL_FIELDS)
    return DealForm(deal_value=found[DEAL_FIELD], notes=found[NOTES_FIELD])


def call_details(form: CallForm, contact: StoredContact | None) -> tuple[datetime, str]:
    """The call's UTC time and zone name; see the module docstring."""
    if not form.contact_id or not form.date or not form.time:
        raise StageRefused(StageRefusal.BOOKING_INCOMPLETE)
    if contact is None or contact.erased_at is not None:
        raise StageRefused(StageRefusal.BAD_CONTACT)
    zone_name = form.timezone or contact.tz or ""
    if not zone_name:
        raise StageRefused(StageRefusal.NO_TIMEZONE)
    zone = check_timezone(zone_name)
    return call_time(form.date, form.time, zone), zone_name


def submitted_outcome(fields: FormFields) -> tuple[str, OutcomeForm]:
    """The Call Outcome form's booking id and fields; see the module docstring."""
    names = set(fields) - {CSRF_FIELD}
    if BOOKING_FIELD not in names or not names <= OUTCOME_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    found: dict[str, str] = {}
    for name in names - {OBJECTIONS}:
        values = fields[name]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        found[name] = values[0]
    booking_id = found.pop(BOOKING_FIELD)
    if not is_ulid(booking_id):
        raise MalformedForm(WRONG_FIELDS)
    for name, codes in CODES.items():
        if found.get(name, "") not in codes | {""}:
            raise MalformedForm(WRONG_FIELDS)
    objections = tuple(fields.get(OBJECTIONS, ()))
    if not set(objections) <= set(ObjectionType):
        raise MalformedForm(WRONG_FIELDS)
    if len(set(objections)) != len(objections):
        raise MalformedForm(REPEATED_FIELD)
    return booking_id, OutcomeForm(**found, objections=objections)


def outcome_error(refused: OutcomeRefused) -> str:
    """The message shown next to the refused field."""
    if refused.reason is OutcomeRefusal.MISSING:
        return MISSING_ERRORS[refused.field]
    return OUTCOME_ERRORS[refused.reason]


def outcome_refused() -> Notice:
    return Notice(Role.ALERT, OUTCOME_REFUSED)


def outcome_saved(saved: str) -> Notice:
    return Notice(Role.STATUS, OUTCOME_SAVED[saved])


def typed_from(outcome: Outcome) -> OutcomeForm:
    """The form prefilled with a stored outcome (to correct it)."""

    def answer(value: bool | None) -> str:
        return "" if value is None else YES if value else NO

    return OutcomeForm(
        outcome=str(outcome.outcome),
        qualified=answer(outcome.qualified),
        reason=outcome.reason or "",
        audit_proposed=answer(outcome.audit_proposed),
        audit_price=(
            "" if outcome.audit_price is None else str(outcome.audit_price // MICROS_PER_DOLLAR)
        ),
        price_reaction="" if outcome.price_reaction is None else str(outcome.price_reaction),
        objections=tuple(str(each) for each in outcome.objections or ()),
        next_step=outcome.next_step or "",
        next_step_on="" if outcome.next_step_on is None else outcome.next_step_on.isoformat(),
    )


def _local_line(template: str, booking: StoredBooking) -> str:
    utc = booking.scheduled_at.astimezone(UTC)
    try:
        local = utc.astimezone(check_timezone(booking.prospect_tz))
    except StageRefused:  # a stored zone this PC no longer knows: show UTC only
        local = utc
    return template.format(
        local=f"{local:{TIME_FORMAT}}", zone=booking.prospect_tz, utc=f"{utc:{TIME_FORMAT}}"
    )


def _choices(labels: Mapping[str, str], chosen: Iterable[str]) -> tuple[Choice, ...]:
    picked = set(chosen)
    return tuple(Choice(str(value), label, str(value) in picked) for value, label in labels.items())


def outcome_view(
    booking: StoredBooking | None,
    typed: OutcomeForm | None = None,
    error: OutcomeRefused | None = None,
    typed_booking_id: str | None = None,
) -> OutcomeView | None:
    """The Call Outcome form for ``booking`` (``None`` without one); see the module docstring.
    A refused form keeps the booking id it posted (``typed_booking_id``), which a stale form
    can hold even when the panel's booking has changed since."""
    if booking is None:
        return None
    recorded = booking.result
    form = typed if typed is not None else typed_from(recorded) if recorded else OutcomeForm()
    return OutcomeView(
        booking_id=typed_booking_id or booking.id,
        heading=OUTCOME_HEADING if recorded is None else CORRECT_HEADING,
        submit=OUTCOME_SUBMIT if recorded is None else CORRECT_SUBMIT,
        call_line=_local_line(CALL_LINE, booking),
        form=form,
        errors={} if error is None else {error.field: outcome_error(error)},
        results=_choices(RESULT_LABELS, (form.outcome,)),
        qualified=_choices(ANSWER_LABELS, (form.qualified,)),
        audit_proposed=_choices(ANSWER_LABELS, (form.audit_proposed,)),
        reactions=_choices(REACTION_LABELS, (form.price_reaction,)),
        objections=_choices(OBJECTION_LABELS, form.objections),
    )


def due_items(due: Iterable[tuple[str, str]]) -> list[DueItem]:
    """The notification area's due lines, one per ``(lead_id, lead_name)``."""
    return [
        DueItem(DUE_LINE.format(name=name), f"/leads/{lead_id}#{OUTCOME_ANCHOR}")
        for lead_id, name in due
    ]


def dollars(micros: int | None) -> str:
    """``micros`` as whole US dollars, "$12,000", or "—" for ``None``."""
    if micros is None:
        return NO_VALUE
    return f"${micros // MICROS_PER_DOLLAR:,}"


def deal_line(micros: int | None) -> str:
    """The panel's (and the board's) "Deal value: ..." line."""
    return DEAL_LINE.format(value=dollars(micros))


def refused(reason: StageRefusal) -> Notice:
    return Notice(Role.ALERT, REFUSALS[reason])


def moved(stage: str) -> Notice:
    return Notice(Role.STATUS, MOVED.format(label=stage_label(stage)))


def booked(*, rebooked: bool) -> Notice:
    return Notice(Role.STATUS, REBOOKED if rebooked else BOOKED)


def reopened(stage: str) -> Notice:
    return Notice(Role.STATUS, REOPENED.format(label=stage_label(stage)))


def deal_saved() -> Notice:
    return Notice(Role.STATUS, DEAL_SAVED)


def opted_out(close_entries: Iterable[ListedEntry]) -> bool:
    """Whether the Lead's latest close came from an opt-out entry."""
    return any(entry.source in OPT_OUT_SOURCES for entry in close_entries)


def _booking_line(found: LeadPipeline) -> str | None:
    booking = found.booking
    if booking is None or found.stage is not PipelineStage.CALL_BOOKED:
        return None
    return _local_line(BOOKING_LINE, booking)


def panel_context(
    lead_id: str,
    found: LeadPipeline,
    contacts: Iterable[StoredContact],
    close_entries: Iterable[ListedEntry],
    notice: Notice | None = None,
    *,
    typed_call: CallForm | None = None,
    typed_deal: DealForm | None = None,
    typed_outcome: OutcomeForm | None = None,
    outcome_refusal: OutcomeRefused | None = None,
    typed_booking_id: str | None = None,
) -> dict[str, object]:
    """What ``lead_pipeline.html`` renders; see the module docstring."""
    can_book = PipelineStage.CALL_BOOKED in found.moves
    rebook = found.stage is PipelineStage.CALL_BOOKED
    chosen = found.booking.contact_id if found.booking is not None else None
    live = [each for each in contacts if each.erased_at is None]
    if chosen is None:
        chosen = next((each.id for each in live if each.is_primary), None)
    if typed_call is not None:  # a refused call form keeps what was typed
        chosen = typed_call.contact_id
    options = [
        ContactOption(
            value=each.id,
            label=each.name or each.email or UNNAMED_CONTACT,
            selected=each.id == chosen,
        )
        for each in live
    ]
    return {
        "pipeline_lead_id": lead_id,
        "pipeline_stage": stage_label(found.stage),
        "pipeline_moves": [
            MoveButton(value=str(move), label=MOVE_LABELS[move], confirm=CONFIRMS.get(move))
            for move in found.moves
            if move in MOVE_LABELS
        ],
        "show_booking": can_book or rebook,
        "booking_label": REBOOK_LABEL if rebook else BOOK_LABEL,
        "booking_line": _booking_line(found),
        "contact_options": options,
        "deal_line": deal_line(found.deal_value),
        "deal_input": (
            typed_deal.deal_value
            if typed_deal is not None
            else "" if found.deal_value is None else str(found.deal_value // MICROS_PER_DOLLAR)
        ),
        "notes": found.notes,
        "notes_input": typed_deal.notes if typed_deal is not None else found.notes or "",
        "call_date": "" if typed_call is None else typed_call.date,
        "call_time": "" if typed_call is None else typed_call.time,
        "call_timezone": "" if typed_call is None else typed_call.timezone,
        "no_notes": NO_NOTES,
        "opted_out": OPTED_OUT if opted_out(close_entries) else None,
        "pipeline_notice": notice,
        "outcome_form": outcome_view(
            found.outcome_booking, typed_outcome, outcome_refusal, typed_booking_id
        ),
    }
