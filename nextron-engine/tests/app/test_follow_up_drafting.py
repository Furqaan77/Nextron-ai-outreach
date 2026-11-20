"""Drafting the follow-up Steps 2 to 4 in the afternoon batch with a 07:00 deadline (AD-8, AD-10,
AD-16, AD-18; Story 4.18), on the world of ``tests.app.test_step_drafting`` (a temporary database
at head, the shipped seeds, Law-PrivateAI x US, the shipped facts) with ``ScriptedLlm`` and a
frozen clock. Nothing reaches Claude.

The spec's I/O matrix: enroll queues one pending drafting item; submit (one batch, the item
``submitted``, Step 1 with no revision stays pending, a paused Enrollment stays pending); applied
(revision 1 on Drafts 2 to 4, one ``check_grounding`` each, the batch deleted, a second poll
changes nothing); a repeated angle and a non-regulation Step 2 angle are discarded; an edited
Step 1 makes the result stale and queues a new item (or the direct job after the deadline); the
deadline sweep; a stopped Enrollment; a blocked Contact; a v1-only tuple; and the direct
``draft_step:<enrollment>:2`` job.
"""

import json
from datetime import timedelta
from typing import Any, Final

import pytest

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.drafting.api import MAX_BODY, Drafting
from nextron.app.drafting.batches import DraftingBatches
from nextron.app.drafting.generation import StepDraftOutcome, StepDrafts
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.db import ai_batches
from nextron.db.uow import current
from nextron.domain.ai import Urgency
from nextron.domain.ai_batch import BatchOwner, custom_id
from nextron.domain.contactability import Blocked, BlockReason
from nextron.domain.enrollment import NewEnrollment, PauseCause
from nextron.ports.llm import (
    BatchLost,
    BatchLostKind,
    BatchSucceeded,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmUsage,
)
from tests.app.test_draft_grounding_rules import GENERATED_BODY, HIRING, PROOF, lead_with_signals
from tests.app.test_draft_revisions import world  # noqa: F401  (the fixture)
from tests.app.test_draft_slots import edit_default, enrolled
from tests.app.test_enroll import (
    LIA_DATE,
    LIA_REF,
    UK_CAMPAIGN,
    UK_DECISION,
    World,
    queue_on,
)
from tests.app.test_step_drafting import PLAYBOOK, SUBJECT, activate
from tests.fakes.llm import ScriptedLlm

OPUS: Final = "claude-opus-5-5"
USAGE: Final = LlmUsage(requests=1, cost_micros=10)
ROUTE: Final = "draft_sequence"
ABA: Final = ("aba_formal_opinion_512", 1)
WISH: Final = ("wish_wheels", 1)
MP: Final = ("mp_online_digital", 1)
STEP_2_BODY: Final = "Hi Zelda,\n\nA short note on ABA guidance for AI tools.\n\nUseful?"
STEP_3_BODY: Final = "Hi Zelda,\n\nWe built a booking platform for a rental firm.\n\nRelevant?"
STEP_4_BODY: Final = "Hi Zelda,\n\nLast note from me on this.\n\nShall I close the file?"
ITEMS: Final = (
    "SELECT target_id, rev, status, fingerprint IS NOT NULL FROM ai_batch_items "
    "WHERE owner = 'drafting' ORDER BY rev"
)


def ok(answer: dict[str, Any]) -> LlmOk:
    return LlmOk(answer, model=OPUS, usage=USAGE)


def step_1_answer(event: str, quote: str) -> dict[str, Any]:
    """A clean schema-2 Step 1 answer: one ``steps`` entry, Step 1."""
    return {
        "steps": [
            {
                "step": 1,
                "subject": SUBJECT,
                "body": GENERATED_BODY,
                "claims": [
                    {"span": HIRING, "evidence_id": quote, "fact_id": None, "fact_version": None},
                    {"span": PROOF, "evidence_id": None, "fact_id": "legal_ai", "fact_version": 1},
                ],
                "angle_fact_id": None,
                "angle_fact_version": None,
            }
        ],
        "cited_signal_id": event,
        "explanation": "The newest Event.",
    }


def follow_up(step: int, body: str, angle: tuple[str, int] | None, span: str) -> dict[str, Any]:
    return {
        "step": step,
        "subject": "ignored",
        "body": body,
        "claims": []
        if angle is None
        else [{"span": span, "evidence_id": None, "fact_id": angle[0], "fact_version": angle[1]}],
        "angle_fact_id": None if angle is None else angle[0],
        "angle_fact_version": None if angle is None else angle[1],
    }


def follow_ups(
    step_2: tuple[str, int] | None = ABA,
    step_3: tuple[str, int] | None = WISH,
    step_4: tuple[str, int] | None = MP,
) -> dict[str, Any]:
    """A clean follow-up answer: Step 2 on the ABA opinion, Steps 3 and 4 on case studies."""
    return {
        "steps": [
            follow_up(2, STEP_2_BODY, step_2, "ABA guidance for AI tools"),
            follow_up(3, STEP_3_BODY, step_3, "We built a booking platform for a rental firm"),
            follow_up(4, STEP_4_BODY, step_4, "Last note from me on this"),
        ],
        "cited_signal_id": None,
        "explanation": "A new angle per Step.",
    }


class Follow:
    """One Enrollment with a live Event, the v2 tuple active and the services on one fake."""

    def __init__(
        self,
        world: World,
        llm: ScriptedLlm,
        *,
        step_1: bool = True,
        enroll: Any = enrolled,
    ) -> None:
        self.world = world
        self.llm = llm
        self.tuples = activate(world, ROUTE, version="v2")
        self.lead, self.contact, self.enrollment = enroll(world)
        self.event, self.quote, _ = lead_with_signals(world, self.lead)
        self.drafts = {
            int(step): str(draft_id)
            for draft_id, step in world.sql(
                "SELECT id, step_no FROM drafts WHERE enrollment_id = ?", (self.enrollment,)
            )
        }
        if step_1:
            llm.script.append(ok(step_1_answer(self.event, self.quote)))
            assert self.direct(1) is StepDraftOutcome.DRAFTED

    def steps(self) -> StepDrafts:
        return StepDrafts(self.world.clock, self.llm, queue_on(self.world.clock))

    def batches(self) -> DraftingBatches:
        clock = self.world.clock
        return DraftingBatches(clock, self.llm, queue_on(clock), self.steps())

    def direct(self, step: int) -> StepDraftOutcome:
        with self.world.units.open():
            return self.steps().draft(self.enrollment, step, Urgency.SOON)

    def submit(self) -> int:
        with self.world.units.open():
            return self.batches().submit_bulk()

    def poll(self) -> int:
        with self.world.units.open():
            return self.batches().poll()

    def deadline(self) -> Any:
        with self.world.units.open(read_only=True):
            stored = sequencing_queries.enrollment(self.enrollment)
            assert stored is not None
            return self.batches().deadline_of(stored.created_at)

    def custom(self, rev: int = 1) -> str:
        return custom_id(ROUTE, self.enrollment, rev)

    def end(self, answer: Any, rev: int = 1, batch: str = "msgbatch_fake_1") -> None:
        self.llm.end_batch(batch, {self.custom(rev): answer})

    def items(self) -> list[tuple[Any, ...]]:
        return self.world.sql(ITEMS)

    def revisions(self, step: int) -> list[tuple[Any, ...]]:
        return self.world.sql(
            "SELECT rev, origin, subject, body, evidence_ids, fact_versions, cited_signal_id, "
            "route_tuple_id, playbook_sha256, prompt_version FROM draft_revisions "
            "WHERE draft_id = ? ORDER BY rev",
            (self.drafts[step],),
        )

    def failed(self, step: int) -> tuple[Any, ...]:
        [row] = self.world.sql(
            "SELECT draft_failed_reason, current_rev FROM drafts WHERE id = ?",
            (self.drafts[step],),
        )
        return row

    def jobs(self, job_type: str) -> list[tuple[Any, ...]]:
        return self.world.sql(
            "SELECT idempotency_key, lane, payload FROM jobs WHERE type = ? ORDER BY rowid",
            (job_type,),
        )


def follow_up_job(found: Follow) -> tuple[str, str, dict[str, Any]]:
    [(key, lane, payload)] = found.jobs("draft_step")[1:]  # after Step 1's
    return key, lane, json.loads(payload)


# The queue ----------------------------------------------------------------------------------------


def test_enroll_queues_one_pending_drafting_item_for_the_follow_ups(world: World) -> None:
    _, _, enrollment_id = enrolled(world)

    assert world.sql(
        "SELECT owner, route, target_id, rev, context_id, status FROM ai_batch_items"
    ) == [("drafting", ROUTE, enrollment_id, 1, None, "pending")]
    [(custom,)] = world.sql("SELECT custom_id FROM ai_batch_items")
    assert custom == custom_id(ROUTE, enrollment_id, 1)


def test_a_step_1_only_request_queues_no_item_and_a_later_one_queues_the_next_rev(
    world: World,
) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    assert world.sql("SELECT COUNT(*) FROM ai_batch_items") == [(0,)]
    drafting = Drafting(world.clock, queue_on(world.clock))

    world.transact(lambda: drafting.request_steps(enrollment_id, (2, 3, 4), Urgency.SOON))
    world.transact(lambda: drafting.request_steps(enrollment_id, (2,), Urgency.SOON))  # present

    assert world.sql("SELECT rev, status FROM ai_batch_items") == [(1, "pending")]


# Submit -------------------------------------------------------------------------------------------


def test_the_submit_sends_one_batch_with_step_1_and_the_follow_up_briefs(world: World) -> None:
    found = Follow(world, ScriptedLlm())

    assert found.submit() == 1

    [(route_tuple, requests)] = found.llm.batches_submitted
    assert (route_tuple.route, route_tuple.prompt_version, route_tuple.schema_version) == (
        ROUTE,
        "v2",
        2,
    )
    [request] = requests
    assert request.custom_id == found.custom()
    first = request.inputs[0]
    assert (first.source, first.block_id) == ("earlier_step", "step_1")
    assert first.text == f"subject: {SUBJECT}\n\n{GENERATED_BODY}"
    assert {block.source for block in request.inputs[1:]} == {"evidence", "signal", "contact"}
    assert request.framing is not None
    assert "Write Steps 2, 3 and 4" in request.framing.brief
    assert "This email is Step 2 of the Sequence." in request.framing.brief
    assert "This email is Step 4 of the Sequence." in request.framing.brief
    assert "ABA Formal Opinion 512" in request.framing.brief  # Step 2's Law-PrivateAI US angle
    assert "aba_formal_opinion_512" in request.framing.facts
    assert found.items() == [(found.enrollment, 1, "submitted", 1)]
    assert found.llm.calls[1:] == []  # Step 1's call only: nothing waits on the batch


def test_a_step_1_without_a_revision_or_a_paused_enrollment_stays_pending(world: World) -> None:
    found = Follow(world, ScriptedLlm(), step_1=False)

    assert found.submit() == 0
    assert found.items() == [(found.enrollment, 1, "pending", 0)]

    found.llm.script.append(ok(step_1_answer(found.event, found.quote)))
    assert found.direct(1) is StepDraftOutcome.DRAFTED
    world.transact(
        lambda: found.world.sequencing.pause(found.enrollment, PauseCause.OPERATOR)
    )

    assert found.submit() == 0
    assert found.items() == [(found.enrollment, 1, "pending", 0)]
    assert found.llm.batches_submitted == []


def test_only_a_v1_tuple_fails_the_follow_ups_no_route_with_no_call(world: World) -> None:
    llm = ScriptedLlm()
    lead, _, enrollment = enrolled(world)
    event, quote, _ = lead_with_signals(world, lead)
    activate(world, ROUTE)  # v1 only
    llm.script.append(
        ok(
            {
                "subject": SUBJECT,
                "body": GENERATED_BODY,
                "claims": [],
                "cited_signal_id": event,
                "explanation": "v1.",
            }
        )
    )
    with world.units.open():
        steps = StepDrafts(world.clock, llm, queue_on(world.clock))
        assert steps.draft(enrollment, 1, Urgency.SOON) is StepDraftOutcome.DRAFTED
    _ = quote

    with world.units.open():
        sent = DraftingBatches(world.clock, llm, queue_on(world.clock), steps).submit_bulk()

    assert sent == 0
    assert llm.batches_submitted == []
    assert world.sql(ITEMS) == [(enrollment, 1, "stale", 0)]
    assert world.sql(
        "SELECT step_no, draft_failed_reason, current_rev FROM drafts WHERE enrollment_id = ? "
        "ORDER BY step_no",
        (enrollment,),
    ) == [(1, None, 1), (2, "no_route", None), (3, "no_route", None), (4, "no_route", None)]
    with world.units.open():
        assert steps.draft(enrollment, 2, Urgency.SOON) is StepDraftOutcome.FAILED
    assert len(llm.calls) == 1  # Step 1's only


# Apply --------------------------------------------------------------------------------------------


def test_an_ended_batch_writes_revision_1_on_drafts_2_to_4_and_is_deleted(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    assert found.poll() == 0  # in progress: nothing yet
    found.end(BatchSucceeded(ok(follow_ups())))

    assert found.poll() == 1
    assert found.poll() == 0  # a second poll changes nothing

    expected = {
        2: (STEP_2_BODY, [list(ABA)]),
        3: (STEP_3_BODY, [list(WISH)]),
        4: (STEP_4_BODY, [list(MP)]),
    }
    for step, (body, facts) in expected.items():
        assert found.revisions(step) == [
            (
                1,
                "generated",
                SUBJECT,  # Step 1's current subject: a follow-up replies in its thread
                body,
                "[]",
                json.dumps(facts),
                None,
                found.tuples[ROUTE],
                PLAYBOOK.sha256,
                "v2",
            )
        ]
        assert found.failed(step) == (None, 1)
        assert world.sql(
            "SELECT idempotency_key, lane FROM jobs WHERE type = 'check_grounding' AND "
            "idempotency_key LIKE ?",
            (f"check_grounding:{found.drafts[step]}:%",),
        ) == [(f"check_grounding:{found.drafts[step]}:1", "interactive")]
    assert found.items() == [(found.enrollment, 1, "applied", 1)]
    assert found.llm.deleted == ["msgbatch_fake_1"]
    assert world.sql("SELECT status FROM ai_batches") == [("deleted",)]


@pytest.mark.parametrize(
    "answer",
    [
        follow_ups(step_3=ABA),  # Step 3 repeats Step 2's angle
        follow_ups(step_3=("legal_ai", 1)),  # Step 3 repeats Step 1's fact
        follow_ups(step_2=WISH, step_3=ABA),  # Step 2's Law US angle isn't a regulation fact
        follow_ups(step_4=None),  # Step 4 has no angle
        follow_ups(step_4=("no_such_fact", 1)),  # not an input fact
    ],
    ids=["step_2_angle", "step_1_fact", "not_regulation", "no_angle", "unknown_fact"],
)
def test_an_answer_code_refuses_is_discarded_and_fails_the_item(
    world: World, answer: dict[str, Any]
) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.end(BatchSucceeded(ok(answer)))

    assert found.poll() == 1

    for step in (2, 3, 4):
        assert found.revisions(step) == []
        assert found.failed(step) == ("invalid_proposal", None)
    assert found.items() == [(found.enrollment, 1, "failed", 1)]
    assert found.llm.deleted == ["msgbatch_fake_1"]


def test_an_answer_missing_a_step_is_discarded(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    answer = follow_ups()
    answer["steps"] = answer["steps"][:2]
    found.end(BatchSucceeded(ok(answer)))

    found.poll()

    assert found.failed(4) == ("invalid_proposal", None)
    assert found.items() == [(found.enrollment, 1, "failed", 1)]


def test_an_invalid_answer_fails_the_item_and_the_drafts(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.end(BatchSucceeded(LlmInvalid(model=OPUS, usage=USAGE)))

    found.poll()

    assert [found.failed(step) for step in (2, 3, 4)] == [("invalid", None)] * 3
    assert found.items() == [(found.enrollment, 1, "failed", 1)]


@pytest.mark.parametrize(
    "result",
    [
        BatchSucceeded(LlmRefused(None, model=OPUS, usage=USAGE)),
        BatchLost(BatchLostKind.EXPIRED),
        None,
    ],
    ids=["refused", "expired", "missing"],
)
def test_a_refused_lost_or_missing_item_re_runs_as_the_direct_job(
    world: World, result: Any
) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.llm.end_batch("msgbatch_fake_1", {} if result is None else {found.custom(): result})

    assert found.poll() == 1

    assert found.items() == [(found.enrollment, 1, "rerun", 1)]
    assert follow_up_job(found) == (
        f"draft_step:{found.enrollment}:2",
        "interactive",
        {"v": 1, "enrollment_id": found.enrollment, "step": 2},
    )
    assert found.llm.deleted == ["msgbatch_fake_1"]


def test_an_edited_step_1_makes_the_result_stale_and_queues_the_next_item(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    world.transact(
        lambda: Drafting(world.clock, queue_on(world.clock)).revise(
            found.drafts[1], "a new subject", GENERATED_BODY
        )
    )
    found.end(BatchSucceeded(ok(follow_ups())))

    found.poll()

    assert [found.revisions(step) for step in (2, 3, 4)] == [[], [], []]
    assert found.items() == [(found.enrollment, 1, "stale", 1), (found.enrollment, 2, "pending", 0)]
    assert found.jobs("draft_step")[1:] == []


def test_an_edited_step_1_after_the_deadline_runs_the_direct_job(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    world.transact(
        lambda: Drafting(world.clock, queue_on(world.clock)).revise(
            found.drafts[1], "a new subject", GENERATED_BODY
        )
    )
    found.end(BatchSucceeded(ok(follow_ups())))
    world.clock.set(found.deadline() + timedelta(minutes=1))

    found.poll()

    assert found.items() == [(found.enrollment, 1, "stale", 1)]
    assert follow_up_job(found)[0] == f"draft_step:{found.enrollment}:2"


# The deadline -------------------------------------------------------------------------------------


def test_the_deadline_defaults_to_07_00_after_the_first_afternoon_submit(world: World) -> None:
    found = Follow(world, ScriptedLlm())

    deadline = found.deadline()

    [(created,)] = world.sql("SELECT created_at FROM enrollments WHERE id = ?", (found.enrollment,))
    assert str(created).startswith("2026-11-18")  # created in the morning, UTC
    assert deadline.isoformat() == "2026-11-19T07:00:00+00:00"

    with world.units.open():
        Setup(world.clock).save({"draft_deadline": "06:30", "sender_timezone": "Europe/London"})
    assert found.deadline().isoformat() == "2026-11-19T06:30:00+00:00"


def test_at_the_deadline_an_item_in_progress_goes_stale_and_runs_directly(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    world.clock.set(found.deadline())  # the first poll at or after 07:00

    assert found.poll() == 1

    assert found.items() == [(found.enrollment, 1, "stale", 1)]
    assert follow_up_job(found)[0] == f"draft_step:{found.enrollment}:2"
    assert found.llm.deleted == []  # the batch is still in progress: fetched until it ends

    found.end(BatchSucceeded(ok(follow_ups())))  # a later result writes nothing
    found.poll()

    assert [found.revisions(step) for step in (2, 3, 4)] == [[], [], []]
    assert found.llm.deleted == ["msgbatch_fake_1"]

    # the direct job drafts them, with the server fallbacks of a direct call
    found.llm.script.append(ok(follow_ups()))
    assert found.direct(2) is StepDraftOutcome.DRAFTED
    assert [len(found.revisions(step)) for step in (2, 3, 4)] == [1, 1, 1]


def test_before_the_deadline_the_sweep_leaves_the_item(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    world.clock.set(found.deadline() - timedelta(minutes=1))

    assert found.poll() == 0
    assert found.items() == [(found.enrollment, 1, "submitted", 1)]


def test_the_sweep_leaves_a_pending_item_with_no_step_1_revision(world: World) -> None:
    found = Follow(world, ScriptedLlm(), step_1=False)
    world.clock.set(found.deadline() + timedelta(hours=1))

    assert found.poll() == 0
    assert found.items() == [(found.enrollment, 1, "pending", 0)]
    assert found.jobs("draft_step")[1:] == []


def enrolled_uk(world: World) -> tuple[str, str, str]:
    """A new GB Lead and Contact enrolled in Law-PrivateAI x UK (eligibility stubbed, as
    ``tests.app.test_enroll`` does until the legal form is read)."""
    uk = world.activate(UK_CAMPAIGN, (LIA_REF, LIA_DATE.isoformat()))
    lead_id, contact_id = world.contact("GB")
    found = world.enroll(contact_id, uk, steps=(1, 2, 3, 4))
    assert isinstance(found, NewEnrollment)
    return lead_id, contact_id, found.enrollment_id


def test_law_uk_frames_the_sra_angle_and_discards_a_non_regulation_step_2(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Compliance, "evaluate_eligibility", lambda self, _: UK_DECISION)
    found = Follow(world, ScriptedLlm(), enroll=enrolled_uk)
    found.submit()

    [(_, [request])] = found.llm.batches_submitted
    assert request.framing is not None
    assert "What the SRA notice means" in request.framing.brief
    assert "ABA" not in request.framing.brief
    assert "sra_ai_notice" in request.framing.facts
    assert "aba_formal_opinion_512" not in request.framing.facts
    # Step 2 takes a case study, not the SRA notice: discarded (fail closed)
    found.end(BatchSucceeded(ok(follow_ups(step_2=("property_3d_sites", 1)))))

    assert found.poll() == 1

    assert [found.failed(step) for step in (2, 3, 4)] == [("invalid_proposal", None)] * 3
    assert found.items() == [(found.enrollment, 1, "failed", 1)]


# Stopped and blocked ------------------------------------------------------------------------------


def test_a_stopped_enrollment_makes_the_result_stale(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    world.stop(found.enrollment)
    found.end(BatchSucceeded(ok(follow_ups())))

    found.poll()

    assert [found.revisions(step) for step in (2, 3, 4)] == [[], [], []]
    assert found.items() == [(found.enrollment, 1, "stale", 1)]
    assert found.llm.deleted == ["msgbatch_fake_1"]


def test_a_blocked_contact_makes_the_result_stale(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.end(BatchSucceeded(ok(follow_ups())))
    monkeypatch.setattr(
        compliance_api,
        "assert_contactable",
        lambda *_: Blocked((BlockReason.ADDRESS_SUPPRESSED,)),
    )

    found.poll()

    assert [found.revisions(step) for step in (2, 3, 4)] == [[], [], []]
    assert found.items() == [(found.enrollment, 1, "stale", 1)]


# The direct job -----------------------------------------------------------------------------------


def test_the_direct_job_drafts_steps_2_to_4_in_one_call(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.llm.script.append(ok(follow_ups()))

    assert found.direct(2) is StepDraftOutcome.DRAFTED

    [_, (route_tuple, blocks, urgency)] = found.llm.calls
    assert (route_tuple.prompt_version, urgency) == ("v2", Urgency.SOON)
    assert blocks[0].source == "earlier_step"
    assert [len(found.revisions(step)) for step in (2, 3, 4)] == [1, 1, 1]
    assert found.direct(2) is StepDraftOutcome.SKIPPED  # nothing open: no call
    assert len(found.llm.calls) == 2


def test_a_sequence_edited_after_enroll_still_drafts_from_the_bound_version(
    world: World,
) -> None:
    """Story 11.7 with Stories 4.15 and 4.18: the Sequence edited to v2 after the Enrollment
    started; Step 1 and the follow-ups still draft, from the briefs of the bound v1."""
    found = Follow(world, ScriptedLlm(), step_1=False)
    with world.units.open(read_only=True):
        seeded = setup_queries.sequence("default")
    assert seeded is not None
    v1_purposes = [brief.purpose for brief in seeded.definition.steps[:2]]
    edit_default(
        world, {1: {"purpose": "A rewritten first touch"}, 2: {"purpose": "A rewritten nudge"}}
    )
    found.llm.script.append(ok(step_1_answer(found.event, found.quote)))
    found.llm.script.append(ok(follow_ups()))

    assert found.direct(1) is StepDraftOutcome.DRAFTED
    assert found.direct(2) is StepDraftOutcome.DRAFTED

    assert [len(found.revisions(step)) for step in (1, 2, 3, 4)] == [1, 1, 1, 1]
    step_1, follow = (framing.brief for framing in found.llm.framings if framing is not None)
    assert v1_purposes[0] in step_1 and v1_purposes[1] in follow
    assert "rewritten" not in step_1 and "rewritten" not in follow


def test_steps_3_and_4_alone_stay_skipped(world: World) -> None:
    found = Follow(world, ScriptedLlm())

    assert found.direct(3) is StepDraftOutcome.SKIPPED
    assert found.direct(4) is StepDraftOutcome.SKIPPED
    assert len(found.llm.calls) == 1


def test_a_refused_direct_call_fails_the_follow_ups(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.llm.script.append(LlmRefused(None, model=OPUS, usage=USAGE))

    assert found.direct(2) is StepDraftOutcome.FAILED

    assert [found.failed(step) for step in (2, 3, 4)] == [("refused", None)] * 3


def test_a_direct_job_for_a_paused_enrollment_queues_an_item_instead(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.end(BatchSucceeded(LlmInvalid(model=OPUS, usage=USAGE)))
    found.poll()  # failed
    world.transact(
        lambda: found.world.sequencing.pause(found.enrollment, PauseCause.OPERATOR)
    )

    assert found.direct(2) is StepDraftOutcome.SKIPPED

    assert found.items()[-1] == (found.enrollment, 2, "pending", 0)
    assert len(found.llm.calls) == 1


# Review patches (pass 1) --------------------------------------------------------------------------


def test_step_1_on_an_active_v2_tuple_stores_revision_1(world: World) -> None:
    found = Follow(world, ScriptedLlm())  # drafted Step 1 through StepDrafts.draft(enr, 1, SOON)

    [(rev, origin, subject, body, *_, prompt_version)] = found.revisions(1)
    assert (rev, origin, subject, body, prompt_version) == (
        1,
        "generated",
        SUBJECT,
        GENERATED_BODY,
        "v2",
    )
    assert found.failed(1) == (None, 1)


def test_a_submit_after_the_deadline_runs_the_direct_job_and_sends_no_batch(world: World) -> None:
    found = Follow(world, ScriptedLlm(), step_1=False)
    world.clock.set(found.deadline() + timedelta(minutes=1))
    found.llm.script.append(ok(step_1_answer(found.event, found.quote)))
    assert found.direct(1) is StepDraftOutcome.DRAFTED  # Step 1 drafted late

    assert found.submit() == 0

    assert found.llm.batches_submitted == []
    assert found.items() == [(found.enrollment, 1, "stale", 0)]
    assert follow_up_job(found)[0] == f"draft_step:{found.enrollment}:2"


def test_only_the_newest_pending_item_is_sent_the_older_goes_stale(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    world.transact(
        lambda: ai_batches.queue_item(
            BatchOwner.DRAFTING,
            route=ROUTE,
            target_id=found.enrollment,
            rev=2,
            context_id=None,
            at=world.clock.now(),
        )
    )

    assert found.submit() == 1

    [(_, [request])] = found.llm.batches_submitted
    assert request.custom_id == found.custom(2)
    assert found.items() == [
        (found.enrollment, 1, "stale", 0),
        (found.enrollment, 2, "submitted", 1),
    ]


@pytest.mark.parametrize("late", [False, True], ids=["before_deadline", "after_deadline"])
def test_a_paused_enrollment_at_apply_makes_the_result_stale_and_requeues(
    world: World, late: bool
) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    world.transact(lambda: found.world.sequencing.pause(found.enrollment, PauseCause.OPERATOR))
    found.end(BatchSucceeded(ok(follow_ups())))
    if late:
        world.clock.set(found.deadline() + timedelta(minutes=1))

    found.poll()

    assert [found.revisions(step) for step in (2, 3, 4)] == [[], [], []]
    if late:
        assert found.items() == [(found.enrollment, 1, "stale", 1)]
        assert follow_up_job(found)[0] == f"draft_step:{found.enrollment}:2"
    else:
        assert found.items() == [
            (found.enrollment, 1, "stale", 1),
            (found.enrollment, 2, "pending", 0),
        ]
        assert found.jobs("draft_step")[1:] == []


def test_an_over_long_body_discards_the_whole_answer(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    answer = follow_ups()
    answer["steps"][1]["body"] = STEP_3_BODY + "x" * MAX_BODY
    found.end(BatchSucceeded(ok(answer)))

    assert found.poll() == 1

    for step in (2, 3, 4):
        assert found.revisions(step) == []
        assert found.failed(step) == ("invalid_proposal", None)
    assert found.items() == [(found.enrollment, 1, "failed", 1)]


@pytest.mark.parametrize("gone", ["stopped", "blocked"])
def test_an_invalid_answer_for_a_stopped_or_blocked_enrollment_writes_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch, gone: str
) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.end(BatchSucceeded(LlmInvalid(model=OPUS, usage=USAGE)))
    if gone == "stopped":
        world.stop(found.enrollment)
    else:
        monkeypatch.setattr(
            compliance_api,
            "assert_contactable",
            lambda *_: Blocked((BlockReason.ADDRESS_SUPPRESSED,)),
        )

    assert found.poll() == 1

    assert [found.failed(step) for step in (2, 3, 4)] == [(None, None)] * 3
    assert found.items() == [(found.enrollment, 1, "stale", 1)]


def test_a_direct_success_after_a_failure_clears_the_failure(world: World) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    found.end(BatchSucceeded(LlmInvalid(model=OPUS, usage=USAGE)))
    found.poll()
    assert [found.failed(step) for step in (2, 3, 4)] == [("invalid", None)] * 3
    found.llm.script.append(ok(follow_ups()))

    assert found.direct(2) is StepDraftOutcome.DRAFTED

    assert world.sql(
        "SELECT step_no, draft_failed_at, draft_failed_reason, current_rev FROM drafts "
        "WHERE enrollment_id = ? AND step_no > 1 ORDER BY step_no",
        (found.enrollment,),
    ) == [(2, None, None, 1), (3, None, None, 1), (4, None, None, 1)]


class _EditsStep1(ScriptedLlm):
    """Answers like ``ScriptedLlm``, after running ``during`` (an edit made during the call)."""

    during: Any = None

    def run(self, *args: Any, **kwargs: Any) -> Any:
        answer = super().run(*args, **kwargs)
        if self.during is not None:
            self.during()
        return answer


def test_a_step_1_edited_during_the_direct_call_writes_nothing_and_requeues(
    world: World,
) -> None:
    llm = _EditsStep1()
    found = Follow(world, llm)
    found.submit()
    found.end(BatchSucceeded(LlmRefused(None, model=OPUS, usage=USAGE)))
    found.poll()  # rerun: the direct job is queued
    assert found.items() == [(found.enrollment, 1, "rerun", 1)]

    def edit() -> None:
        with current().transaction():
            Drafting(world.clock, queue_on(world.clock)).revise(
                found.drafts[1], "a new subject", GENERATED_BODY
            )

    llm.during = edit
    llm.script.append(ok(follow_ups()))

    assert found.direct(2) is StepDraftOutcome.SKIPPED

    assert [found.revisions(step) for step in (2, 3, 4)] == [[], [], []]
    assert [found.failed(step) for step in (2, 3, 4)] == [(None, None)] * 3
    assert found.items()[-1] == (found.enrollment, 2, "pending", 0)
    assert len(found.revisions(1)) == 2  # the edit itself was kept


def test_a_batch_whose_tuple_no_longer_resolves_re_runs_directly_with_no_provider_call(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = Follow(world, ScriptedLlm())
    found.submit()
    monkeypatch.setattr(playbook_queries, "tuple_by_key", lambda *_: None)

    assert found.poll() == 1

    assert found.items() == [(found.enrollment, 1, "rerun", 1)]
    assert follow_up_job(found)[0] == f"draft_step:{found.enrollment}:2"
    assert found.llm.fetched == []
    assert found.llm.deleted == []
    assert world.sql("SELECT status FROM ai_batches") == [("deleted",)]


def test_the_schedule_reports_an_item_queued_before_the_latest_submit_slot(
    world: World,
) -> None:
    found = Follow(world, ScriptedLlm())  # queued on the morning of 2026-11-18, UTC

    def missed() -> bool:
        with world.units.open(read_only=True):
            return found.batches().schedule().missed

    assert missed() is False  # the latest 14:00 slot (the day before) came before it
    world.clock.set(found.deadline() - timedelta(hours=16))  # 2026-11-18 15:00
    assert missed() is True
