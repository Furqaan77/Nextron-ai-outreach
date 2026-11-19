"""The Calls report (Story 9.6; FR-69, FR-54, NFR-49, NFR-50, AD-13): the view for
``create_app``'s ``GET /reports/calls`` route (``calls_report_page``), which stays thin. It reads
nothing itself: the route passes it ``nextron.app.analytics.offer.offer_validation()``'s
``OfferReport``.

The route declares ``@personal_data(False)``: the report shows counts, codes and prices only,
never a Call Outcome's ``reason`` or ``next_step``, a Lead's or a Contact's name, so erased data
can't appear. Every page still answers ``Cache-Control: no-store`` (it carries the CSRF token).
It isn't a navigation section (Epic 11's Funnel will host the report as a panel): the Pipeline
board links to it ("Calls report").

**The page** (``calls_report.html``, which includes ``calls_report_panel.html``, the report body
Epic 11 can include on its own, from ``report_context``): the heading "Calls report", the label
"Counts engine-sourced Leads only.", then booked, held and no-shows. Below 10 held calls the line
"Offer validation starts at 10 held calls: <n> of 10 recorded" and nothing else; from 10 the
Offer validation figures: audits proposed ("5 of 12 (42%)"), audits accepted, the proposed
price range ("$2,000–$3,000") and median (whole dollars, cents only when the median isn't
whole; "—" without a proposal), the qualified share, each price reaction with its count (all
three, zeros too), and the top objections (at most 3, by count, none: "No objections recorded");
and, with fewer than 2 audits accepted, the checkpoint "Review the Offer's price and scope
before the next Variant" (``role="alert"``).
"""

from dataclasses import dataclass
from typing import Final

from nextron.domain.manual_stages import MICROS_PER_DOLLAR
from nextron.domain.offer_validation import CHECKPOINT, HELD_CALLS_NEEDED, OfferReport
from nextron.web.lead_pipeline import NO_VALUE, OBJECTION_LABELS, REACTION_LABELS, dollars

REPORT_PATH: Final = "/reports/calls"
TEMPLATE: Final = "calls_report.html"
PANEL_TEMPLATE: Final = "calls_report_panel.html"
ENGINE_SOURCED: Final = "Counts engine-sourced Leads only."
PROGRESS_LINE: Final = "Offer validation starts at {needed} held calls: {held} of {needed} recorded"
NO_OBJECTIONS: Final = "No objections recorded"
MICROS_PER_CENT: Final = MICROS_PER_DOLLAR // 100


@dataclass(frozen=True, slots=True)
class Figure:
    """One labelled figure of the report."""

    label: str
    value: str


def share(n: int, of: int, percent: int) -> str:
    """"5 of 12 (42%)"."""
    return f"{n:,} of {of:,} ({percent}%)"


def money(micros: int | None) -> str:
    """``micros`` in whole US dollars ("$2,500"), with cents only when it isn't whole
    ("$2,500.50"), or "—" for ``None``."""
    if micros is None or not micros % MICROS_PER_DOLLAR:
        return dollars(micros)
    cents = micros // MICROS_PER_CENT
    return f"${cents // 100:,}.{cents % 100:02d}"


def price_range(low: int | None, high: int | None) -> str:
    """"$2,000–$3,000", or "—" without a proposal."""
    if low is None or high is None:
        return NO_VALUE
    return f"{money(low)}–{money(high)}"


def report_context(report: OfferReport) -> dict[str, object]:
    """What ``calls_report_panel.html`` renders; see the module docstring."""
    counts = [
        Figure("Booked", f"{report.booked:,}"),
        Figure("Held", f"{report.held:,}"),
        Figure("No-shows", f"{report.no_shows:,}"),
    ]
    progress = None
    figures: list[Figure] = []
    reactions: list[Figure] = []
    objections: list[Figure] = []
    if report.validating:
        figures = [
            Figure("Audits proposed", share(report.proposed, report.held, report.proposed_percent)),
            Figure("Audits accepted", f"{report.accepted:,}"),
            Figure("Audit price range", price_range(report.price_low, report.price_high)),
            Figure("Median audit price", money(report.price_median)),
            Figure(
                "Qualified", share(report.qualified, report.held, report.qualified_percent)
            ),
        ]
        reactions = [
            Figure(REACTION_LABELS[reaction], f"{count:,}")
            for reaction, count in report.price_reactions
        ]
        objections = [
            Figure(OBJECTION_LABELS[objection], f"{count:,}")
            for objection, count in report.top_objections
        ]
    else:
        progress = PROGRESS_LINE.format(needed=HELD_CALLS_NEEDED, held=report.held)
    return {
        "engine_sourced": ENGINE_SOURCED,
        "counts": counts,
        "progress": progress,
        "validating": report.validating,
        "figures": figures,
        "reactions": reactions,
        "objections": objections,
        "no_objections": NO_OBJECTIONS,
        "checkpoint": CHECKPOINT if report.checkpoint else None,
    }
