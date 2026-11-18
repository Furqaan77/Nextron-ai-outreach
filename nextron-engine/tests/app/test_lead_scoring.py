"""Story 2.15's scoring service on a temporary database at head (FR-17, FR-62, AD-3, AD-4,
AD-7): ``Scoring.rescore`` and ``retier`` through ``LeadScores``, with the shipped Segments. The
world is Story 2.9's (``tests.app.test_signal_extraction``); Signals, Evidence and postings are
stored straight through the research repositories.

Covered: the matrix's scored Law Lead; a disqualifier closing the Lead (the ``events`` row's
cause, ``cause_ref`` and actor) with no score; the skips; a closed posting's Event; the chain's
enqueue from the Primary Contact's selection; "Re-score" on the interactive lane; the daily
re-tiering (an Event aging out drops the Tier without a crawl and re-runs the Primary
selection); and the Segment version kept until the Lead is re-scored."""

import json
import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.pipeline.api import Pipeline
from nextron.app.research.people import PrimaryContacts
from nextron.app.scoring.api import RescoreOutcome, RescoreResult, Scoring
from nextron.app.scoring.lead_scores import LeadScores, RescoreRequest
from nextron.app.setup.api import Setup
from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.domain import seed_yaml
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from tests.app.test_signal_extraction import SEGMENTS, SITE, T0, World, build_world

TEAM_URL = f"https://{SITE}/team"
SCORE_COLUMNS = (
    "fit_score, signal_score, lead_score, tier, score_segment_version, disqualified_rule"
)


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine, prompts=False)


def lead_scores(world: World) -> LeadScores:
    scoring = Scoring(world.clock, Pipeline(world.clock), world.research)
    return LeadScores(world.clock, scoring, world.queue)


def score(world: World, lead: str) -> RescoreOutcome:
    with world.units.open():
        return lead_scores(world).score_lead(lead)


def recompute(world: World) -> int:
    with world.units.open():
        return lead_scores(world).recompute_tiers()


def scores(world: World, lead: str) -> tuple[Any, ...]:
    [row] = world.sql(f"SELECT {SCORE_COLUMNS} FROM leads WHERE id = ?", (lead,))
    return row


def evidence(world: World, lead: str, **values: Any) -> str:
    row: dict[str, Any] = {
        "lead_id": lead,
        "evidence_type": "quote",
        "quote": "We are hiring a Legal Operations Manager.",
        "sought": None,
        "number": None,
        "fact": None,
        "value": None,
        "pages_checked": (TEAM_URL,),
        "source": "website",
        "source_url": TEAM_URL,
        "retrieved_at": T0,
        "extracted_by": "ai:extract_signals@v1",
        "person_contact_id": None,
        "at": world.clock.now(),
    }
    row.update(values)
    return world.transact(lambda: signals_repo.insert_evidence(**row))


def signal(
    world: World,
    lead: str,
    signal_type: str,
    *,
    signal_class: str = "event",
    tier: int = 1,
    age: int | None = 10,
    evidence_id: str | None = None,
) -> str:
    supporting = evidence_id or evidence(world, lead)
    event_date: date | None = None if age is None else (T0 - timedelta(days=age)).date()
    return world.transact(
        lambda: signals_repo.insert_signal(
            lead_id=lead,
            signal_type=signal_type,
            signal_class=signal_class,
            tier=tier,
            score_only=signal_class == "attribute" and signal_type.endswith("contact_form_only"),
            event_date=event_date,
            first_seen_at=T0 - timedelta(days=200),
            evidence_ids=[supporting],
        )
    )


def enriched(world: World, lead: str) -> None:
    """An ``ok`` signal extraction run, so ``score_lead`` scores the Lead."""
    world.transact(
        lambda: signals_repo.insert_run(
            lead_id=lead,
            crawl_run_id=None,
            route="extract_signals",
            actor="ai:extract_signals@v1",
            outcome="ok",
            error_code=None,
            pages_sent=1,
            pages_truncated=0,
            stored=1,
            discarded=0,
            started_at=T0,
            ended_at=T0,
        )
    )


def lawyers(world: World, lead: str, number: int, *, counted: bool = False) -> str:
    if counted:
        return evidence(
            world,
            lead,
            evidence_type="count",
            quote=None,
            sought="lawyers listed",
            number=number,
            source_url=None,
            extracted_by="rule",
        )
    return evidence(
        world,
        lead,
        quote=f"Smith Law LLP has {number} lawyers.",
        fact="lawyers_firm_wide",
        number=number,
    )


def scored_law_lead(world: World) -> tuple[str, str, str]:
    """The matrix's Law Lead: 20 lawyers, a live Tier 1 hiring Event (age 10) and
    contact-form-only; its id, the Event's and the Attribute's."""
    lead = world.lead()
    enriched(world, lead)
    lawyers(world, lead, 20)
    hiring = signal(world, lead, "law_hiring_legal_ops")
    form = signal(
        world, lead, "law_contact_form_only", signal_class="attribute", tier=3, age=None
    )
    return lead, hiring, form


# Scoring ----------------------------------------------------------------------------------------


def test_a_law_lead_gets_its_scores_tier_segment_version_and_factors(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead, hiring, form = scored_law_lead(world)
    caplog.set_level(logging.INFO, logger="nextron.app.scoring.lead_scores")

    outcome = score(world, lead)

    assert outcome.result is RescoreResult.SCORED
    assert scores(world, lead) == (65, 60, 63, "A", 1, None)
    [(factors, computed, disqualified)] = world.sql(
        "SELECT score_factors, tier_computed_at, disqualified_evidence FROM leads WHERE id = ?",
        (lead,),
    )
    found = json.loads(factors)
    assert [(f["kind"], f["key"], f["points"]) for f in found["fit"]] == [
        ("size_in_range", "lawyers_firm_wide", 50.0),
        ("attribute", "law_contact_form_only", 15.0),
    ]
    assert [(f["kind"], f["signal_id"], f["points"]) for f in found["signal"]] == [
        ("event", hiring, 60.0)
    ]
    assert found["fit"][1]["signal_id"] == form
    assert all(f["evidence_ids"] for f in found["fit"] + found["signal"])
    assert computed is not None and disqualified is None
    [record] = [r for r in caplog.records if r.getMessage() == "lead_scored"]
    fields = (getattr(record, "lead_id"), getattr(record, "lead_score"), getattr(record, "factors"))
    assert fields == (lead, 63, 3)


def test_three_lawyers_listed_disqualifies_the_lead_through_the_pipeline(world: World) -> None:
    lead = world.lead()
    enriched(world, lead)
    count = lawyers(world, lead, 3, counted=True)
    signal(world, lead, "law_hiring_legal_ops")

    outcome = score(world, lead)

    assert (outcome.result, outcome.evidence_id) == (RescoreResult.DISQUALIFIED, count)
    assert scores(world, lead) == (None, None, None, None, 1, "law_fewer_than_5_lawyers")
    assert world.sql(
        "SELECT pipeline_stage, score_factors, disqualified_evidence FROM leads WHERE id = ?",
        (lead,),
    ) == [("disqualified", None, json.dumps([count]))]
    assert world.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'leads' AND entity_id = ? AND to_status = 'disqualified'",
        (lead,),
    ) == [("new", "disqualified", "segment_disqualifier", count, "system:scoring")]
    # a Disqualified Lead is closed: it is never scored again
    assert score(world, lead).reason == "lead_closed"


def test_an_unknown_size_is_never_disqualified(world: World) -> None:
    lead = world.lead()
    enriched(world, lead)

    score(world, lead)

    assert scores(world, lead) == (25, 0, 13, "C", 1, None)


def test_a_closed_postings_event_is_not_live(world: World) -> None:
    lead = world.lead()
    enriched(world, lead)
    quote = evidence(world, lead)
    hiring = signal(world, lead, "law_hiring_legal_ops", evidence_id=quote)

    def posting() -> None:
        posting_id = postings_repo.insert_posting(
            lead_id=lead,
            source="careers_page",
            board_token=None,
            url=f"https://{SITE}/careers",
            title="Legal Operations Manager",
            description=None,
            posted_date=None,
            checked_at=T0,
        )
        postings_repo.set_event(posting_id, hiring, quote)
        postings_repo.close(posting_id, T0)

    world.transact(posting)

    score(world, lead)

    assert scores(world, lead)[1:4] == (0, 13, "C")


def test_the_skips_write_nothing(world: World, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.scoring.lead_scores")
    raw = world.lead()  # never enriched
    lost = world.lead(web_domain="jones-law.com")
    enriched(world, lost)
    world.transact(lambda: Pipeline(world.clock).close(lost, PipelineStage.LOST, None))
    merged = world.lead(web_domain="smith-law-two.com")
    enriched(world, merged)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (raw, merged))

    reasons = [
        score(world, lead).reason
        for lead in ("01KA0000000000000000000000", "not-a-ulid", raw, lost, merged)
    ]

    assert reasons == ["unknown_lead", "unknown_lead", "not_enriched", "lead_closed", "merged_lead"]
    for lead in (raw, lost, merged):
        assert scores(world, lead) == (None,) * 6
    logged = [r for r in caplog.records if r.getMessage() == "score_skipped"]
    assert [r.reason for r in logged] == reasons  # type: ignore[attr-defined]


# Enqueue points ---------------------------------------------------------------------------------


def score_jobs(world: World) -> list[tuple[Any, ...]]:
    return world.jobs("score_lead")


def test_the_primary_contacts_selection_queues_score_lead(world: World) -> None:
    lead = world.lead()
    primary = PrimaryContacts(world.clock, world.research, world.queue)

    with world.units.open():
        primary.select_primary_contact(lead)
    with world.units.open():
        primary.select_primary_contact(lead)  # the live job absorbs the repeat
    with world.units.open():
        primary.select_primary_contact("01KA0000000000000000000000")  # skipped: none queued

    assert score_jobs(world) == [
        (
            f"score_lead:{lead}:tick",
            None,
            json.dumps({"lead_id": lead, "v": 1}, separators=(",", ":"), sort_keys=True),
            "bulk",
        )
    ]


def test_rescore_queues_score_lead_on_the_interactive_lane(world: World) -> None:
    lead = world.lead()

    with world.units.open():
        assert lead_scores(world).request_rescore(lead) is RescoreRequest.QUEUED
    with world.units.open():
        unknown = lead_scores(world).request_rescore("01KA0000000000000000000000")
    assert unknown is RescoreRequest.UNKNOWN

    [(key, _, _, lane)] = score_jobs(world)
    assert (key, lane) == (f"score_lead:{lead}:tick", "interactive")


def test_rescore_refuses_a_closed_or_merged_lead_and_queues_nothing(world: World) -> None:
    lost = world.lead()
    world.transact(lambda: Pipeline(world.clock).close(lost, PipelineStage.LOST, None))
    survivor = world.lead(web_domain="jones-law.com")
    merged = world.lead(web_domain="smith-law-two.com")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, merged))

    with world.units.open():
        closed_answer = lead_scores(world).request_rescore(lost)
    with world.units.open():
        merged_answer = lead_scores(world).request_rescore(merged)

    assert (closed_answer, merged_answer) == (RescoreRequest.CLOSED, RescoreRequest.MERGED)
    assert score_jobs(world) == []


# Re-tiering -------------------------------------------------------------------------------------


def no_decision_maker(world: World, lead: str) -> Any:
    [(value,)] = world.sql("SELECT no_decision_maker FROM leads WHERE id = ?", (lead,))
    return value


def test_an_event_aging_out_drops_the_tier_and_re_runs_the_primary_selection(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    assert no_decision_maker(world, lead) is None  # no selection yet
    caplog.set_level(logging.INFO, logger="nextron.app.scoring.lead_scores")

    world.clock.advance(timedelta(days=50))  # the Event is 60 days old: half its weight
    assert recompute(world) == 1
    assert scores(world, lead) == (65, 30, 48, "A", 1, None)
    assert no_decision_maker(world, lead) is None  # nothing dropped: no selection

    world.clock.advance(timedelta(days=30))  # 90 days old: gone
    assert recompute(world) == 1

    assert scores(world, lead) == (65, 0, 33, "C", 1, None)
    assert no_decision_maker(world, lead) == 1  # the selection re-ran (no Contacts)
    [(factors,)] = world.sql("SELECT score_factors FROM leads WHERE id = ?", (lead,))
    assert json.loads(factors)["signal"] == []
    logged = [r for r in caplog.records if r.getMessage() == "tiers_recomputed"]
    assert [(r.leads, r.retiered) for r in logged] == [(1, 1), (1, 1)]  # type: ignore[attr-defined]


def test_recompute_skips_unscored_closed_and_merged_leads(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    unscored = world.lead(web_domain="jones-law.com")
    closed = world.lead(web_domain="smith-law-two.com")
    enriched(world, closed)
    score(world, closed)
    world.transact(lambda: Pipeline(world.clock).close(closed, PipelineStage.LOST, None))
    merged = world.lead(web_domain="smith-law-three.com")
    enriched(world, merged)
    score(world, merged)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead, merged))
    before = world.sql("SELECT tier_computed_at FROM leads WHERE id = ?", (merged,))
    world.clock.advance(timedelta(days=1))

    assert recompute(world) == 1
    assert scores(world, unscored) == (None,) * 6
    assert world.sql("SELECT tier_computed_at FROM leads WHERE id = ?", (merged,)) == before


def test_recompute_leaves_a_lead_whose_stored_factors_dont_decode(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    corrupt = world.lead(web_domain="jones-law.com")
    enriched(world, corrupt)
    score(world, corrupt)
    world.sql("UPDATE leads SET score_factors = '{\"x\":1}' WHERE id = ?", (corrupt,))
    before = world.sql("SELECT * FROM leads WHERE id = ?", (corrupt,))
    world.clock.advance(timedelta(days=1))

    assert recompute(world) == 1  # the healthy Lead only
    assert world.sql("SELECT * FROM leads WHERE id = ?", (corrupt,)) == before


def test_the_segment_version_is_kept_until_the_lead_is_re_scored(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    shipped = validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))
    changed = [
        replace(
            segment,
            signal_types=tuple(
                replace(each, weight=20) if each.key == "law_contact_form_only" else each
                for each in segment.signal_types
            ),
        )
        for segment in shipped
    ]
    with world.units.open():
        Setup(world.clock).sync_segments(changed)
    assert world.sql("SELECT version FROM segments WHERE key = 'law_private_ai'") == [(2,)]

    recompute(world)
    assert scores(world, lead) == (65, 60, 63, "A", 1, None)  # the stored score stands

    score(world, lead)  # "Re-score" applies the current Segment
    assert scores(world, lead) == (70, 60, 65, "A", 2, None)
