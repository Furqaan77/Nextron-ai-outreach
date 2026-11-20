"""Story 8.11: restart a completed Sequence only on a new Event at least 90 days later (FR-63,
AD-4, AD-16), on the world of ``tests.app.test_enroll`` (a temporary database at head,
``FrozenClock``, the real subscriber registry, the shipped seeds, Law-PrivateAI x US active, one
Sending Inbox), through real units of work.

- **The candidates** (``queries.sequencing.restart_candidates`` and
  ``Selection.restart_candidates``): the frozen-``now`` matrix rows (too early; old Event only,
  a live Tier 2 Event first seen before the Sequence ended; happy; stopped; Not Now / Lost;
  Tier 3 / retracted / dead, the dead one a Tier 1 Event first seen after the end but 100 days
  old), each candidate with its restart Event, ordered by ``ready_order`` on that Event's age;
  an unscored Lead left out; a completed restart never restarts again.
- **The restart** (``Selection.restart``): origin ``restart``, the stored Event, the next
  Variant, the inbox, the Step slots and the ``events`` row; ``restart_event_id`` immutable;
  ``restart_context`` names the earlier Sequence, its sent Steps and the Event (``None`` for a
  first Enrollment or an earlier one that hasn't ended); no candidate writes nothing.
- **``enroll(origin=restart)``**: the restart check runs straight after the gate, so a wrong,
  foreign, older, Tier 3 Event, another Contact of the Lead, another valid Campaign, too early
  or an old Event only is refused ``restart_not_allowed``; a suppressed Contact is ``blocked``
  with nothing written; ``restart_event_id`` is bound by the trigger alone; the argument checks.
- **Eligibility** now reads the Lead's live Tier 1/2 Event times (FR-63 for every origin), but
  only a restart re-contacts the Contact (AD-21, loop 2): ``evaluate_eligibility(...,
  restart=True)`` passes, the default Decision (the Ready list, an ``operator_pick`` or a
  ``selection``) carries ``recontact_not_allowed``, next to any other failing reason.
- **A Lead restarts once**: after a restart completes and another Contact of the Lead is
  enrolled and completes, a newer Event never makes it a candidate.
- **Not Interested**: the suppression a Not Interested Reply writes (``reply_class``, person
  scope; this tree has no Reply path yet) closes the Lead ``lost``, so it is no candidate
  (``Selection.restart``: ``restart_not_allowed``) and ``enroll(restart)`` is ``blocked`` by the
  gate; nothing is written.
- **The Lead page** shows the restart line and the earlier Sequence's sent Steps with dates, and
  no restart line on the Drafts of an Enrollment that isn't a restart.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine

from nextron.app.compliance.api import Compliance
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.scoring import selection as selection_module
from nextron.app.scoring.selection import PickOutcome, PickReason, Selection
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup.api import Setup
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.contactability import BlockReason, ContactSubject, SuppressionScope
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import ReasonKind
from nextron.domain.enrollment import (
    EnrollmentOrigin,
    EnrollRefusal,
    NewEnrollment,
    RefusalCode,
)
from nextron.domain.scoring import Tier
from nextron.domain.suppression_entry import SuppressionSource
from nextron.web import leads_page
from tests.app.test_draft_revisions import FOOTER_SETTINGS, lead_page
from tests.app.test_enroll import OPS_US, T0, US_CAMPAIGN, World, build_world, stamp

RESTART = EnrollmentOrigin.RESTART
REPLY_ID = "01JD3QZ8X5V9K2M7N4P6R8T0RP"  # the Not Interested Reply's id (no replies table yet)


@pytest.fixture
def world(db_engine: Engine) -> World:
    found = build_world(db_engine)
    with found.units.open():
        Setup(found.clock).save(FOOTER_SETTINGS)
    return found


def now_of(world: World) -> datetime:
    return world.clock.now()


def completed(
    world: World,
    *,
    days: int = 90,
    ended_days: int | None = None,
    steps: tuple[int, ...] = (1,),
    add_variant: bool = False,
) -> tuple[str, str, str]:
    """A Lead whose Contact's Sequence completed without a Reply, its last Step ``days`` days
    before now (each Step sent then, its Draft ``sent``) and its end ``ended_days`` days before
    now (default: at the last Step); the Lead's, Contact's and Enrollment's ids. The Enrollment
    is created 30 days before its last Step."""
    now = now_of(world)
    sent = now - timedelta(days=days)
    ended = sent if ended_days is None else now - timedelta(days=ended_days)
    lead_id, contact_id = world.contact()
    world.clock.set(sent - timedelta(days=30))
    enrolled = world.enroll(contact_id, world.campaign(US_CAMPAIGN), steps=steps)
    world.clock.set(now)
    assert isinstance(enrolled, NewEnrollment), enrolled
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (stamp(sent), stamp(ended), enrolled.enrollment_id),
    )
    world.sql(
        "UPDATE drafts SET status = 'sent', status_at = ? WHERE enrollment_id = ?",
        (stamp(sent), enrolled.enrollment_id),
    )
    world.sql("UPDATE leads SET pipeline_stage = 'contacted' WHERE id = ?", (lead_id,))
    return lead_id, contact_id, enrolled.enrollment_id


def event(
    world: World,
    lead_id: str,
    *,
    days_ago: int = 5,
    tier: int = 1,
    signal_class: str = "event",
    score_only: bool = False,
) -> str:
    """A Signal of the Lead first seen ``days_ago`` days before now, with one live Evidence."""
    seen = now_of(world) - timedelta(days=days_ago)

    def store() -> str:
        evidence = signals_repo.insert_evidence(
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
            retrieved_at=seen,
            extracted_by="ai:extract_signals@v1",
            person_contact_id=None,
            at=seen,
        )
        return signals_repo.insert_signal(
            lead_id=lead_id,
            signal_type="law_hiring",
            signal_class=signal_class,
            tier=tier,
            score_only=score_only,
            event_date=None,
            first_seen_at=seen,
            evidence_ids=[evidence],
        )

    return world.transact(store)


def scored(world: World, lead_id: str, tier: Tier = Tier.A, score: int = 70) -> None:
    world.sql(
        "UPDATE leads SET tier = ?, lead_score = ?, fit_score = ?, signal_score = ?, "
        "tier_computed_at = ? WHERE id = ?",
        (str(tier), score, score, score, stamp(now_of(world)), lead_id),
    )


def selection_of(world: World) -> Selection:
    return Selection(world.clock, Sequencing(world.clock), Compliance(world.clock))


def candidates(world: World) -> list[tuple[str, str]]:
    found = world.read(lambda: sequencing_queries.restart_candidates(now_of(world)))
    return [(candidate.lead_id, candidate.event_id) for candidate in found]


def restart(world: World, lead_id: str) -> PickOutcome:
    selection = selection_of(world)
    with world.units.open():
        return selection.restart(lead_id)


def enroll_restart(
    world: World, contact_id: str, event_id: str, campaign: str = US_CAMPAIGN
) -> NewEnrollment | EnrollRefusal:
    sequencing = Sequencing(world.clock)
    campaign_id = world.campaign(campaign)
    return world.transact(
        lambda: sequencing.enroll(contact_id, campaign_id, RESTART, (1,), restart_event_id=event_id)
    )


def refused(world: World, contact_id: str, event_id: str) -> EnrollRefusal:
    before = world.counts()
    found = enroll_restart(world, contact_id, event_id)
    assert isinstance(found, EnrollRefusal)
    assert world.counts() == before  # nothing written
    return found


# The candidates -------------------------------------------------------------------------------


def test_the_matrix_lists_exactly_the_happy_rows_with_their_event(world: World) -> None:
    happy, _, _ = completed(world)
    happy_event = event(world, happy)
    early, _, _ = completed(world, days=89)
    event(world, early)
    old, _, _ = completed(world, days=120, ended_days=60)
    event(world, old, days_ago=61, tier=2)  # live, but first seen before the Sequence ended
    stopped, _, stopped_enrollment = completed(world)
    world.sql(
        "UPDATE enrollments SET status = 'stopped', stop_cause = 'operator_stop' WHERE id = ?",
        (stopped_enrollment,),
    )
    event(world, stopped)
    not_now, _, _ = completed(world)
    event(world, not_now)
    world.sql("UPDATE leads SET pipeline_stage = 'not_now' WHERE id = ?", (not_now,))
    lost, _, _ = completed(world)
    event(world, lost)
    world.sql("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (lost,))
    tier_3, _, _ = completed(world, days=200)
    event(world, tier_3, tier=3)
    event(world, tier_3, days_ago=100)  # Tier 1, first seen after the end, but dead (100 days)
    retracted, _, _ = completed(world)
    retracted_event = event(world, retracted)
    world.sql(
        "UPDATE signals SET retracted_at = ? WHERE id = ?",
        (stamp(now_of(world)), retracted_event),
    )
    score_only, _, _ = completed(world)
    event(world, score_only, score_only=True)
    event(world, score_only, signal_class="attribute")

    assert candidates(world) == [(happy, happy_event)]


def test_the_newest_event_is_the_restart_event_and_its_age_orders_the_candidates(
    world: World,
) -> None:
    first, _, _ = completed(world)
    event(world, first, days_ago=20)
    first_newest = event(world, first, days_ago=10)
    second, _, _ = completed(world)
    second_event = event(world, second, days_ago=3)
    third, _, _ = completed(world)
    third_event = event(world, third, days_ago=1)
    unscored, _, _ = completed(world)
    event(world, unscored)
    scored(world, first, Tier.A, 90)
    scored(world, second, Tier.A, 60)
    scored(world, third, Tier.B, 99)

    rows = world.read(selection_of(world).restart_candidates)

    assert [(row.ready.lead_id, row.restart.event_id) for row in rows] == [
        (second, second_event),  # Tier A, newest Event first
        (first, first_newest),
        (third, third_event),  # then Tier B
    ]
    assert [row.ready.newest_event_age for row in rows] == [3, 10, 1]
    assert {row.ready.lead_id for row in rows} == set(dict(candidates(world))) - {unscored}


# The restart ----------------------------------------------------------------------------------


def test_a_restart_enrolls_the_same_contact_and_campaign_on_the_event(world: World) -> None:
    world.add_variant(US_CAMPAIGN, "B")
    variants = world.variants(US_CAMPAIGN)
    lead_id, contact_id, previous = completed(world, steps=(1, 2))
    event_id = event(world, lead_id)
    [(inbox_id,)] = world.sql("SELECT id FROM sending_inboxes")
    before = world.counts()

    outcome = restart(world, lead_id)

    assert outcome.reason is None and outcome.enrollment_id is not None
    enrollment_id = outcome.enrollment_id
    assert world.sql(
        "SELECT lead_id, contact_id, campaign_id, variant_id, sending_inbox_id, origin, status, "
        "restart_event_id, created_at FROM enrollments WHERE id = ?",
        (enrollment_id,),
    ) == [
        (
            lead_id,
            contact_id,
            world.campaign(US_CAMPAIGN),
            variants["B"],  # the round-robin's next Variant
            inbox_id,
            "restart",
            "active",
            event_id,
            stamp(now_of(world)),
        )
    ]
    slots = world.sql(
        "SELECT step_no, status, due_at FROM drafts WHERE enrollment_id = ? ORDER BY step_no",
        (enrollment_id,),
    )
    assert [row[:2] for row in slots] == [(1, "ready"), (2, "ready"), (3, "ready"), (4, "ready")]
    assert slots[0][2] is not None  # Step 1 due at once
    assert world.events("enrollments", enrollment_id) == [
        (
            None,
            "active",
            "enrolled",
            world.campaign(US_CAMPAIGN),
            "system:sequencing",
            stamp(now_of(world)),
        )
    ]
    gained = {t: n - before[t] for t, n in world.counts().items() if n != before[t]}
    assert gained["enrollments"] == 1 and "jobs" not in gained
    context = world.read(lambda: sequencing_queries.restart_context(enrollment_id))
    assert context is not None
    ended = now_of(world) - timedelta(days=90)
    assert (context.previous_enrollment_id, context.ended_at, context.event_id) == (
        previous,
        ended,
        event_id,
    )
    assert [(step.step_no, step.sent_at) for step in context.sent_steps] == [(1, ended), (2, ended)]
    assert candidates(world) == []  # a live Enrollment now
    with pytest.raises(sqlite3.IntegrityError, match="bound columns"):
        world.sql(
            "UPDATE enrollments SET restart_event_id = NULL WHERE id = ?", (enrollment_id,)
        )


def test_no_candidate_restarts_nothing(world: World) -> None:
    lead_id, _, _ = completed(world, days=89)
    event(world, lead_id)
    before = world.counts()

    assert restart(world, lead_id) == PickOutcome(lead_id, None, "restart_not_allowed")
    assert restart(world, "not-a-ulid") == PickOutcome("not-a-ulid", None, "restart_not_allowed")
    assert world.counts() == before
    with pytest.raises(TypeError):
        restart(world, 7)  # type: ignore[arg-type]


def test_a_campaign_whose_sequence_is_gone_is_no_campaign(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead_id, _, _ = completed(world)
    event(world, lead_id)
    before = world.counts()
    monkeypatch.setattr(selection_module, "_steps_of", lambda _campaign: ())

    outcome = restart(world, lead_id)

    assert outcome == PickOutcome(lead_id, None, PickReason.NO_CAMPAIGN)
    assert world.counts() == before


def test_the_restart_context_is_none_for_another_enrollment(world: World) -> None:
    _, _, previous = completed(world)
    assert world.read(lambda: sequencing_queries.restart_context(previous)) is None
    assert world.read(lambda: sequencing_queries.restart_context("nope")) is None


# enroll(origin=restart) ---------------------------------------------------------------------------


def test_a_wrong_foreign_or_older_event_or_another_contact_is_refused(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    older = event(world, lead_id, days_ago=20)
    newest = event(world, lead_id, days_ago=2)
    tier_3 = event(world, lead_id, days_ago=1, tier=3)
    other_lead, other_contact, _ = completed(world)
    foreign = event(world, other_lead)

    for wrong in (older, tier_3, foreign, "01JD3QZ8X5V9K2M7N4P6R8T0AB"):
        assert refused(world, contact_id, wrong) == EnrollRefusal(RefusalCode.RESTART_NOT_ALLOWED)
    # the other Lead's Contact can't restart on this Lead's Event either
    assert refused(world, other_contact, newest) == EnrollRefusal(RefusalCode.RESTART_NOT_ALLOWED)
    assert isinstance(enroll_restart(world, contact_id, newest), NewEnrollment)


def test_a_restart_from_a_stopped_sequence_is_refused(world: World) -> None:
    lead_id, contact_id, enrollment_id = completed(world)
    event_id = event(world, lead_id)
    world.sql(
        "UPDATE enrollments SET status = 'stopped', stop_cause = 'operator_stop' WHERE id = ?",
        (enrollment_id,),
    )

    assert refused(world, contact_id, event_id) == EnrollRefusal(RefusalCode.RESTART_NOT_ALLOWED)


def test_too_early_or_an_old_event_only_is_restart_not_allowed(world: World) -> None:
    lead_id, contact_id, _ = completed(world, days=89)
    event_id = event(world, lead_id)
    old_lead, old_contact, _ = completed(world, days=120, ended_days=60)
    old_event = event(world, old_lead, days_ago=61, tier=2)  # first seen before the end

    assert refused(world, contact_id, event_id) == EnrollRefusal(RefusalCode.RESTART_NOT_ALLOWED)
    assert refused(world, old_contact, old_event) == EnrollRefusal(
        RefusalCode.RESTART_NOT_ALLOWED
    )


def test_a_suppressed_contact_is_blocked_and_nothing_is_written(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event_id = event(world, lead_id)
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    with world.units.open():
        Compliance(world.clock).add_value(email)
    before = world.counts()

    found = refused(world, contact_id, event_id)
    outcome = restart(world, lead_id)

    assert found.code is RefusalCode.BLOCKED
    assert BlockReason.ADDRESS_SUPPRESSED in found.blocks
    assert outcome.enrollment_id is None
    assert outcome.reason == "blocked"
    assert world.counts() == before  # no Enrollment, Draft, Task or channel Draft


def test_the_restart_event_is_given_exactly_for_a_restart(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event_id = event(world, lead_id)
    sequencing = Sequencing(world.clock)
    campaign_id = world.campaign(US_CAMPAIGN)

    with pytest.raises(ValueError, match="exactly for origin restart"):
        world.transact(lambda: sequencing.enroll(contact_id, campaign_id, RESTART, (1,)))
    with pytest.raises(ValueError, match="exactly for origin restart"):
        world.transact(
            lambda: sequencing.enroll(
                contact_id,
                campaign_id,
                EnrollmentOrigin.SELECTION,
                (1,),
                restart_event_id=event_id,
            )
        )
    with pytest.raises(TypeError, match="restart Event id"):
        world.transact(
            lambda: sequencing.enroll(
                contact_id, campaign_id, RESTART, (1,), restart_event_id=7  # type: ignore[arg-type]
            )
        )


def test_another_contact_of_the_lead_is_refused_on_the_right_event(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event_id = event(world, lead_id)
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    domain = email.split("@", 1)[1]
    added = world.transact(
        lambda: world.research.add_contact(
            lead_id,
            ContactCandidate(name="Yara Pell", email=f"yara@{domain}"),
            source=ProvenanceSource.WEBSITE,
            detail="Firm website team page",
            retrieved_at=T0,
        )
    )
    other_contact = added.contact_id
    assert other_contact is not None and other_contact != contact_id

    assert refused(world, other_contact, event_id) == EnrollRefusal(
        RefusalCode.RESTART_NOT_ALLOWED
    )
    assert isinstance(enroll_restart(world, contact_id, event_id), NewEnrollment)


def test_another_valid_campaign_is_refused_on_the_right_event(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event_id = event(world, lead_id)
    ops_campaign = world.activate(OPS_US)
    [(ops_segment,)] = world.sql("SELECT segment_id FROM campaigns WHERE id = ?", (ops_campaign,))
    # the Lead moves to the other Campaign's Segment, so that Campaign is active and matches
    world.sql("UPDATE leads SET segment_id = ? WHERE id = ?", (ops_segment, lead_id))
    assert world.read(lambda: setup_queries.campaign_active(ops_campaign))
    before = world.counts()

    found = enroll_restart(world, contact_id, event_id, campaign=OPS_US)

    assert found == EnrollRefusal(RefusalCode.RESTART_NOT_ALLOWED)
    assert world.counts() == before


@pytest.mark.parametrize("origin", [EnrollmentOrigin.OPERATOR_PICK, EnrollmentOrigin.SELECTION])
def test_an_operator_pick_never_re_contacts_an_unanswered_contact(
    world: World, origin: EnrollmentOrigin
) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    assert world.read(
        lambda: Compliance(world.clock).evaluate_eligibility(contact_id, restart=True)
    ).eligible
    default = world.read(lambda: Compliance(world.clock).evaluate_eligibility(contact_id))
    assert default.kinds == (ReasonKind.RECONTACT_NOT_ALLOWED,)
    before = world.counts()

    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN), origin=origin)

    assert isinstance(found, EnrollRefusal)
    assert found.code is RefusalCode.NOT_ELIGIBLE
    assert [reason.kind for reason in found.reasons] == [ReasonKind.RECONTACT_NOT_ALLOWED]
    assert world.counts() == before
    outcome = restart(world, lead_id)
    assert outcome.reason is None and outcome.enrollment_id is not None


@pytest.mark.parametrize("origin", [EnrollmentOrigin.OPERATOR_PICK, EnrollmentOrigin.SELECTION])
def test_a_refusal_keeps_recontact_not_allowed_next_to_another_reason(
    world: World, origin: EnrollmentOrigin
) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    world.sql("DELETE FROM provenance_records WHERE contact_id = ?", (contact_id,))
    before = world.counts()

    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN), origin=origin)

    assert isinstance(found, EnrollRefusal)
    assert found.code is RefusalCode.NOT_ELIGIBLE
    kinds = [reason.kind for reason in found.reasons]
    assert set(kinds) == {ReasonKind.PROVENANCE_MISSING, ReasonKind.RECONTACT_NOT_ALLOWED}
    assert kinds.count(ReasonKind.RECONTACT_NOT_ALLOWED) == 1
    assert world.counts() == before


def test_the_ready_list_shows_an_unanswered_lead_as_not_eligible(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    scored(world, lead_id)
    world.transact(lambda: world.research.make_primary(lead_id, contact_id))

    rows = world.read(selection_of(world).ready_list)

    [row] = [each for each in rows if each.lead_id == lead_id]
    assert not row.eligibility.eligible
    assert row.eligibility.kinds == (ReasonKind.RECONTACT_NOT_ALLOWED,)
    assert world.read(
        lambda: Compliance(world.clock).evaluate_eligibility(contact_id, restart=True)
    ).eligible


def test_a_lead_restarts_once_even_after_another_contact_completes(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    first = restart(world, lead_id)
    assert first.enrollment_id is not None
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (stamp(now_of(world)), stamp(now_of(world)), first.enrollment_id),
    )
    # another Contact of the Lead, with no history of its own, is picked and completes
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    domain = email.split("@", 1)[1]
    added = world.transact(
        lambda: world.research.add_contact(
            lead_id,
            ContactCandidate(name="Yara Pell", email=f"yara@{domain}"),
            source=ProvenanceSource.WEBSITE,
            detail="Firm website team page",
            retrieved_at=T0,
        )
    )
    other_contact = added.contact_id
    assert other_contact is not None
    world.clock.set(now_of(world) + timedelta(days=1))
    picked = world.enroll(
        other_contact, world.campaign(US_CAMPAIGN), origin=EnrollmentOrigin.OPERATOR_PICK
    )
    assert isinstance(picked, NewEnrollment), picked
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (stamp(now_of(world)), stamp(now_of(world)), picked.enrollment_id),
    )
    world.sql("UPDATE leads SET pipeline_stage = 'contacted' WHERE id = ?", (lead_id,))
    world.clock.set(now_of(world) + timedelta(days=100))
    newer = event(world, lead_id, days_ago=2)
    facts = world.read(lambda: sequencing_queries.restart_facts(lead_id))
    assert facts.latest is not None and facts.latest.enrollment_id == picked.enrollment_id
    assert facts.restarted
    before = world.counts()

    assert candidates(world) == []
    assert restart(world, lead_id) == PickOutcome(lead_id, None, "restart_not_allowed")
    assert refused(world, other_contact, newer) == EnrollRefusal(
        RefusalCode.RESTART_NOT_ALLOWED
    )
    assert world.counts() == before


def test_a_not_interested_contact_is_blocked_and_nothing_is_written(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event_id = event(world, lead_id)
    # what a Not Interested Reply writes (this tree has no Reply path yet): a person-scope
    # suppression of the Contact with source reply_class
    world.transact(
        lambda: Compliance(world.clock).suppress(
            ContactSubject(contact_id),
            SuppressionScope.PERSON,
            SuppressionSource.REPLY_CLASS,
            REPLY_ID,
        )
    )
    before = world.counts()

    outcome = restart(world, lead_id)
    found = refused(world, contact_id, event_id)  # enroll(restart) asked directly

    # the opt-out closed the Lead lost (Pipeline.close_suppressed): no longer a candidate
    assert world.stage(lead_id) == "lost"
    assert candidates(world) == []
    assert outcome == PickOutcome(lead_id, None, "restart_not_allowed")
    assert found.code is RefusalCode.BLOCKED  # the gate answers first
    assert world.counts() == before  # no Enrollment, Draft, Task or channel Draft


def test_a_completed_restart_never_restarts_again(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    first = restart(world, lead_id)
    assert first.enrollment_id is not None
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (stamp(now_of(world)), stamp(now_of(world)), first.enrollment_id),
    )
    world.clock.set(now_of(world) + timedelta(days=100))
    newer = event(world, lead_id, days_ago=2)
    before = world.counts()

    assert candidates(world) == []
    assert restart(world, lead_id) == PickOutcome(lead_id, None, "restart_not_allowed")
    assert refused(world, contact_id, newer) == EnrollRefusal(RefusalCode.RESTART_NOT_ALLOWED)
    assert world.counts() == before


def test_the_bound_trigger_alone_refuses_another_valid_restart_event(world: World) -> None:
    lead_id, _, _ = completed(world)
    other = event(world, lead_id, days_ago=4)
    newest = event(world, lead_id, days_ago=2)
    outcome = restart(world, lead_id)
    assert outcome.enrollment_id is not None
    assert world.sql(
        "SELECT restart_event_id FROM enrollments WHERE id = ?", (outcome.enrollment_id,)
    ) == [(newest,)]

    # both are valid Events, so ck_enrollments_restart_event holds: only the trigger refuses
    with pytest.raises(sqlite3.IntegrityError, match="bound columns"):
        world.sql(
            "UPDATE enrollments SET restart_event_id = ? WHERE id = ?",
            (other, outcome.enrollment_id),
        )


def test_the_restart_context_is_none_for_a_first_or_an_unended_previous_enrollment(
    world: World,
) -> None:
    lead_id, _, previous = completed(world)
    event(world, lead_id)
    outcome = restart(world, lead_id)
    restarted = outcome.enrollment_id
    assert restarted is not None

    def context() -> object:
        return world.read(lambda: sequencing_queries.restart_context(restarted))

    assert context() is not None
    # the earlier Enrollment hasn't ended: raw SQL marks the restart completed and sets the
    # earlier one back to active with no ended_at (one live Enrollment, so the index holds)
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (stamp(now_of(world)), stamp(now_of(world)), restarted),
    )
    world.sql(
        "UPDATE enrollments SET status = 'active', ended_at = NULL WHERE id = ?", (previous,)
    )
    assert context() is None
    # the restart is the Lead's first Enrollment (created_at is bound: drop its trigger)
    world.sql("DROP TRIGGER trg_enrollments_bound")
    later = stamp(now_of(world) + timedelta(days=1))
    world.sql("UPDATE enrollments SET created_at = ? WHERE id = ?", (later, previous))
    assert context() is None


# Eligibility --------------------------------------------------------------------------------------


def test_eligibility_passes_after_a_new_event_90_days_on(world: World) -> None:
    lead_id, contact_id, _ = completed(world, days=120, ended_days=60)

    def eligible(*, restart: bool = True) -> bool:
        compliance = Compliance(world.clock)
        return world.read(
            lambda: compliance.evaluate_eligibility(contact_id, restart=restart)
        ).eligible

    assert not eligible()
    event(world, lead_id, tier=3)
    assert not eligible()
    event(world, lead_id, days_ago=61, tier=2)  # live, but first seen before the end
    assert not eligible()
    event(world, lead_id, tier=2)
    assert eligible()
    assert not eligible(restart=False)  # only a restart re-contacts the Contact (AD-21)
    with pytest.raises(TypeError, match="restart as a bool"):
        eligible(restart=1)  # type: ignore[arg-type]


def test_a_live_restart_is_eligible_with_the_default_and_on_the_lead_page(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    recontact = {"data-reason": str(ReasonKind.RECONTACT_NOT_ALLOWED)}

    def default() -> object:
        return world.read(lambda: Compliance(world.clock).evaluate_eligibility(contact_id))

    assert default().kinds == (ReasonKind.RECONTACT_NOT_ALLOWED,)  # before: no live restart
    assert len(lead_page(world, lead_id).find_all("li", **recontact)) == 1
    outcome = restart(world, lead_id)
    assert outcome.enrollment_id is not None

    # the Contact's own live restart Enrollment counts as restart=True (loop 3, I1)
    assert default().eligible
    assert lead_page(world, lead_id).find_all("li", **recontact) == []
    # once it has ended, the default carries recontact_not_allowed again
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (stamp(now_of(world)), stamp(now_of(world)), outcome.enrollment_id),
    )
    assert default().kinds == (ReasonKind.RECONTACT_NOT_ALLOWED,)


def test_a_live_restart_of_another_contact_keeps_the_rule(world: World) -> None:
    lead_id, contact_id, _ = completed(world)
    event(world, lead_id)
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    domain = email.split("@", 1)[1]
    added = world.transact(
        lambda: world.research.add_contact(
            lead_id,
            ContactCandidate(name="Yara Pell", email=f"yara@{domain}"),
            source=ProvenanceSource.WEBSITE,
            detail="Firm website team page",
            retrieved_at=T0,
        )
    )
    other_contact = added.contact_id
    assert other_contact is not None
    outcome = restart(world, lead_id)
    assert outcome.enrollment_id is not None
    # the live restart belongs to another Contact of the Lead (contact_id is bound: raw SQL
    # with the trigger dropped)
    world.sql("DROP TRIGGER trg_enrollments_bound")
    world.sql(
        "UPDATE enrollments SET contact_id = ? WHERE id = ?", (other_contact, outcome.enrollment_id)
    )

    found = world.read(lambda: Compliance(world.clock).evaluate_eligibility(contact_id))

    assert found.kinds == (ReasonKind.RECONTACT_NOT_ALLOWED,)  # not this Contact's restart


# The Lead page ------------------------------------------------------------------------------------


def test_the_lead_page_shows_the_restart_line_and_the_earlier_steps(world: World) -> None:
    lead_id, _, _ = completed(world, steps=(1, 2))
    event(world, lead_id)
    outcome = restart(world, lead_id)
    assert outcome.enrollment_id is not None
    ended = (now_of(world) - timedelta(days=90)).date().isoformat()

    page = lead_page(world, lead_id)

    banners = page.find_all("div", class_="draft-restart")
    assert len(banners) == 4  # each Draft of the restart Enrollment, none of the earlier one
    line = banners[0].find("p", class_="draft-restart-line")
    assert line.text == f"Restart: previous Sequence ended {ended} without a Reply"
    steps = [item.text for item in banners[0].find_all("li")]
    assert steps == [f"Step 1 sent {ended}", f"Step 2 sent {ended}"]


def test_a_draft_source_without_a_restart_has_no_banner(world: World) -> None:
    assert leads_page.DraftRow.__dataclass_fields__["restart"].default is None
    assert leads_page.DraftSource.__dataclass_fields__["restart"].default is None
    lead_id, _, _ = completed(world, steps=(1, 2))  # a selection Enrollment, not a restart

    page = lead_page(world, lead_id)

    assert len(page.find_all("article", class_="draft")) == 2  # one per Step sent
    assert page.find_all("div", class_="draft-restart") == []
