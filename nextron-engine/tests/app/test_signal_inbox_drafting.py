"""Story 4.16: a Signal Inbox Lead scored with "Draft Step 1 when ready" is picked in the same
unit of work as its score (FR-8, FR-19, AD-8, AD-10, AD-21), on the world of
``tests.app.test_enroll`` (a temporary database at head, ``FrozenClock``, the real subscriber
registry, the shipped seeds, Law-PrivateAI x US active, one Sending Inbox):

- the I/O matrix through ``LeadScores.score_lead``: happy, box off, no capacity, each pick
  reason, a suppressed Contact, a later rescore, a disqualified Lead, a skipped (merged) Lead,
  a later hand pick;
- the score, the Enrollment, its ``draft_step`` job and the item's outcome commit together or
  not at all;
- ``Selection.signal_inbox_refusals`` (7 days, newest first, enrolled or merged Leads left out)
  and the "Ready to draft" order;
- ``Discovery.record_draft_outcome``'s argument check; the repo never overwrites a decided item;
  without ``selection`` and ``discovery`` scoring never picks;
- the ``signal_inbox_pick`` log line, written only after the score's unit commits;
- ``build_app`` hands ``LeadScores`` the app's ``Selection`` and a ``Discovery``.
"""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.scoring.api import RescoreOutcome, RescoreResult, Scoring
from nextron.app.scoring.lead_scores import LeadScores
from nextron.app.scoring.selection import Selection
from nextron.app.setup.api import Campaigns
from nextron.db.models.base import new_id
from nextron.db.repos.discovery import signal_inbox as signal_inbox_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.scoring import Tier
from tests.app.test_enroll import LATER, T0, US_CAMPAIGN, World, build_world, queue_on, stamp
from tests.app.test_selection import blocked_gate, scored, selection_of, warm

ITEM_COLUMNS = "draft_when_ready, draft_outcome, draft_decided_at"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def inbox_item(world: World, lead_id: str, *, draft: bool = True) -> str:
    """A processed Signal Inbox item of the Lead, its checkbox ``draft``, and an ``ok`` signal
    extraction run of the Lead, so ``score_lead`` scores it; the item's id."""
    world.transact(
        lambda: signals_repo.insert_run(
            lead_id=lead_id,
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
    item_id = new_id()
    world.sql(
        "INSERT INTO signal_inbox_items (id, url, text, status, submitted_at, processed_at, "
        "lead_id, draft_when_ready) VALUES (?, 'https://post.example/1', 'We rekey orders.', "
        "'processed', ?, ?, ?, ?)",
        (item_id, stamp(T0), stamp(T0), lead_id, int(draft)),
    )
    return item_id


def lead_scores(
    world: World, *, selection: Selection | None = None, wired: bool = True, scoring: Any = None
) -> LeadScores:
    return LeadScores(
        world.clock,
        scoring or Scoring(world.clock, Pipeline(world.clock), Research(world.clock)),
        queue_on(world.clock),
        selection=(selection or selection_of(world)) if wired else None,
        discovery=world.discovery if wired else None,
    )


def score(world: World, lead_id: str, **kwargs: Any) -> RescoreOutcome:
    scores = lead_scores(world, **kwargs)
    with world.units.open():
        return scores.score_lead(lead_id)


def item_row(world: World, item_id: str) -> tuple[Any, ...]:
    [row] = world.sql(f"SELECT {ITEM_COLUMNS} FROM signal_inbox_items WHERE id = ?", (item_id,))
    return row


def refusals(world: World) -> list[tuple[str, str, str]]:
    found = world.read(lambda: selection_of(world).signal_inbox_refusals(world.clock.now()))
    return [(each.lead_id, each.lead_name, each.outcome) for each in found]


def lead_name(world: World, lead_id: str) -> str:
    [(name,)] = world.sql("SELECT name FROM leads WHERE id = ?", (lead_id,))
    return str(name)


def ready(world: World) -> list[tuple[str, bool]]:
    rows = world.read(selection_of(world).ready_list)
    return [(row.lead_id, row.signal_inbox_refused) for row in rows]


# The I/O matrix ---------------------------------------------------------------------------------


def test_a_scored_lead_is_enrolled_with_its_draft_job_in_the_scores_unit(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.scoring.lead_scores")
    warm(world, 30)
    lead_id, contact_id = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)
    other = inbox_item(world, lead_id, draft=False)

    outcome = score(world, lead_id)

    assert outcome.result is RescoreResult.SCORED
    [(enrollment_id, origin, status, enrolled_contact)] = world.sql(
        "SELECT id, origin, status, contact_id FROM enrollments WHERE lead_id = ?", (lead_id,)
    )
    assert (origin, status, enrolled_contact) == ("operator_pick", "active", contact_id)
    [(lane, payload)] = world.sql("SELECT lane, payload FROM jobs WHERE type = 'draft_step'")
    assert lane == "interactive"  # enroll's Step 1 job at urgency soon
    assert enrollment_id in payload
    assert item_row(world, item_id) == (1, "enrolled", stamp(LATER))
    assert item_row(world, other) == (0, None, None)  # its box was off
    assert world.events("signal_inbox_items", item_id) == [
        ("processed", "processed", "enrolled", lead_id, "system:discovery", stamp(LATER))
    ]
    assert world.sql("SELECT picked FROM daily_capacity") == [(1,)]
    assert refusals(world) == []
    [record] = [r for r in caplog.records if r.getMessage() == "signal_inbox_pick"]
    fields = (getattr(record, "lead_id"), getattr(record, "enrolled"), getattr(record, "reason"))
    assert fields == (lead_id, True, None)


def test_the_score_the_enrollment_and_the_outcome_commit_together(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.scoring.lead_scores")
    warm(world, 30)
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)
    before = world.counts()
    stored = world.sql("SELECT lead_score, tier_computed_at FROM leads WHERE id = ?", (lead_id,))

    class Failing:
        def record_draft_outcome(self, lead_id: str, outcome: str) -> int:
            raise RuntimeError("the outcome can't be recorded")

    scores = LeadScores(
        world.clock,
        Scoring(world.clock, Pipeline(world.clock), Research(world.clock)),
        queue_on(world.clock),
        selection=selection_of(world),
        discovery=Failing(),
    )
    with pytest.raises(RuntimeError), world.units.open():
        scores.score_lead(lead_id)

    assert world.counts() == before  # no Enrollment, Draft, job or capacity row
    assert [r for r in caplog.records if r.getMessage() == "signal_inbox_pick"] == []
    assert world.sql(
        "SELECT lead_score, tier_computed_at FROM leads WHERE id = ?", (lead_id,)
    ) == stored
    assert item_row(world, item_id) == (1, None, None)


def test_the_box_off_scores_only(world: World) -> None:
    warm(world, 30)
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id, draft=False)

    assert score(world, lead_id).result is RescoreResult.SCORED

    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM daily_capacity") == [(0,)]
    assert item_row(world, item_id) == (0, None, None)


def test_no_capacity_is_refused_shown_and_listed_first(world: World) -> None:
    first, _ = scored(world, Tier.A, 90)  # without the refusal it would come first
    lead_id, _ = scored(world, Tier.C, 10)
    item_id = inbox_item(world, lead_id)

    score(world, lead_id)

    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM drafts") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'draft_step'") == [(0,)]
    assert item_row(world, item_id) == (1, "over_capacity", stamp(LATER))
    assert refusals(world) == [(lead_id, lead_name(world, lead_id), "over_capacity")]
    assert ready(world)[0] == (lead_id, True)
    assert (first, False) in ready(world)


@pytest.mark.parametrize(
    "reason", ["no_primary_contact", "campaign_not_active", "lead_already_enrolled", "no_campaign"]
)
def test_each_pick_reason_is_recorded_with_nothing_drafted(world: World, reason: str) -> None:
    warm(world, 30)
    if reason == "no_primary_contact":
        lead_id, _ = scored(world, Tier.B, 60, primary=False)
    elif reason == "campaign_not_active":
        campaign_id = world.campaign(US_CAMPAIGN)
        with world.units.open():
            Campaigns(world.clock).pause(campaign_id)
        lead_id, _ = scored(world, Tier.B, 60)
    elif reason == "no_campaign":
        world.sql(
            "DELETE FROM variants WHERE campaign_id IN "
            "(SELECT id FROM campaigns WHERE key = 'law_private_ai_uk')"
        )
        world.sql("DELETE FROM campaigns WHERE key = 'law_private_ai_uk'")
        lead_id, _ = scored(world, Tier.B, 60, country="GB")
    else:
        lead_id, contact_id = scored(world, Tier.B, 60)
        world.enroll(contact_id, world.campaign(US_CAMPAIGN))
    item_id = inbox_item(world, lead_id)
    enrollments = world.sql("SELECT COUNT(*) FROM enrollments")
    drafts = world.sql("SELECT COUNT(*) FROM drafts")
    jobs = world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'draft_step'")

    score(world, lead_id)

    assert item_row(world, item_id) == (1, reason, stamp(LATER))
    assert world.sql("SELECT COUNT(*) FROM enrollments") == enrollments
    assert world.sql("SELECT COUNT(*) FROM drafts") == drafts
    assert world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'draft_step'") == jobs
    if reason == "lead_already_enrolled":
        assert refusals(world) == []  # a live Enrollment: nothing to tell Mac
    else:
        assert [each[2] for each in refusals(world)] == [reason]


def test_a_suppressed_contact_is_blocked_and_writes_no_guarded_row(world: World) -> None:
    warm(world, 30)
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)

    with blocked_gate():
        score(world, lead_id)

    assert item_row(world, item_id) == (1, "blocked", stamp(LATER))
    for table in ("enrollments", "drafts"):
        assert world.sql(f"SELECT COUNT(*) FROM {table}") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'draft_step'") == [(0,)]


def test_a_later_rescore_never_picks_again(world: World) -> None:
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)
    score(world, lead_id)  # no capacity yet
    warm(world, 30)
    world.clock.set(LATER + timedelta(minutes=5))

    score(world, lead_id)

    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]
    assert item_row(world, item_id) == (1, "over_capacity", stamp(LATER))
    assert len(world.events("signal_inbox_items", item_id)) == 1


def test_a_disqualified_lead_is_recorded_without_a_pick(world: World) -> None:
    warm(world, 30)
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)

    class Disqualifying:
        def rescore(self, lead_id: str, *, use_current: bool = False) -> RescoreOutcome:
            return RescoreOutcome(RescoreResult.DISQUALIFIED, evidence_id=new_id())

    score(world, lead_id, scoring=Disqualifying())

    assert item_row(world, item_id) == (1, "disqualified", stamp(LATER))
    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM daily_capacity") == [(0,)]  # no pick at all
    assert refusals(world) == [(lead_id, lead_name(world, lead_id), "disqualified")]


def test_a_skipped_score_leaves_the_item_undecided(world: World) -> None:
    warm(world, 30)
    survivor, _ = scored(world, Tier.B, 60)
    merged, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, merged)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, merged))

    assert score(world, merged).result is RescoreResult.SKIPPED

    assert item_row(world, item_id) == (1, None, None)
    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]


def test_a_refused_lead_picked_by_hand_leaves_the_line_and_the_top(world: World) -> None:
    lead_id, _ = scored(world, Tier.B, 60)
    inbox_item(world, lead_id)
    score(world, lead_id)
    assert [each[0] for each in refusals(world)] == [lead_id]
    warm(world, 30)

    with world.units.open():
        [picked] = selection_of(world).pick([lead_id])

    assert picked.enrollment_id is not None
    assert refusals(world) == []
    assert lead_id not in [each[0] for each in ready(world)]


def test_without_selection_and_discovery_scoring_never_picks(world: World) -> None:
    warm(world, 30)
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)

    score(world, lead_id, wired=False)

    assert item_row(world, item_id) == (1, None, None)
    assert world.sql("SELECT COUNT(*) FROM enrollments") == [(0,)]


# The refusals ----------------------------------------------------------------------------------


def test_refusals_are_newest_first_one_per_lead_and_last_seven_days(world: World) -> None:
    older, _ = scored(world, Tier.B, 60)
    newer, _ = scored(world, Tier.B, 60)
    merged, _ = scored(world, Tier.B, 60)
    for lead_id in (older, newer, merged):
        inbox_item(world, lead_id)
    score(world, older)
    world.clock.set(LATER + timedelta(minutes=1))
    score(world, newer)
    score(world, merged)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (older, merged))

    assert [each[0] for each in refusals(world)] == [newer, older]

    world.clock.set(LATER + timedelta(days=7, seconds=30))  # older is now 7 days and 30 s old
    assert [each[0] for each in refusals(world)] == [newer]
    world.clock.set(LATER + timedelta(days=7, minutes=2))
    assert refusals(world) == []
    assert ready(world)[0][1] is False


def test_record_draft_outcome_takes_a_code(world: World) -> None:
    lead_id, _ = scored(world, Tier.B, 60)
    item_id = inbox_item(world, lead_id)
    for bad in ("Enrolled", "has space", "x" * 33, ""):
        with pytest.raises(ValueError):
            world.transact(lambda bad=bad: world.discovery.record_draft_outcome(lead_id, bad))
    assert world.transact(lambda: world.discovery.record_draft_outcome("nope", "enrolled")) == 0
    assert item_row(world, item_id) == (1, None, None)
    assert world.transact(lambda: world.discovery.record_draft_outcome(lead_id, "x" * 32)) == 1
    assert world.transact(lambda: world.discovery.record_draft_outcome(lead_id, "enrolled")) == 0


def test_the_repo_never_overwrites_a_decided_item(world: World) -> None:
    """Review patch: the repo itself only decides processed, ticked, undecided items."""
    lead_id, _ = scored(world, Tier.B, 60)
    decided = inbox_item(world, lead_id)
    unticked = inbox_item(world, lead_id, draft=False)
    at = world.clock.now()
    assert world.transact(lambda: signal_inbox_repo.record_draft_outcome([decided], "x", at)) == 1

    later = at + timedelta(minutes=1)
    again = world.transact(
        lambda: signal_inbox_repo.record_draft_outcome([decided, unticked], "enrolled", later)
    )

    assert again == 0
    assert item_row(world, decided) == (1, "x", stamp(at))
    assert item_row(world, unticked) == (0, None, None)


def test_pick_one_takes_a_str(world: World) -> None:
    with pytest.raises(TypeError):
        world.transact(lambda: selection_of(world).pick_one(1))  # type: ignore[arg-type]


def test_build_app_wires_lead_scores_to_the_selection_and_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review patch: ``build_app`` hands ``LeadScores`` a ``Discovery`` and the app's own
    ``Selection``; without either, scoring would silently never pick."""
    from nextron.bootstrap import load_bootstrap  # noqa: PLC0415
    from tests.fakes.secrets import InMemorySecrets  # noqa: PLC0415
    from tests.fakes.units import FakeUnits  # noqa: PLC0415

    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[LeadScores] = []
    registries: list[dict[str, Any]] = []
    apps: list[dict[str, Any]] = []
    real_scores, real_job_types, real_create_app = (
        main.LeadScores,
        main.build_job_types,
        main.create_app,
    )

    def lead_scores_of(*args: Any, **kwargs: Any) -> LeadScores:
        built.append(real_scores(*args, **kwargs))
        return built[-1]

    def job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_job_types(**kwargs)

    def create_app(**kwargs: Any) -> Any:
        apps.append(kwargs)
        return real_create_app(**kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "LeadScores", lead_scores_of)
    monkeypatch.setattr(main, "build_job_types", job_types)
    monkeypatch.setattr(main, "create_app", create_app)

    main.build_app(load_bootstrap(), units=FakeUnits())

    [scores] = built
    [given] = registries
    [app_kwargs] = apps
    assert isinstance(scores._discovery, Discovery)  # noqa: SLF001
    assert isinstance(scores._selection, Selection)  # noqa: SLF001
    assert scores._selection is app_kwargs["selection"]  # noqa: SLF001
    assert given["scores"]() is scores
    assert app_kwargs["lead_scores"] is scores
