"""Story 4.10's ``Selection`` (FR-19 S1, FR-59, UX-DR20, AD-11, AD-16, AD-21) on the world of
``tests.app.test_enroll`` (a temporary database at head, ``FrozenClock``, the real subscriber
registry, the shipped seeds, Law-PrivateAI x US active, one Sending Inbox):

- **Capacity.** ``today()`` from the inboxes' cold caps, the follow-ups due, the largest
  Sequence's Step count and today's ``picked``; no warm inbox gives 0; reading writes nothing.
- **The list.** Only scored, unmerged Leads at ``new``, ``queued`` or ``contacted`` with a
  Primary Contact who isn't erased and no live Enrollment; Tier A by newest live Event (a
  retracted Signal doesn't count), then B and C by Lead Score; the badges and the live
  Eligibility.
- **Picks.** Within capacity (the I/O matrix: remaining 2, three eligible Leads), every reason
  in its turn, a refusal consuming no capacity, origin ``operator_pick`` with 4.9's slots and no
  job; the argument check.
- **Recording.** ``record_capacity`` upserts today's row, finalises earlier ones on the next
  day, and ``LeadScores.recompute_tiers`` calls it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.app.scoring.api import Scoring
from nextron.app.scoring.lead_scores import LeadScores
from nextron.app.scoring.selection import PickOutcome, PickReason, Selection
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup.api import Setup
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import current, use_case
from nextron.domain.contactability import Blocked, BlockReason, key_hash
from nextron.domain.scoring import Factor, FactorKind, Tier, factors_text
from nextron.domain.sequences import InvalidSequences
from tests.app.test_enroll import LATER, T0, US_CAMPAIGN, World, build_world, queue_on, stamp

OTHER = "01JD3QZ8X5V9K2M7N4P6R8T0AA"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def selection_of(world: World) -> Selection:
    return Selection(world.clock, Sequencing(world.clock), Compliance(world.clock))


def warm(world: World, level: int = 30) -> None:
    """The inbox active and warmed up long enough: its cold cap is ``level`` (also the ramp's
    target, so the weekly ramp can't lift it)."""
    world.sql(
        "UPDATE sending_inboxes SET status = 'active', warmup_started_on = '2026-10-01', "
        "warmup_method = 'manual', warm_level = ?, ramp_target = ?",
        (level, level),
    )


def scored(
    world: World,
    tier: Tier,
    score: int,
    *,
    primary: bool = True,
    country: str = "US",
    events: tuple[int, ...] = (),
    segment: str | None = None,
) -> tuple[str, str]:
    """A new Lead with a Contact, scored ``tier`` and ``score``, its Contact made Primary, and
    one stored Event factor per age in ``events`` (days since first seen); its and the
    Contact's ids."""
    lead_id, contact_id = world.contact(country, segment=segment)
    factors = []
    for age in events:
        evidence = world.transact(
            lambda: signals_repo.insert_evidence(
                lead_id=lead_id,
                evidence_type="quote",
                quote="We are hiring.",
                sought=None,
                number=None,
                fact=None,
                value=None,
                pages_checked=("https://firm.com/careers",),
                source="website",
                source_url="https://firm.com/careers",
                retrieved_at=T0,
                extracted_by="ai:extract_signals@v1",
                person_contact_id=None,
                at=T0,
            )
        )
        signal_id = world.transact(
            lambda: signals_repo.insert_signal(
                lead_id=lead_id,
                signal_type="law_hiring",
                signal_class="event",
                tier=1,
                score_only=False,
                event_date=None,
                first_seen_at=LATER - timedelta(days=age),
                evidence_ids=[evidence],
            )
        )
        factors.append(Factor(FactorKind.EVENT, "law_hiring", 10.0, 50, 1, signal_id, (evidence,)))
    world.sql(
        "UPDATE leads SET tier = ?, lead_score = ?, fit_score = ?, signal_score = ?, "
        "tier_computed_at = ?, score_factors = ? WHERE id = ?",
        (str(tier), score, score, score, stamp(T0), factors_text((), factors), lead_id),
    )
    if primary:
        world.transact(lambda: Research(world.clock).make_primary(lead_id, contact_id))
    return lead_id, contact_id


def ready_ids(world: World) -> list[str]:
    return [row.lead_id for row in world.read(selection_of(world).ready_list)]


def pick(world: World, *lead_ids: str) -> tuple[PickOutcome, ...]:
    selection = selection_of(world)
    with world.units.open():
        return selection.pick(list(lead_ids))


def capacity_rows(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT local_date, total_cold_capacity, follow_ups_reserved, new_sequence_capacity, "
        "picked, signal_shortfall, computed_at FROM daily_capacity ORDER BY local_date"
    )


@contextmanager
def blocked_gate() -> Iterator[None]:
    original = compliance_api.assert_contactable
    compliance_api.assert_contactable = lambda *_: Blocked(  # type: ignore[assignment]
        (BlockReason.ADDRESS_SUPPRESSED,)
    )
    try:
        yield
    finally:
        compliance_api.assert_contactable = original  # type: ignore[assignment]


# Capacity -----------------------------------------------------------------------------------------


def test_no_warm_inbox_means_no_capacity(world: World) -> None:
    today = world.read(selection_of(world).today)

    assert (today.total, today.reserved, today.steps, today.capacity, today.picked) == (
        0,
        0,
        4,
        0,
        0,
    )
    assert today.remaining == 0
    assert today.local_date == LATER.date()  # sender_timezone unset: UTC


def test_today_reads_the_caps_the_follow_ups_and_the_largest_sequence(world: World) -> None:
    warm(world, 30)
    lead_id, contact_id = scored(world, Tier.B, 60)
    enrolled = world.enroll(contact_id, world.campaign(US_CAMPAIGN), steps=(1, 2))
    step_2 = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? AND step_no = 2",
        (getattr(enrolled, "enrollment_id"),),
    )[0][0]
    world.sql("UPDATE drafts SET due_at = ? WHERE id = ?", (stamp(LATER), step_2))

    today = world.read(selection_of(world).today)

    assert (today.total, today.reserved, today.steps) == (30, 1, 4)
    assert today.capacity == 7  # min(30 - 1, 30 // 4)
    assert lead_id  # the Lead itself is enrolled, so it isn't listed
    assert capacity_rows(world) == []  # reading writes nothing


def test_the_sender_timezone_sets_the_day(world: World) -> None:
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "Pacific/Auckland"})
    world.clock.set(datetime(2026, 11, 18, 12, 0, tzinfo=UTC))  # 01:00 on the 19th there

    assert world.read(selection_of(world).today).local_date.isoformat() == "2026-11-19"


def test_the_follow_ups_reserved_use_the_sender_timezone_day(world: World) -> None:
    warm(world, 30)
    _, contact_id = scored(world, Tier.B, 60)
    enrolled = world.enroll(contact_id, world.campaign(US_CAMPAIGN), steps=(1, 2))
    step_2 = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? AND step_no = 2",
        (getattr(enrolled, "enrollment_id"),),
    )[0][0]
    day = LATER.date()
    world.clock.set(datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC))
    # 03:00 UTC the next day is still this evening in Los Angeles
    next_day = day + timedelta(days=1)
    due = datetime(next_day.year, next_day.month, next_day.day, 3, 0, tzinfo=UTC)
    world.sql("UPDATE drafts SET due_at = ? WHERE id = ?", (stamp(due), step_2))
    assert world.read(selection_of(world).today).reserved == 0  # UTC while unset

    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/Los_Angeles"})

    today = world.read(selection_of(world).today)
    assert (today.local_date, today.reserved) == (day, 1)


# The list -----------------------------------------------------------------------------------------


def test_the_list_holds_only_ready_leads(world: World) -> None:
    ready, _ = scored(world, Tier.B, 60)
    unscored, unscored_contact = world.contact()
    world.transact(lambda: Research(world.clock).make_primary(unscored, unscored_contact))
    no_primary, _ = scored(world, Tier.B, 60, primary=False)
    merged, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (ready, merged))
    replied, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET pipeline_stage = 'replied' WHERE id = ?", (replied,))
    contacted, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET pipeline_stage = 'contacted' WHERE id = ?", (contacted,))
    queued, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET pipeline_stage = 'queued' WHERE id = ?", (queued,))
    enrolled, enrolled_contact = scored(world, Tier.B, 60)
    world.enroll(enrolled_contact, world.campaign(US_CAMPAIGN))

    assert set(ready_ids(world)) == {ready, contacted, queued}
    assert {unscored, no_primary, merged, replied, enrolled}.isdisjoint(ready_ids(world))


def test_an_erased_primary_contact_drops_the_lead(world: World) -> None:
    lead_id, contact_id = scored(world, Tier.B, 60)
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    world.sql(
        "UPDATE contacts SET name = '[erased]', role = NULL, email = ?, linkedin_url = NULL, "
        "office_country = NULL, office_region = NULL, erased_at = ? WHERE id = ?",
        (key_hash("address", email), stamp(T0), contact_id),
    )

    assert ready_ids(world) == []


def test_the_order_tier_a_by_newest_event_then_b_and_c_by_score(world: World) -> None:
    c_high, _ = scored(world, Tier.C, 90)
    b_low, _ = scored(world, Tier.B, 40)
    b_high, _ = scored(world, Tier.B, 80)
    a_old, _ = scored(world, Tier.A, 99, events=(20,))
    a_new, _ = scored(world, Tier.A, 10, events=(30, 2))
    a_retracted, _ = scored(world, Tier.A, 50, events=(1,))
    world.sql("UPDATE signals SET retracted_at = ? WHERE lead_id = ?", (stamp(T0), a_retracted))

    assert ready_ids(world) == [a_new, a_old, a_retracted, b_high, b_low, c_high]


def test_each_row_shows_its_facts_badges_and_live_eligibility(world: World) -> None:
    c_lead, _ = scored(world, Tier.C, 40)
    b_lead, _ = scored(world, Tier.B, 50)
    world.sql("UPDATE contacts SET role = 'Managing Partner'")

    rows = {row.lead_id: row for row in world.read(selection_of(world).ready_list)}

    c_row, b_row = rows[c_lead], rows[b_lead]
    assert (c_row.tier, c_row.lead_score, c_row.generic_copy, c_row.below_minimum) == (
        Tier.C,
        40,
        True,
        True,
    )
    assert (b_row.generic_copy, b_row.below_minimum) == (False, False)
    assert c_row.name.startswith("Firm ")
    law = world.read(lambda: setup_queries.segment("law_private_ai"))
    assert law is not None and c_row.segment == law.definition.name
    assert c_row.market is not None
    assert c_row.role == "Managing Partner"
    assert c_row.eligibility.eligible is True
    assert world.sql("SELECT COUNT(*) FROM daily_capacity") == [(0,)]


def test_the_minimum_lead_score_setting_moves_the_badge(world: World) -> None:
    lead_id, _ = scored(world, Tier.B, 40)
    with world.units.open():
        Setup(world.clock).save({"min_lead_score": "30"})

    [row] = world.read(selection_of(world).ready_list)

    assert (row.lead_id, row.below_minimum) == (lead_id, False)


# Picks --------------------------------------------------------------------------------------------


def test_picks_within_capacity_enroll_in_order_and_count(world: World) -> None:
    warm(world, 8)  # 8 // 4 = 2 new Sequences today
    a, _ = scored(world, Tier.B, 60)
    b, _ = scored(world, Tier.B, 60)
    c, _ = scored(world, Tier.B, 60)
    jobs = world.sql("SELECT COUNT(*) FROM jobs")

    outcomes = pick(world, a, b, c)

    assert [(o.lead_id, o.reason) for o in outcomes] == [
        (a, None),
        (b, None),
        (c, PickReason.OVER_CAPACITY),
    ]
    assert outcomes[2].enrollment_id is None
    for outcome in outcomes[:2]:
        [(origin, status)] = world.sql(
            "SELECT origin, status FROM enrollments WHERE id = ?", (outcome.enrollment_id,)
        )
        assert (origin, status) == ("operator_pick", "active")
        slots = world.sql(
            "SELECT step_no FROM drafts WHERE enrollment_id = ? ORDER BY step_no",
            (outcome.enrollment_id,),
        )
        assert slots == [(1,), (2,), (3,), (4,)]
    assert world.sql("SELECT COUNT(*) FROM jobs") == jobs  # no job, AI call or email
    [row] = capacity_rows(world)
    assert row[:6] == (LATER.date().isoformat(), 8, 0, 2, 2, None)
    assert world.read(selection_of(world).today).remaining == 0
    assert ready_ids(world) == [c]


def test_every_cold_cap_zero_refuses_every_pick(world: World) -> None:
    a, _ = scored(world, Tier.B, 60)

    [outcome] = pick(world, a)

    assert outcome.reason == PickReason.OVER_CAPACITY
    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]
    assert capacity_rows(world)[0][1:6] == (0, 0, 0, 0, None)


def test_each_reason_in_its_turn(world: World) -> None:
    warm(world, 30)
    no_primary, _ = scored(world, Tier.B, 60, primary=False)
    merged, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (no_primary, merged))
    [(ops,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    no_campaign, _ = scored(world, Tier.B, 60, country="GB", segment=ops)
    world.sql(  # every shipped Segment x Market has a Campaign: drop Ops x UK's
        "DELETE FROM variants WHERE campaign_id IN "
        "(SELECT id FROM campaigns WHERE key = 'ops_automation_uk')"
    )
    world.sql("DELETE FROM campaigns WHERE key = 'ops_automation_uk'")
    uk, _ = scored(world, Tier.B, 60, country="GB")  # Law-PrivateAI x UK is still a draft
    replied, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET pipeline_stage = 'replied' WHERE id = ?", (replied,))

    outcomes = pick(world, OTHER, merged, no_primary, no_campaign, uk, replied)

    assert [o.reason for o in outcomes] == [
        "unknown_lead",
        "unknown_lead",
        "no_primary_contact",
        "no_campaign",
        "campaign_not_active",
        "lead_already_replied",
    ]
    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]
    assert capacity_rows(world)[0][4] == 0  # a refusal consumes no capacity


def test_a_blocked_contact_is_refused_and_consumes_nothing(world: World) -> None:
    warm(world, 30)
    a, _ = scored(world, Tier.B, 60)
    before = world.counts()

    with blocked_gate():
        [outcome] = pick(world, a)

    assert (outcome.enrollment_id, outcome.reason) == (None, "blocked")
    after = world.counts()
    assert {t: after[t] - before[t] for t in after if after[t] != before[t]} == {
        "daily_capacity": 1
    }
    assert capacity_rows(world)[0][4] == 0


def test_an_ineligible_contact_is_refused_and_consumes_nothing(world: World) -> None:
    warm(world, 30)
    a, contact_id = scored(world, Tier.B, 60)
    first = world.enroll(contact_id, world.campaign(US_CAMPAIGN))
    sent = stamp(LATER - timedelta(days=30))
    world.sql(  # FR-63: a Sequence completed without a Reply 30 days ago
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (sent, sent, getattr(first, "enrollment_id")),
    )
    before = world.counts()

    [outcome] = pick(world, a)

    assert (outcome.enrollment_id, outcome.reason) == (None, "not_eligible")
    after = world.counts()
    assert {t: after[t] - before[t] for t in after if after[t] != before[t]} == {
        "daily_capacity": 1
    }
    assert capacity_rows(world)[0][4] == 0


def test_an_undecodable_sequence_refuses_the_pick_as_no_campaign(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    warm(world, 30)
    a, _ = scored(world, Tier.B, 60)

    def undecodable(_: str) -> None:
        raise InvalidSequences("sequences[x].steps", "is not valid JSON")

    monkeypatch.setattr(setup_queries, "sequence_by_id", undecodable)

    [outcome] = pick(world, a)

    assert (outcome.enrollment_id, outcome.reason) == (None, PickReason.NO_CAMPAIGN)
    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]


def test_pick_takes_a_list_or_tuple_of_str(world: World) -> None:
    selection = selection_of(world)
    with world.units.open():
        with pytest.raises(TypeError):
            selection.pick("01JD3QZ8X5V9K2M7N4P6R8T0AA")  # type: ignore[arg-type]
    with world.units.open():
        with pytest.raises(TypeError):
            selection.pick([1])  # type: ignore[list-item]


# Recording ----------------------------------------------------------------------------------------


def record(world: World) -> None:
    selection = selection_of(world)

    @use_case
    def caller() -> None:
        with current().transaction():
            selection.record_capacity()

    with world.units.open():
        caller()


def test_record_capacity_upserts_today_and_finalises_earlier_days(world: World) -> None:
    warm(world, 12)  # 12 // 4 = 3
    a, _ = scored(world, Tier.B, 60)
    pick(world, a)
    record(world)  # again the same day: the row is updated, picked kept
    assert [row[:6] for row in capacity_rows(world)] == [
        (LATER.date().isoformat(), 12, 0, 3, 1, None)
    ]

    world.clock.set(LATER + timedelta(days=1))
    record(world)

    first, second = capacity_rows(world)
    assert first[:6] == (LATER.date().isoformat(), 12, 0, 3, 1, 2)  # 3 - 1 never used
    assert second[:6] == ((LATER + timedelta(days=1)).date().isoformat(), 12, 0, 3, 0, None)
    assert second[6] == stamp(LATER + timedelta(days=1))

    world.clock.set(LATER + timedelta(days=2))
    record(world)
    assert [row[5] for row in capacity_rows(world)] == [2, 3, None]  # a finalised row stays


def test_capacity_falling_below_the_picks_finalises_to_zero(world: World) -> None:
    warm(world, 8)  # 8 // 4 = 2
    a, _ = scored(world, Tier.B, 60)
    b, _ = scored(world, Tier.B, 60)
    pick(world, a, b)
    warm(world, 4)  # the same day the cap falls: 4 // 4 = 1, below the 2 picked
    record(world)
    assert capacity_rows(world)[0][3:6] == (1, 2, None)
    assert world.read(selection_of(world).today).remaining == 0

    world.clock.set(LATER + timedelta(days=1))
    record(world)

    assert capacity_rows(world)[0][5] == 0  # never negative


def test_recompute_tiers_records_the_days_capacity(world: World) -> None:
    warm(world, 30)
    scores = LeadScores(
        world.clock,
        Scoring(world.clock, Pipeline(world.clock), Research(world.clock)),
        queue_on(world.clock),
        selection=selection_of(world),
    )

    with world.units.open():
        scores.recompute_tiers()

    assert [row[:6] for row in capacity_rows(world)] == [
        (LATER.date().isoformat(), 30, 0, 7, 0, None)
    ]

