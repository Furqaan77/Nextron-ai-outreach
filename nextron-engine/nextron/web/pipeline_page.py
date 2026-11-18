"""The Pipeline board (Story 9.7; FR-46, FR-45, UX-DR18, AD-3, AD-13): the view for
``create_app``'s ``GET /leads/pipeline`` route (``pipeline_board``), which stays thin. It
declares ``@personal_data(True)`` (a Lead's name can be a sole trader's name), so it sends
``Cache-Control: no-store``. This module reads nothing itself and imports from ``nextron.app``
only ``nextron.app.queries.*``.

**The query string.** ``board_filters(query_items, segments, markets)`` reads it into
``BoardFilters``: ``segment`` (a Segment key), ``market`` (a Market code), ``from`` and ``to``
(``YYYY-MM-DD``, UTC days, both inclusive) and one ``page_<stage>`` per ``PipelineStage`` (1 to
7 ASCII digits worth at least 1), each at most once. A blank one (empty or whitespace only) is
unset. Any other parameter, a repeated one, an unknown Segment key or Market code, a date that
isn't ``YYYY-MM-DD`` or isn't a real day, or a bad page raises ``MalformedForm`` (400 "Request
refused: malformed form"), so a hand-edited URL never shows an unfiltered board that looks
filtered. ``from`` after ``to`` is ``BoardFilters.range_refused``: the route answers 400 with
the page, the alert "End date must be on or after start date" (``role="alert"``), the form
keeping its values, and no groups.

**The page** (``pipeline.html``, from ``board_context``): a native ``method="get"`` filter form
(no ``hx-post``, no ``csrf_field()``: a token must never reach a URL) with a Segment select ("All
Segments", then each by name, its value the key), a Market select ("All Markets", then each by
name, its value the code), "Entered stage from" and "Entered stage to" date inputs, a Filter
button and, while a filter is set, "Clear filters" linking to ``/leads/pipeline``. Then one
``GroupView`` per ``PipelineStage`` in machine order, an empty one included: the stage's label,
"<n> Leads" ("1 Lead"), "Deal value: $12,000" (Story 9.1: the group's total in whole US
dollars, thousands separated; "Deal value: —" when no Lead in it has one), and up to 50 rows (the
name linked to ``/leads/<id>``, the Segment's name or "Unknown Segment", the day it entered the
stage or "—"; a ``not_now`` row also shows "Re-contact: —" until Story 8.8 stores the date), or
"No Leads at this stage." When a group has more than one page, its ``Pager`` ("Page <n> of
<pages>", Previous and Next) links keep every filter and every other group's page, and end with
``#stage-<stage>`` so the browser returns to the group.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Final
from urllib.parse import urlencode

from nextron.app.queries.pipeline import BoardLead, PipelineBoard, StageGroup
from nextron.app.queries.setup import SeededMarket, SeededSegment
from nextron.domain.pipeline_stage import PipelineStage
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, MalformedForm
from nextron.web.lead_pipeline import deal_line
from nextron.web.leads_page import (
    LIST_PATH,
    NO_VALUE,
    PAGE_PATTERN,
    STAGE_LABELS,
    UNKNOWN_SEGMENT,
    Option,
    Pager,
    segment_names,
    segment_options,
)

BOARD_PATH: Final = "/leads/pipeline"
TEMPLATE: Final = "pipeline.html"
SEGMENT_FIELD: Final = "segment"
MARKET_FIELD: Final = "market"
FROM_FIELD: Final = "from"
TO_FIELD: Final = "to"
PAGE_PREFIX: Final = "page_"
PAGE_FIELDS: Final[Mapping[str, PipelineStage]] = MappingProxyType(
    {f"{PAGE_PREFIX}{stage.value}": stage for stage in PipelineStage}
)
FILTER_FIELDS: Final = frozenset({SEGMENT_FIELD, MARKET_FIELD, FROM_FIELD, TO_FIELD, *PAGE_FIELDS})
DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # matched with fullmatch
DATE_FORMAT: Final = "%Y-%m-%d"
RANGE_REFUSED: Final = "End date must be on or after start date"
NO_RECONTACT: Final = f"Re-contact: {NO_VALUE}"  # Story 8.8 adds leads.recontact_on
EMPTY_GROUP: Final = "No Leads at this stage."
ANCHOR_PREFIX: Final = "stage-"


@dataclass(frozen=True, slots=True)
class BoardFilters:
    """The board's query: the Segment (key and id), the Market (code and id), the entry-date
    range, each ``None`` when unset, and the page asked for of each group that named one."""

    segment: str | None = None
    segment_id: str | None = None
    market: str | None = None
    market_id: str | None = None
    entered_from: date | None = None
    entered_to: date | None = None
    pages: Mapping[PipelineStage, int] = field(default_factory=dict)

    @property
    def filtered(self) -> bool:
        """Whether a Segment, a Market or a date is set."""
        return any(
            value is not None
            for value in (self.segment, self.market, self.entered_from, self.entered_to)
        )

    @property
    def range_refused(self) -> bool:
        """Whether ``from`` is after ``to``."""
        return (
            self.entered_from is not None
            and self.entered_to is not None
            and self.entered_from > self.entered_to
        )


@dataclass(frozen=True, slots=True)
class BoardRow:
    """One Lead as the board shows it."""

    href: str
    name: str
    segment: str
    entered: str
    recontact: str | None


@dataclass(frozen=True, slots=True)
class GroupView:
    """One stage's group as the board shows it."""

    stage: str
    anchor: str
    label: str
    count_text: str
    deal_value: str
    recontact: bool
    rows: tuple[BoardRow, ...]
    pager: Pager | None


def _single_values(query_items: Iterable[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in query_items:
        if name not in FILTER_FIELDS:
            raise MalformedForm(WRONG_FIELDS)
        if name in values:
            raise MalformedForm(REPEATED_FIELD)
        values[name] = value
    return values


def _date(value: str) -> date | None:
    if not value.strip():
        return None
    if not DATE_PATTERN.fullmatch(value):
        raise MalformedForm(WRONG_FIELDS)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise MalformedForm(WRONG_FIELDS) from None


def _page(value: str) -> int | None:
    if not value.strip():
        return None
    if not PAGE_PATTERN.fullmatch(value) or int(value) < 1:
        raise MalformedForm(WRONG_FIELDS)
    return int(value)


def _key(value: str) -> str | None:
    return value if value.strip() else None


def board_filters(
    query_items: Iterable[tuple[str, str]],
    segments: Iterable[SeededSegment],
    markets: Iterable[SeededMarket],
) -> BoardFilters:
    """The filters of the query ``query_items`` (name and value pairs, in order), the Segment
    key and Market code resolved against ``segments`` and ``markets``; ``MalformedForm`` for any
    other or repeated parameter, an unknown Segment or Market, a bad date or a bad page."""
    values = _single_values(query_items)
    segment = _key(values.get(SEGMENT_FIELD, ""))
    segment_id = None
    if segment is not None:
        found = [each.id for each in segments if each.key == segment]
        if not found:
            raise MalformedForm(WRONG_FIELDS)
        segment_id = found[0]
    market = _key(values.get(MARKET_FIELD, ""))
    market_id = None
    if market is not None:
        found = [each.id for each in markets if each.code == market]
        if not found:
            raise MalformedForm(WRONG_FIELDS)
        market_id = found[0]
    pages: dict[PipelineStage, int] = {}
    for name, stage in PAGE_FIELDS.items():
        asked = _page(values.get(name, ""))
        if asked is not None:
            pages[stage] = asked
    return BoardFilters(
        segment=segment,
        segment_id=segment_id,
        market=market,
        market_id=market_id,
        entered_from=_date(values.get(FROM_FIELD, "")),
        entered_to=_date(values.get(TO_FIELD, "")),
        pages=pages,
    )


def market_options(markets: Iterable[SeededMarket], selected: str | None) -> tuple[Option, ...]:
    """Each stored Market by name (then code), its value the code."""
    ordered = sorted(markets, key=lambda market: (market.definition.name.casefold(), market.code))
    return tuple(
        Option(market.code, market.definition.name, market.code == selected) for market in ordered
    )


def board_href(filters: BoardFilters, board: PipelineBoard, stage: PipelineStage, page: int) -> str:
    """The board's URL showing ``page`` of ``stage``'s group, with the set filters and every
    other group's shown page (when past the first), URL-encoded, anchored at the group."""
    params: list[tuple[str, str]] = []
    if filters.segment is not None:
        params.append((SEGMENT_FIELD, filters.segment))
    if filters.market is not None:
        params.append((MARKET_FIELD, filters.market))
    if filters.entered_from is not None:
        params.append((FROM_FIELD, filters.entered_from.isoformat()))
    if filters.entered_to is not None:
        params.append((TO_FIELD, filters.entered_to.isoformat()))
    for group in board.groups:
        shown = page if group.stage is stage else group.page
        if shown > 1:
            params.append((f"{PAGE_PREFIX}{group.stage.value}", str(shown)))
    query = urlencode(params)
    anchor = f"#{ANCHOR_PREFIX}{stage.value}"
    return f"{BOARD_PATH}?{query}{anchor}" if query else f"{BOARD_PATH}{anchor}"


def entered_day(at: datetime | None) -> str:
    """``at`` (aware UTC) as ``YYYY-MM-DD``, or "—"."""
    return NO_VALUE if at is None else format(at, DATE_FORMAT)


def count_text(total: int) -> str:
    """A group's count line."""
    return f"{total:,} {'Lead' if total == 1 else 'Leads'}"


def board_row(lead: BoardLead, names: Mapping[str, str]) -> BoardRow:
    """One Lead as the board shows it."""
    return BoardRow(
        href=f"{LIST_PATH}/{lead.id}",
        name=lead.name,
        segment=names.get(lead.segment_id, UNKNOWN_SEGMENT),
        entered=entered_day(lead.entered_at),
        recontact=NO_RECONTACT if lead.pipeline_stage == PipelineStage.NOT_NOW else None,
    )


def group_pager(group: StageGroup, filters: BoardFilters, board: PipelineBoard) -> Pager | None:
    """The group's pager, or ``None`` when it fits on one page."""
    if group.pages <= 1:
        return None
    return Pager(
        page=group.page,
        pages=group.pages,
        previous=(
            board_href(filters, board, group.stage, group.page - 1) if group.page > 1 else None
        ),
        next=(
            board_href(filters, board, group.stage, group.page + 1)
            if group.page < group.pages
            else None
        ),
    )


def group_view(
    group: StageGroup, filters: BoardFilters, board: PipelineBoard, names: Mapping[str, str]
) -> GroupView:
    """One stage's group as the board shows it."""
    return GroupView(
        stage=group.stage.value,
        anchor=f"{ANCHOR_PREFIX}{group.stage.value}",
        label=STAGE_LABELS[group.stage],
        count_text=count_text(group.total),
        deal_value=deal_line(group.deal_value),  # Story 9.1's one formatter
        recontact=group.stage is PipelineStage.NOT_NOW,
        rows=tuple(board_row(lead, names) for lead in group.leads),
        pager=group_pager(group, filters, board),
    )


def board_context(
    filters: BoardFilters,
    board: PipelineBoard | None,
    segments: Iterable[SeededSegment],
    markets: Iterable[SeededMarket],
) -> dict[str, object]:
    """What ``pipeline.html`` renders; ``board`` is ``None`` when the range is refused."""
    stored = tuple(segments)
    names = segment_names(stored)
    groups = (
        ()
        if board is None
        else tuple(group_view(group, filters, board, names) for group in board.groups)
    )
    return {
        "segment_options": segment_options(stored, filters.segment),
        "market_options": market_options(markets, filters.market),
        "entered_from": "" if filters.entered_from is None else filters.entered_from.isoformat(),
        "entered_to": "" if filters.entered_to is None else filters.entered_to.isoformat(),
        "filtered": filters.filtered,
        "range_refused": RANGE_REFUSED if filters.range_refused else None,
        "groups": groups,
        "empty_group": EMPTY_GROUP,
    }
