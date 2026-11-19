"""Offer validation after the first held calls (Story 9.6; FR-69, FR-54, NFR-49, NFR-50).
Standard library only, plus ``nextron.domain``.

**What counts.** Only engine-sourced Leads: a Lead whose creation ``events`` row (entity
``leads``, no ``from`` status) has a cause in ``engine_creation_causes()``, the
``pipeline_stage.CREATION`` causes of the ``ENGINE_SOURCES`` (today every ``LeadSource``). Each
counted booking is one ``CallFact``: an unsuperseded ``call_outcomes`` row, its ``outcome``
``None`` while it is pending.

**The report.** ``build_report(facts, accepted_lead_ids)`` is an ``OfferReport``:

- ``booked``: every fact; ``held`` and ``no_shows``: those with that recorded outcome (a
  pending booking counts as booked only);
- ``validating``: at least ``HELD_CALLS_NEEDED`` (10) held calls; every figure below is
  computed either way, the page shows them only while ``validating``;
- ``proposed``: held calls with ``audit_proposed``; ``proposed_percent`` its share of the held
  calls (``percent``);
- ``accepted``: the distinct Leads with a held call that are in ``accepted_lead_ids`` (the Leads
  with an ``events`` row to ``audit_won``);
- ``price_low``, ``price_high`` and ``price_median``: over the proposed held calls'
  ``audit_price`` (micro-dollars), each ``None`` without one; an even count's median is the
  mean of the middle two (``median``);
- ``price_reactions``: one ``(PriceReaction, count)`` per value in enum order, zeros included,
  over held calls;
- ``qualified`` and ``qualified_percent``: qualified held calls and their share of the held;
- ``top_objections``: at most ``TOP_OBJECTIONS`` (3) ``(ObjectionType, count)``, counted once
  per held call that raised it, by count descending then ``ObjectionType`` order, zero counts
  left out;
- ``checkpoint``: ``validating`` and fewer than ``AUDITS_NEEDED`` (2) accepted, when the page
  shows ``CHECKPOINT`` ("Review the Offer's price and scope before the next Variant").

``percent(n, d)`` is ``n / d`` as a whole percent rounded half up, 0 when ``d`` is 0.
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from nextron.domain.call_outcomes import CallResult, PriceReaction
from nextron.domain.leads import LeadSource
from nextron.domain.pipeline_stage import CREATION
from nextron.domain.reply_classes import ObjectionType

HELD_CALLS_NEEDED: Final = 10
AUDITS_NEEDED: Final = 2
TOP_OBJECTIONS: Final = 3
CHECKPOINT: Final = "Review the Offer's price and scope before the next Variant"
ENGINE_SOURCES: Final[frozenset[LeadSource]] = frozenset(LeadSource)


def engine_creation_causes() -> frozenset[str]:
    """The creation ``events`` causes of engine-sourced Leads."""
    return frozenset(str(CREATION[source].cause) for source in ENGINE_SOURCES)


@dataclass(frozen=True, slots=True)
class CallFact:
    """One counted booking: its Lead, its recorded outcome (``None`` while pending) and the
    held call's fields (``None``, or ``()`` for objections, when they don't apply)."""

    lead_id: str
    outcome: CallResult | None = None
    qualified: bool | None = None
    audit_proposed: bool | None = None
    audit_price: int | None = None
    objections: tuple[ObjectionType, ...] = ()
    price_reaction: PriceReaction | None = None


@dataclass(frozen=True, slots=True)
class OfferReport:
    """The Calls report's figures; see the module docstring."""

    booked: int
    held: int
    no_shows: int
    validating: bool
    proposed: int
    proposed_percent: int
    accepted: int
    price_low: int | None
    price_high: int | None
    price_median: int | None
    price_reactions: tuple[tuple[PriceReaction, int], ...]
    qualified: int
    qualified_percent: int
    top_objections: tuple[tuple[ObjectionType, int], ...]
    checkpoint: bool


def percent(n: int, d: int) -> int:
    """``n / d`` as a whole percent, rounded half up; 0 when ``d`` is 0."""
    if d <= 0:
        return 0
    return (200 * n + d) // (2 * d)


def median(values: Sequence[int]) -> int | None:
    """The median of ``values`` (the mean of the middle two for an even count), or ``None``.

    Prices are whole dollars in micro-dollars, so the mean of two is a whole micro-dollar."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _top_objections(held: Iterable[CallFact]) -> tuple[tuple[ObjectionType, int], ...]:
    counts = {each: 0 for each in ObjectionType}
    for fact in held:
        for objection in set(fact.objections):
            counts[objection] += 1
    order = list(ObjectionType)
    ranked = sorted(
        (item for item in counts.items() if item[1]),
        key=lambda item: (-item[1], order.index(item[0])),
    )
    return tuple(ranked[:TOP_OBJECTIONS])


def build_report(facts: Iterable[CallFact], accepted_lead_ids: Collection[str]) -> OfferReport:
    """The ``OfferReport`` of ``facts``; see the module docstring."""
    every = tuple(facts)
    held = tuple(fact for fact in every if fact.outcome is CallResult.HELD)
    no_shows = sum(1 for fact in every if fact.outcome is CallResult.NO_SHOW)
    proposed = tuple(fact for fact in held if fact.audit_proposed)
    prices = [fact.audit_price for fact in proposed if fact.audit_price is not None]
    accepted = len({fact.lead_id for fact in held} & set(accepted_lead_ids))
    qualified = sum(1 for fact in held if fact.qualified)
    validating = len(held) >= HELD_CALLS_NEEDED
    return OfferReport(
        booked=len(every),
        held=len(held),
        no_shows=no_shows,
        validating=validating,
        proposed=len(proposed),
        proposed_percent=percent(len(proposed), len(held)),
        accepted=accepted,
        price_low=min(prices) if prices else None,
        price_high=max(prices) if prices else None,
        price_median=median(prices),
        price_reactions=tuple(
            (reaction, sum(1 for fact in held if fact.price_reaction is reaction))
            for reaction in PriceReaction
        ),
        qualified=qualified,
        qualified_percent=percent(qualified, len(held)),
        top_objections=_top_objections(held),
        checkpoint=validating and accepted < AUDITS_NEEDED,
    )
