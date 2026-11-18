"""Story 9.6's Offer validation report (FR-69, NFR-49, NFR-50) through
``nextron.app.analytics.offer.offer_validation`` on a temporary database at head: each I/O
matrix row (early, empty, the fixture's exact numbers, passed, a Lead that isn't
engine-sourced, superseded and pending bookings, no proposals), with Leads created through
``Discovery.upsert_lead`` and outcomes recorded through ``Pipeline``, and no row changed by a
read."""

import sqlite3
from typing import Any

import pytest

from nextron.app.analytics.offer import offer_validation
from nextron.app.pipeline.api import OPERATOR
from nextron.db.models.base import new_id
from nextron.domain.call_outcomes import CallResult, Outcome, PriceReaction
from nextron.domain.offer_validation import OfferReport
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.reply_classes import ObjectionType
from tests.app.test_call_outcomes import book
from tests.app.test_pipeline_manual import run
from tests.app.test_suppression import World, world  # noqa: F401 (the fixture)

S = PipelineStage
O = ObjectionType
R = PriceReaction
DOLLAR = 1_000_000
NO_SHOW = Outcome(outcome=CallResult.NO_SHOW)


def held(
    *,
    qualified: bool = True,
    price: int | None = None,
    objections: tuple[ObjectionType, ...] = (),
    reaction: PriceReaction = R.NOT_DISCUSSED,
) -> Outcome:
    return Outcome(
        outcome=CallResult.HELD,
        qualified=qualified,
        reason=None if qualified else "Too small",
        audit_proposed=price is not None,
        audit_price=None if price is None else price * DOLLAR,
        price_reaction=reaction,
        objections=tuple(sorted(objections)),
    )


class Calls:
    """Leads with one booking each, numbered for distinct domains."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.count = 0

    def lead(self) -> tuple[str, str]:
        self.count += 1
        domain = f"firm{self.count}.co.uk"
        lead = self.world.lead(domain)
        contact = self.world.contact(lead, name="Pat Lee", email=f"pat@{domain}")
        return lead, contact

    def booked(self, outcome: Outcome | None = None) -> tuple[str, str]:
        """A Lead with one booking, recorded with ``outcome`` unless ``None``."""
        lead, contact = self.lead()
        booking = book(self.world, lead, contact)
        if outcome is not None:
            run(
                self.world,
                lambda p: p.record_call_outcome(booking, outcome, actor=OPERATOR),
            )
        return lead, booking

    def win_audit(self, lead: str) -> None:
        run(self.world, lambda p: p.move(lead, S.AUDIT_WON, actor=OPERATOR))


def report(world: World) -> OfferReport:
    return world.read(offer_validation)


def every_table(world: World) -> dict[str, list[tuple[Any, ...]]]:
    names = [
        name
        for (name,) in world.sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    return {name: world.sql(f'SELECT * FROM "{name}" ORDER BY rowid') for name in names}


def counts(found: OfferReport) -> tuple[int, int, int]:
    return found.booked, found.held, found.no_shows


def test_empty(world: World) -> None:
    found = report(world)

    assert counts(found) == (0, 0, 0)
    assert not found.validating and not found.checkpoint


def test_early(world: World) -> None:
    calls = Calls(world)
    for _ in range(6):
        calls.booked(held())
    calls.booked(NO_SHOW)
    calls.booked()  # pending

    found = report(world)

    assert counts(found) == (8, 6, 1)
    assert not found.validating
    assert not found.checkpoint


def fixture(world: World) -> Calls:
    """12 held, 5 proposed, one Lead at ``audit_won``, objections no_budget x4,
    uses_other_tool x3, send_information x2."""
    calls = Calls(world)
    objections = [
        (O.NO_BUDGET, O.USES_OTHER_TOOL),
        (O.NO_BUDGET, O.USES_OTHER_TOOL),
        (O.NO_BUDGET, O.SEND_INFORMATION),
        (O.NO_BUDGET,),
        (O.USES_OTHER_TOOL, O.SEND_INFORMATION),
    ] + [()] * 7
    prices = [2_000, 2_500, 2_500, 3_000, 2_500] + [None] * 7
    reactions = [R.ACCEPTED, R.PUSHED_BACK, R.PUSHED_BACK] + [R.NOT_DISCUSSED] * 9
    leads = [
        calls.booked(
            held(
                qualified=index < 8,
                price=prices[index],
                objections=objections[index],
                reaction=reactions[index],
            )
        )[0]
        for index in range(12)
    ]
    calls.win_audit(leads[0])
    return calls


def test_the_fixture_numbers(world: World) -> None:
    fixture(world)

    found = report(world)

    assert counts(found) == (12, 12, 0)
    assert found.validating
    assert (found.proposed, found.proposed_percent) == (5, 42)
    assert found.accepted == 1
    assert (found.price_low, found.price_high, found.price_median) == (
        2_000 * DOLLAR,
        3_000 * DOLLAR,
        2_500 * DOLLAR,
    )
    assert found.price_reactions == ((R.ACCEPTED, 1), (R.PUSHED_BACK, 2), (R.NOT_DISCUSSED, 9))
    assert (found.qualified, found.qualified_percent) == (8, 67)
    assert found.top_objections == (
        (O.NO_BUDGET, 4),
        (O.USES_OTHER_TOOL, 3),
        (O.SEND_INFORMATION, 2),
    )
    assert found.checkpoint


def test_passed(world: World) -> None:
    calls = Calls(world)
    leads = [calls.booked(held(price=2_500))[0] for _ in range(10)]
    calls.win_audit(leads[0])
    calls.win_audit(leads[1])

    found = report(world)

    assert found.validating and found.accepted == 2
    assert not found.checkpoint


def test_a_lead_that_skipped_audit_won_is_not_accepted(world: World) -> None:
    calls = Calls(world)
    leads = [calls.booked(held(price=2_500))[0] for _ in range(10)]
    run(world, lambda p: p.move(leads[0], S.BUILD_PROPOSED, actor=OPERATOR))

    assert report(world).accepted == 0


@pytest.mark.parametrize("later", [S.BUILD_PROPOSED, S.WON])
def test_a_lead_that_reached_audit_won_and_moved_on_still_counts(
    world: World, later: PipelineStage
) -> None:
    calls = Calls(world)
    leads = [calls.booked(held(price=2_500))[0] for _ in range(10)]
    calls.win_audit(leads[0])
    run(world, lambda p: p.move(leads[0], S.BUILD_PROPOSED, actor=OPERATOR))
    if later is S.WON:
        run(world, lambda p: p.move(leads[0], S.WON, actor=OPERATOR))
    assert world.stage(leads[0]) == later

    assert report(world).accepted == 1


def test_an_audit_won_under_a_merged_lead_counts_for_the_survivor(world: World) -> None:
    calls = Calls(world)
    survivor, _ = calls.booked(held(price=2_500))  # the older Lead, at call_held
    merged, _ = calls.booked(held(price=3_000))
    calls.win_audit(merged)
    run(world, lambda p: p.move(merged, S.BUILD_PROPOSED, actor=OPERATOR))
    world.transact(lambda: world.discovery.merge(survivor, merged))
    # the bookings moved to the survivor; the audit_won row stays under the merged Lead
    assert world.sql("SELECT DISTINCT lead_id FROM call_outcomes") == [(survivor,)]
    assert world.stage(survivor) == S.BUILD_PROPOSED

    found = report(world)

    assert counts(found) == (2, 2, 0)
    assert found.accepted == 1


def copy_row(world: World, table: str, row_id: str, **values: object) -> str:
    """A copy of ``table``'s row ``row_id`` with a new id and ``values``; returns the id."""
    new = new_id()
    connection = sqlite3.connect(world.db_file, isolation_level=None)
    try:
        connection.execute(
            f"CREATE TEMP TABLE copied AS SELECT * FROM {table} WHERE id = ?", (row_id,)
        )
        assignments = ", ".join(f"{column} = ?" for column in ("id", *values))
        connection.execute(f"UPDATE copied SET {assignments}", (new, *values.values()))
        connection.execute(f"INSERT INTO {table} SELECT * FROM copied")
    finally:
        connection.close()
    return new


def test_a_lead_without_an_engine_creation_row_is_not_counted(world: World) -> None:
    calls = Calls(world)
    lead, booking = calls.booked(held(price=2_500))
    # a Lead with no creation events row (say, one a future story adds by hand)
    stranger = copy_row(world, "leads", lead, web_domain="stranger.co.uk")
    copy_row(world, "call_outcomes", booking, lead_id=stranger)

    found = report(world)

    assert counts(found) == (1, 1, 0)
    assert found.proposed == 1
    assert found.accepted == 0


def test_superseded_excluded_and_pending_booked_only(world: World) -> None:
    calls = Calls(world)
    lead, contact = calls.lead()
    first = book(world, lead, contact)
    second = book(world, lead, contact)  # the rebooking supersedes the first
    run(world, lambda p: p.record_call_outcome(second, held(), actor=OPERATOR))
    calls.booked()  # pending
    [(superseded,)] = world.sql("SELECT superseded_at FROM call_outcomes WHERE id = ?", (first,))
    assert superseded is not None

    assert counts(report(world)) == (2, 1, 0)


def test_no_proposals(world: World) -> None:
    calls = Calls(world)
    for _ in range(10):
        calls.booked(held())

    found = report(world)

    assert found.validating and found.proposed == 0
    assert found.price_low is found.price_high is found.price_median is None


def test_the_report_writes_nothing(world: World) -> None:
    fixture(world)
    before = every_table(world)

    report(world)

    assert every_table(world) == before


def test_only_an_engine_cause_with_no_from_status_makes_a_lead_engine_sourced(
    world: World,
) -> None:
    calls = Calls(world)
    lead, booking = calls.booked(held(price=2_500))
    [(creation,)] = world.sql(
        "SELECT id FROM events WHERE entity = 'leads' AND entity_id = ? AND from_status IS NULL",
        (lead,),
    )
    by_hand = copy_row(world, "leads", lead, web_domain="by-hand.co.uk")
    copy_row(world, "events", creation, entity_id=by_hand, cause="created_by_hand")
    not_new = copy_row(world, "leads", lead, web_domain="not-new.co.uk")
    copy_row(world, "events", creation, entity_id=not_new, from_status=str(S.NEW))
    for stranger in (by_hand, not_new):
        copy_row(world, "call_outcomes", booking, lead_id=stranger)
        copy_row(
            world,
            "events",
            creation,
            entity_id=stranger,
            from_status=str(S.CALL_HELD),
            to_status=str(S.AUDIT_WON),
            cause="operator_move",
        )

    found = report(world)

    assert counts(found) == (1, 1, 0)
    assert found.accepted == 0
