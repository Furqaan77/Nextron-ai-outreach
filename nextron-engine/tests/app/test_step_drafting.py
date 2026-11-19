"""Drafting Step 1 with Opus from Evidence and facts (FR-20, FR-21, AD-6, AD-7, AD-8, AD-16,
AD-17, AD-18; Story 4.15), on the world of ``tests.app.test_draft_revisions`` (a temporary
database at head, the real subscriber registry, the shipped seeds, Law-PrivateAI x US, the footer
settings saved), with ``ScriptedLlm`` and the shipped ``draft_sequence`` tuple activated through
the real 4.5 gate path (a stored passing run, then ``Playbook.activate``).

- ``enroll`` enqueues one ``draft_step:<enrollment>:1`` job in its own unit; ``request_steps``
  without a queue refuses before any write;
- the spec's I/O matrix through ``StepDrafts.draft``: happy, blocked, no tuple, refused,
  truncated, invalid, failed, a bad citation, a dead Signal, a re-run and the masked contact
  details; plus a retryable gateway error, a revision that can't be composed, a subject
  suppressed during the call and a later success that clears the failure;
- the budget at 100 %: drafting and its grounding check still run while ``extract_signals`` is
  refused;
- the Lead page's "Drafting failed" notice.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any, Final

import pytest

from nextron import main
from nextron.adapters.ai import routes
from nextron.app.compliance import api as compliance_api
from nextron.app.drafting import api as drafting_api
from nextron.app.drafting.api import Drafting
from nextron.app.drafting.generation import StepDraftOutcome, StepDrafts
from nextron.app.drafting.grounding import GroundingChecks
from nextron.app.playbook.api import ActivationTrigger, Playbook
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.playbook import test_runs as runs_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.ai import Urgency
from nextron.domain.ai_test_gate import ROUTE_SETS
from nextron.domain.budget import AiCall, allow
from nextron.domain.campaign import StepAngleNote
from nextron.domain.contactability import Allowed, Blocked, BlockReason
from nextron.domain.playbook import PlaybookSnapshot
from nextron.domain.sequences import SegmentAngle
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    LlmFailed,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmResult,
    LlmTruncated,
    LlmUsage,
)
from tests.app.test_draft_grounding_rules import GENERATED_BODY, HIRING, PROOF, lead_with_signals
from tests.app.test_draft_revisions import (
    lead_page,
    world,  # noqa: F401  (the fixture)
)
from tests.app.test_draft_slots import enrolled
from tests.app.test_enroll import World, queue_on
from tests.fakes.llm import ScriptedLlm
from tests.fakes.usage_meter import InMemoryUsageMeter

OPUS: Final = "claude-opus-5-5"
PLAYBOOK: Final = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Playbook.\n")
USAGE: Final = LlmUsage(requests=1, cost_micros=10)
SUBJECT: Final = "your intake coordinator role"
FAILED: Final = "SELECT draft_failed_at, draft_failed_reason, current_rev FROM drafts WHERE id = ?"


def activate(world: World, *routes_: str) -> dict[str, str]:
    """Sync the shipped prompts under ``PLAYBOOK`` and activate each of ``routes_`` through a
    stored passing run on its set; each route's tuple id."""
    files, defaults = main.load_prompts()
    with world.units.open():
        Playbook(world.clock).sync_prompts(files, defaults, PLAYBOOK)
    found: dict[str, str] = {}
    for route in routes_:
        [(tuple_id,)] = world.sql("SELECT id FROM ai_route_tuples WHERE route = ?", (route,))
        now = world.clock.now()
        test_set = ROUTE_SETS[route]

        def store(
            chosen: str = str(tuple_id), name: str = route, chosen_set: Any = test_set
        ) -> None:
            runs_repo.insert(
                tuple_id=chosen,
                test_set=chosen_set.value,
                outcome="passed",
                error=None,
                case_count=14,
                passed_count=14,
                metrics="[]",
                results="[]",
                cost_micros=0,
                started_at=now,
                finished_at=now,
                cases_sha256=playbook_queries.case_set_fingerprint(chosen_set, name),
            )

        world.transact(store)
        with world.units.open():
            Playbook(world.clock).activate(str(tuple_id), trigger=ActivationTrigger.TEST_PASSED)
        found[route] = str(tuple_id)
    return found


def ok(answer: dict[str, Any]) -> LlmOk:
    return LlmOk(answer, model=OPUS, usage=USAGE)


def answer(event: str, quote: str, **changes: Any) -> dict[str, Any]:
    """A clean Step 1 proposal citing the Lead's hiring Event, its quote and ``legal_ai``."""
    found: dict[str, Any] = {
        "subject": SUBJECT,
        "body": GENERATED_BODY,
        "claims": [
            {"span": HIRING, "evidence_id": quote, "fact_id": None, "fact_version": None},
            {"span": PROOF, "evidence_id": None, "fact_id": "legal_ai", "fact_version": 1},
        ],
        "cited_signal_id": event,
        "explanation": "The newest Event.",
    }
    return found | changes


def step_1(world: World) -> tuple[str, str, str, str, str, str]:
    """A new enrolled Contact with a live hiring Event: (lead, contact, enrollment, draft,
    event, quote)."""
    lead_id, contact_id, enrollment_id = enrolled(world, steps=(1, 2))
    event, quote, _absence = lead_with_signals(world, lead_id)
    [(draft_id,)] = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? AND step_no = 1", (enrollment_id,)
    )
    return lead_id, contact_id, enrollment_id, str(draft_id), event, quote


def draft(world: World, llm: ScriptedLlm, enrollment_id: str) -> StepDraftOutcome:
    with world.units.open():
        return StepDrafts(world.clock, llm, queue_on(world.clock)).draft(
            enrollment_id, 1, Urgency.SOON
        )


def revisions(world: World, draft_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT rev, origin, subject, body, evidence_ids, fact_versions, cited_signal_id, "
        "route_tuple_id, playbook_sha256, playbook_version, prompt_version FROM draft_revisions "
        "WHERE draft_id = ? ORDER BY rev",
        (draft_id,),
    )


def failure(world: World, draft_id: str) -> tuple[Any, ...]:
    [row] = world.sql(FAILED, (draft_id,))
    return row


def prompt_texts(llm: ScriptedLlm) -> list[str]:
    """Every text the calls sent: the blocks and the framing."""
    texts: list[str] = []
    for (_tuple, blocks, _urgency), framing in zip(llm.calls, llm.framings, strict=True):
        texts += [block.text for block in blocks]
        if framing is not None:
            texts += [framing.brief, framing.facts, framing.note]
    return texts


# The job ------------------------------------------------------------------------------------------


def test_enroll_enqueues_one_draft_step_job_for_step_1(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1, 2, 3, 4))

    rows = world.sql("SELECT idempotency_key, lane, payload FROM jobs WHERE type = 'draft_step'")

    assert [(key, lane) for key, lane, _ in rows] == [
        (f"draft_step:{enrollment_id}:1", "interactive")
    ]
    assert json.loads(rows[0][2]) == {"v": 1, "enrollment_id": enrollment_id, "step": 1}


def test_a_later_step_request_enqueues_no_draft_step(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    drafting = Drafting(world.clock, queue_on(world.clock))

    world.transact(lambda: drafting.request_steps(enrollment_id, (2,), Urgency.SOON))

    [(count,)] = world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'draft_step'")
    assert count == 1  # Step 1's only


def test_request_steps_without_a_queue_refuses_before_any_write(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    before = world.counts()

    with pytest.raises(RuntimeError, match="job queue"):
        world.transact(
            lambda: Drafting(world.clock).request_steps(enrollment_id, (2,), Urgency.SOON)
        )

    assert world.counts() == before


def test_a_bulk_step_1_request_enqueues_its_draft_step_on_the_bulk_lane(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(2,))
    drafting = Drafting(world.clock, queue_on(world.clock))

    world.transact(lambda: drafting.request_steps(enrollment_id, (1,), Urgency.BULK))

    assert world.sql("SELECT idempotency_key, lane FROM jobs WHERE type = 'draft_step'") == [
        (f"draft_step:{enrollment_id}:1", "bulk")
    ]


def test_re_requesting_an_existing_step_1_enqueues_no_second_draft_step(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    drafting = Drafting(world.clock, queue_on(world.clock))

    assert world.transact(lambda: drafting.request_steps(enrollment_id, (1,), Urgency.SOON)) == ()

    [(count,)] = world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'draft_step'")
    assert count == 1


# The I/O matrix -----------------------------------------------------------------------------------


def test_a_valid_proposal_stores_revision_1_and_enqueues_its_check(world: World) -> None:
    tuples = activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)
    llm = ScriptedLlm(ok(answer(event, quote)))

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.DRAFTED

    [row] = revisions(world, draft_id)
    assert row == (
        1,
        "generated",
        SUBJECT,
        GENERATED_BODY,
        json.dumps([quote]),
        json.dumps([["legal_ai", 1]]),
        event,
        tuples["draft_sequence"],
        PLAYBOOK.sha256,
        PLAYBOOK.version,
        "v1",
    )
    assert failure(world, draft_id) == (None, None, 1)
    [(key, lane)] = world.sql(
        "SELECT idempotency_key, lane FROM jobs WHERE type = 'check_grounding'"
    )
    assert (key, lane) == (f"check_grounding:{draft_id}:1", "interactive")
    # the claims reached the grounding rules: a clean set stays pending
    [(status, flags)] = world.sql(
        "SELECT status, flags FROM grounding_results WHERE draft_id = ?", (draft_id,)
    )
    assert (status, flags) == ("pending", "[]")
    # one call, the Opus tuple, the framing and one Lead's blocks
    [(route_tuple, blocks, urgency)] = llm.calls
    assert (route_tuple.route, route_tuple.model, urgency) == ("draft_sequence", OPUS, Urgency.SOON)
    [framing] = llm.framings
    assert framing is not None
    assert framing.brief.startswith("# Step brief") and "Step 1" in framing.brief
    assert "fact_id `legal_ai`, version 1" in framing.facts
    assert framing.note.endswith("English: American English")
    assert [b.source for b in blocks].count("contact") == 1
    assert {b.block_id for b in blocks if b.source == "evidence"} >= {quote}
    assert event in {b.block_id for b in blocks if b.source == "signal"}


def test_the_claims_reach_the_grounding_rules(world: World) -> None:
    activate(world, "draft_sequence")
    lead_id, _, enrollment_id, draft_id, event, quote = step_1(world)
    [(absence,)] = world.sql(
        "SELECT id FROM evidence WHERE lead_id = ? AND type = 'absence'", (lead_id,)
    )
    unhedged = answer(event, quote)
    unhedged["claims"][0] = {
        "span": HIRING,
        "evidence_id": absence,
        "fact_id": None,
        "fact_version": None,
    }

    assert draft(world, ScriptedLlm(ok(unhedged)), enrollment_id) is StepDraftOutcome.DRAFTED

    [(status, flags)] = world.sql(
        "SELECT status, flags FROM grounding_results WHERE draft_id = ?", (draft_id,)
    )
    assert status == "fail"
    assert "unhedged_absence" in {flag["rule"] for flag in json.loads(flags)}


def test_a_blocked_subject_gets_no_call_and_one_event(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, _, _ = step_1(world)
    asked: list[tuple[Any, ...]] = []

    def blocked(subject: Any, channel: Any, purpose: Any) -> Blocked:
        asked.append((subject, channel, purpose))
        return Blocked((BlockReason.ADDRESS_SUPPRESSED,))

    monkeypatch.setattr(compliance_api, "assert_contactable", blocked)
    llm = ScriptedLlm()

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.BLOCKED

    assert llm.calls == [] and revisions(world, draft_id) == []
    assert failure(world, draft_id) == (None, None, None)  # decision 6
    assert world.events("drafts", draft_id)[-1][:5] == (
        "ready",
        "drafting_blocked",
        str(BlockReason.ADDRESS_SUPPRESSED),
        enrollment_id,
        "system:drafting",
    )
    assert [(channel, purpose) for _, channel, purpose in asked] == [("email", "cold")]


def test_no_active_tuple_fails_with_no_route_and_no_call(world: World) -> None:
    _, _, enrollment_id, draft_id, _, _ = step_1(world)
    llm = ScriptedLlm()

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.FAILED

    at, reason, rev = failure(world, draft_id)
    assert (reason, rev) == ("no_route", None) and at is not None
    assert llm.calls == []


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (LlmRefused(None, model=OPUS, usage=USAGE), "refused"),
        (LlmTruncated(model=OPUS, usage=USAGE), "truncated"),
        (LlmInvalid(model=OPUS, usage=USAGE), "invalid"),
        (LlmFailed(False, "llm_rejected", 400, model=OPUS, usage=USAGE), "failed"),
    ],
)
def test_each_unusable_answer_stores_no_revision_and_its_reason(
    world: World, result: LlmResult, reason: str
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, _, _ = step_1(world)

    assert draft(world, ScriptedLlm(result), enrollment_id) is StepDraftOutcome.FAILED

    assert failure(world, draft_id)[1:] == (reason, None)
    assert revisions(world, draft_id) == []
    [(count,)] = world.sql("SELECT COUNT(*) FROM jobs WHERE type = 'check_grounding'")
    assert count == 0


@pytest.mark.parametrize(
    "bad",
    [
        "unknown_evidence",
        "unknown_fact",
        "uncited",
        "dead_signal",
        "attribute_signal",
        "two_line_subject",
    ],
)
def test_a_proposal_that_fails_the_code_checks_is_discarded(world: World, bad: str) -> None:
    activate(world, "draft_sequence")
    lead_id, _, enrollment_id, draft_id, event, quote = step_1(world)
    proposal = answer(event, quote)
    if bad == "unknown_evidence":
        proposal["claims"][0]["evidence_id"] = "01JD3QZ8X5V9K2M7N4P6R8T0AE"
    elif bad == "unknown_fact":
        proposal["claims"][1]["fact_version"] = 9
    elif bad == "uncited":
        proposal["claims"][0]["evidence_id"] = None
    elif bad == "dead_signal":  # an Event first seen and dated well past its life
        world.sql(
            "UPDATE signals SET event_date = '2025-01-01', first_seen_at = "
            "'2025-01-01T00:00:00.000000Z' WHERE id = ?",
            (event,),
        )
    elif bad == "attribute_signal":
        [(attribute,)] = world.sql(
            "SELECT id FROM signals WHERE lead_id = ? AND signal_class = 'attribute'", (lead_id,)
        )
        proposal["cited_signal_id"] = attribute
    else:
        proposal["subject"] = "line one\nline two"

    assert draft(world, ScriptedLlm(ok(proposal)), enrollment_id) is StepDraftOutcome.FAILED

    assert failure(world, draft_id)[1:] == ("invalid_proposal", None)
    assert revisions(world, draft_id) == []


def test_a_rerun_is_a_no_op_with_one_call(world: World) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)
    llm = ScriptedLlm(ok(answer(event, quote)))

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.DRAFTED
    assert draft(world, llm, enrollment_id) is StepDraftOutcome.SKIPPED

    assert len(llm.calls) == 1 and len(revisions(world, draft_id)) == 1


def test_a_missing_or_unready_slot_and_another_step_are_skipped(world: World) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, _, _ = step_1(world)
    world.sql("UPDATE drafts SET status = 'cancelled' WHERE id = ?", (draft_id,))
    llm = ScriptedLlm()

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.SKIPPED
    assert draft(world, llm, "01JD3QZ8X5V9K2M7N4P6R8T0ZZ") is StepDraftOutcome.SKIPPED
    with world.units.open():
        other = StepDrafts(world.clock, llm, queue_on(world.clock)).draft(
            enrollment_id, 2, Urgency.SOON
        )
    assert other is StepDraftOutcome.SKIPPED
    assert llm.calls == []


def test_contact_details_in_evidence_are_masked_and_no_contact_email_is_sent(
    world: World,
) -> None:
    activate(world, "draft_sequence")
    lead_id, contact_id, enrollment_id, _, event, quote = step_1(world)
    now = world.clock.now()
    world.transact(
        lambda: signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type="quote",
            quote="Write to careers@firm.example.com or call +44 20 7946 0958.",
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=("https://firm.example.com/careers",),
            source="website",
            source_url="https://firm.example.com/careers",
            retrieved_at=now,
            extracted_by="ai:extract_signals@v1",
            person_contact_id=None,
            at=now,
        )
    )
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    llm = ScriptedLlm(LlmRefused(None, model=OPUS, usage=USAGE))

    draft(world, llm, enrollment_id)

    texts = prompt_texts(llm)
    joined = "\n".join(texts)
    assert "Write to [email] or call [phone]." in joined
    assert "@" not in joined and "7946" not in joined
    assert email not in joined


def test_a_retryable_gateway_error_propagates_and_stores_nothing(world: World) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, _, _ = step_1(world)
    llm = ScriptedLlm(AdapterError(True, "llm_unavailable", "the Claude API could not be reached"))

    with pytest.raises(AdapterError):
        draft(world, llm, enrollment_id)

    assert failure(world, draft_id) == (None, None, None)
    assert revisions(world, draft_id) == []


def test_a_revision_that_cant_be_composed_fails_not_composable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("the footer is blank")

    monkeypatch.setattr(drafting_api, "_prepare", refuse)

    assert (
        draft(world, ScriptedLlm(ok(answer(event, quote))), enrollment_id)
        is StepDraftOutcome.FAILED
    )
    assert failure(world, draft_id)[1:] == ("not_composable", None)
    assert revisions(world, draft_id) == []


@pytest.mark.parametrize("gone", ["sequence_version", "segment"])
def test_a_context_that_cant_be_read_fails_not_composable_with_no_call(
    world: World, monkeypatch: pytest.MonkeyPatch, gone: str
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, _, _ = step_1(world)
    if gone == "sequence_version":  # the stored Sequence moved on since the enrollment
        stored = setup_queries.sequence_by_id

        def newer(sequence_id: str) -> Any:
            found = stored(sequence_id)
            assert found is not None
            return dataclasses.replace(found, version=found.version + 1)

        monkeypatch.setattr(setup_queries, "sequence_by_id", newer)
    else:
        monkeypatch.setattr(setup_queries, "segment_by_id", lambda _id: None)
    llm = ScriptedLlm()

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.FAILED

    assert llm.calls == []
    assert failure(world, draft_id)[1:] == ("not_composable", None)
    assert revisions(world, draft_id) == []


def test_an_erased_contact_is_skipped_with_no_call(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, _, _ = step_1(world)
    stored = research_queries.contact
    monkeypatch.setattr(
        research_queries,
        "contact",
        lambda contact_id: dataclasses.replace(stored(contact_id), erased_at=world.clock.now()),
    )
    llm = ScriptedLlm()

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.SKIPPED

    assert llm.calls == []
    assert revisions(world, draft_id) == [] and failure(world, draft_id) == (None, None, None)


def test_the_variant_note_and_the_segment_market_angle_reach_the_framing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, _, event, quote = step_1(world)
    campaign_of, sequence_of = setup_queries.campaign, setup_queries.sequence_by_id
    [(campaign_id,)] = world.sql(
        "SELECT campaign_id FROM enrollments WHERE id = ?", (enrollment_id,)
    )
    enrolled_in = world.transact(lambda: campaign_of(str(campaign_id)))
    assert enrolled_in is not None
    segment = world.transact(lambda: setup_queries.segment_by_id(enrolled_in.segment_id))
    market = world.transact(lambda: setup_queries.market_by_id(enrolled_in.market_id))
    assert segment is not None and market is not None
    angle = SegmentAngle(segment.key, market.code, "Name the filing backlog.")

    def campaign(campaign_id: str) -> Any:
        found = campaign_of(campaign_id)
        assert found is not None
        note = (StepAngleNote(1, "Lead with intake load."),)
        variants = tuple(dataclasses.replace(v, step_angles=note) for v in found.variants)
        return dataclasses.replace(found, variants=variants)

    def sequence(sequence_id: str) -> Any:
        found = sequence_of(sequence_id)
        assert found is not None
        steps = found.definition.steps
        first = dataclasses.replace(steps[0], segment_angles=(angle,))
        definition = dataclasses.replace(found.definition, steps=(first, *steps[1:]))
        return dataclasses.replace(found, definition=definition)

    monkeypatch.setattr(setup_queries, "campaign", campaign)
    monkeypatch.setattr(setup_queries, "sequence_by_id", sequence)
    llm = ScriptedLlm(ok(answer(event, quote)))

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.DRAFTED

    [framing] = llm.framings
    assert framing is not None
    assert "Lead with intake load." in framing.note
    assert "Name the filing backlog." in framing.brief


def test_a_subject_suppressed_during_the_call_stores_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)
    answers: list[Allowed | Blocked] = [
        Allowed(),
        Blocked((BlockReason.ADDRESS_SUPPRESSED,)),
    ]
    monkeypatch.setattr(compliance_api, "assert_contactable", lambda *_: answers.pop(0))

    outcome = draft(world, ScriptedLlm(ok(answer(event, quote))), enrollment_id)

    assert outcome is StepDraftOutcome.SKIPPED
    assert revisions(world, draft_id) == [] and failure(world, draft_id) == (None, None, None)


def test_a_later_success_clears_the_failure(world: World) -> None:
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)
    draft(world, ScriptedLlm(LlmRefused(None, model=OPUS, usage=USAGE)), enrollment_id)
    assert failure(world, draft_id)[1] == "refused"

    assert draft(world, ScriptedLlm(ok(answer(event, quote))), enrollment_id) is (
        StepDraftOutcome.DRAFTED
    )
    assert failure(world, draft_id) == (None, None, 1)


def test_the_db_refuses_a_half_recorded_failure(world: World) -> None:
    _, _, _, draft_id, _, _ = step_1(world)

    for assignments in (
        "draft_failed_at = '2026-11-18T10:00:00.000000Z'",
        "draft_failed_reason = 'refused'",
        "draft_failed_at = '2026-11-18T10:00:00.000000Z', draft_failed_reason = 'other'",
    ):
        with pytest.raises(Exception, match="CHECK constraint failed"):
            world.sql(f"UPDATE drafts SET {assignments} WHERE id = ?", (draft_id,))


# The budget ---------------------------------------------------------------------------------------


def test_at_full_ai_spend_drafting_and_grounding_run_while_extraction_is_refused(
    world: World,
) -> None:
    activate(world, "draft_sequence", "grounding_check")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)
    limits = world.read(setup_queries.budget_limits)
    meter = InMemoryUsageMeter(world.clock)
    meter.record(
        AiCall(
            "extract_signals",
            "claude-haiku-5-5:medium:v1:s1:aaaaaaaaaaaa",
            "claude-haiku-5-5",
            input_tokens=0,
            output_tokens=0,
            cost_micros=limits.ai_budget_micros,
        )
    )
    usage = meter.snapshot(world.clock.now())
    extraction = routes.default_candidate("extract_signals")
    drafting = routes.default_candidate("draft_sequence")
    grounding = routes.default_candidate("grounding_check")
    assert extraction is not None and drafting is not None and grounding is not None
    assert allow(extraction.budget_class, usage, limits) is False
    assert allow(drafting.budget_class, usage, limits) is True
    assert allow(grounding.budget_class, usage, limits) is True

    assert draft(world, ScriptedLlm(ok(answer(event, quote))), enrollment_id) is (
        StepDraftOutcome.DRAFTED
    )
    checked = ScriptedLlm(LlmOk({"claims": [], "flags": []}, model=OPUS, usage=USAGE))
    with world.units.open():
        GroundingChecks(world.clock, checked).check(draft_id, 1, Urgency.SOON)
    assert len(checked.calls) == 1


# The Lead page ------------------------------------------------------------------------------------


def test_the_lead_page_shows_a_drafting_failure_until_a_revision_exists(world: World) -> None:
    lead_id, _, enrollment_id, draft_id, _, _ = step_1(world)
    draft(world, ScriptedLlm(), enrollment_id)  # no tuple: no_route

    page = lead_page(world, lead_id)

    first, second = page.find("section", class_="lead-drafts").find_all("article", class_="draft")
    notice = first.find("p", class_="draft-failed")
    assert notice is not None
    assert notice.text == (
        "Drafting failed: no tested drafting model is active. Regenerate to retry."
    )
    assert second.find_all("p", class_="draft-failed") == []


def test_build_app_wires_step_drafts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron.bootstrap import load_bootstrap  # noqa: PLC0415
    from tests.fakes.secrets import InMemorySecrets  # noqa: PLC0415
    from tests.fakes.units import FakeUnits  # noqa: PLC0415

    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[StepDrafts] = []
    checks: list[GroundingChecks] = []
    registries: list[dict[str, Any]] = []
    real_drafts, real_checks = main.StepDrafts, main.GroundingChecks
    real_job_types = main.build_job_types

    def step_drafts(*args: Any) -> StepDrafts:
        built.append(real_drafts(*args))
        return built[-1]

    def grounding_checks(*args: Any) -> GroundingChecks:
        checks.append(real_checks(*args))
        return checks[-1]

    def job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_job_types(**kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "StepDrafts", step_drafts)
    monkeypatch.setattr(main, "GroundingChecks", grounding_checks)
    monkeypatch.setattr(main, "build_job_types", job_types)

    main.build_app(load_bootstrap(), units=FakeUnits())

    [drafts] = built
    [grounding] = checks
    assert drafts._llm is grounding._llm  # noqa: SLF001 - the app's one LLM gateway
    assert drafts._queue is not None  # noqa: SLF001
    [given] = registries
    assert given["step_drafts"]() is drafts
