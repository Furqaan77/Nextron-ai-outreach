"""The "Ready to draft" page (FR-19 S1, FR-59, UX-DR20, AD-13; Story 4.10): views for
``create_app``'s ``GET /leads/ready`` and ``POST /leads/ready/pick``, which stay thin. Both
declare ``@personal_data(True)`` (a Lead's name can be a sole trader's name, and a Contact's role
is about a person), so both send ``Cache-Control: no-store``. This module reads nothing itself:
it renders what ``nextron.app.scoring.selection.Selection`` gives.

- ``header_text(today)``: "Remaining today: <n> of <N>" (``remaining`` of ``capacity``), or,
  while the inboxes' total cold capacity is 0, "No cold capacity yet: inboxes need 14 days of
  recorded warm-up".
- ``ready_row(row)``: one Lead as the page shows it: its name linked to its page, Segment,
  Market (or "—"), Tier, Lead Score, the Primary Contact's role (or "—"), the live Eligibility
  (``leads_page.eligibility_row``), and the "generic copy" (Tier C) and "below minimum score"
  badges. A badged Lead can still be picked.
- ``submitted_picks(fields) -> tuple[str, ...]``: the checked ``lead`` values in the order
  posted (page order). The form posts nothing else but the CSRF token; there must be 1 to
  ``MAX_PICKS`` (200), each a ULID and each once. Anything else raises ``MalformedForm`` (400).
- ``result_row(outcome, name)``: one picked Lead's result: "Enrolled", or "Refused: <plain
  reason>" (``REASON_TEXTS``, by the ``PickOutcome.reason`` code; an unknown code shows as
  itself).
- ``page_context(today, rows, results=())``: what ``leads_ready.html`` and its list fragment
  ``leads_ready_list.html`` render; the POST answers the fragment, which HTMX swaps in.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from nextron.app.scoring.selection import PickOutcome, ReadyRow, TodayCapacity
from nextron.domain.ids import is_ulid
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import WRONG_FIELDS, FormFields, MalformedForm
from nextron.web.leads_page import EligibilityRow, eligibility_row

PAGE_TEMPLATE: Final = "leads_ready.html"
LIST_TEMPLATE: Final = "leads_ready_list.html"
LEAD_FIELD: Final = "lead"
MAX_PICKS: Final = 200
NO_VALUE: Final = "—"
REMAINING: Final = "Remaining today: {remaining:,} of {capacity:,}"
NO_CAPACITY: Final = "No cold capacity yet: inboxes need 14 days of recorded warm-up"
GENERIC_COPY: Final = "generic copy"
BELOW_MINIMUM: Final = "below minimum score"
ENROLLED: Final = "Enrolled"
REFUSED: Final = "Refused: {reason}"
UNKNOWN_LEAD_NAME: Final = "Unknown Lead"
REASON_TEXTS: Final = {
    # Selection's own reasons
    "unknown_lead": "the Lead is unknown or merged",
    "no_primary_contact": "the Lead has no Primary Contact",
    "no_campaign": "no Campaign for the Lead's Segment and the Contact's Market",
    "over_capacity": "no capacity left today",
    # Sequencing.enroll's refusals (nextron.domain.enrollment.RefusalCode)
    "blocked": "the Contact is suppressed",
    "campaign_not_active": "the Campaign isn't active",
    "missing_tz_or_market": "the Contact has no timezone or Market",
    "not_eligible": "the Contact isn't eligible",
    "campaign_mismatch": "the Campaign doesn't match the Lead's Segment and Market",
    "lead_already_replied": "the Lead is past Contacted",
    "lead_already_enrolled": "the Lead already has a live Sequence",
    "no_active_variant": "the Campaign has no active Variant",
    "no_sending_inbox": "no Sending Inbox is connected",
}


@dataclass(frozen=True, slots=True)
class ReadyView:
    """One Lead of the list as the page shows it."""

    lead_id: str
    href: str
    name: str
    segment: str
    market: str
    tier: str
    lead_score: int
    role: str
    eligibility: EligibilityRow
    badges: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResultView:
    """One picked Lead's result."""

    name: str
    enrolled: bool
    text: str


def header_text(today: TodayCapacity) -> str:
    """The page's capacity line; see the module docstring."""
    if today.total == 0:
        return NO_CAPACITY
    return REMAINING.format(remaining=today.remaining, capacity=today.capacity)


def ready_row(row: ReadyRow) -> ReadyView:
    """One Lead as the page shows it."""
    badges = tuple(
        badge
        for badge, shown in ((GENERIC_COPY, row.generic_copy), (BELOW_MINIMUM, row.below_minimum))
        if shown
    )
    return ReadyView(
        lead_id=row.lead_id,
        href=f"/leads/{row.lead_id}",
        name=row.name,
        segment=row.segment,
        market=row.market or NO_VALUE,
        tier=str(row.tier),
        lead_score=row.lead_score,
        role=row.role or NO_VALUE,
        eligibility=eligibility_row(row.eligibility),
        badges=badges,
    )


def submitted_picks(fields: FormFields) -> tuple[str, ...]:
    """The checked Lead ids in page order; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {LEAD_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = tuple(fields[LEAD_FIELD])
    if not 1 <= len(values) <= MAX_PICKS:
        raise MalformedForm(WRONG_FIELDS)
    if len(set(values)) != len(values) or not all(is_ulid(value) for value in values):
        raise MalformedForm(WRONG_FIELDS)
    return values


def reason_text(reason: str) -> str:
    """The plain text of a pick's reason code; the code itself when unknown."""
    return REASON_TEXTS.get(reason, reason)


def result_row(outcome: PickOutcome, name: str | None) -> ResultView:
    """One picked Lead's result; see the module docstring."""
    shown = name if name is not None else UNKNOWN_LEAD_NAME
    if outcome.enrollment_id is not None:
        return ResultView(shown, True, ENROLLED)
    return ResultView(shown, False, REFUSED.format(reason=reason_text(outcome.reason or "")))


def page_context(
    today: TodayCapacity, rows: Iterable[ReadyRow], results: Iterable[ResultView] = ()
) -> dict[str, object]:
    """What ``leads_ready.html`` and ``leads_ready_list.html`` render."""
    return {
        "header_text": header_text(today),
        "rows": tuple(ready_row(row) for row in rows),
        "results": tuple(results),
    }
