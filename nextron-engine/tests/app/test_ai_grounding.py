"""The AI grounding check and its fail-closed result (FR-21, AD-7, AD-8, AD-16, AD-18; Story
4.14), on the world of ``tests.app.test_draft_revisions`` (a temporary database at head, the
real subscriber registry, the shipped seeds, Law-PrivateAI x US, the footer settings saved),
with ``ScriptedLlm`` and the shipped grounding tuples activated through the real 4.5 gate path
(a stored passing run, then ``Playbook.activate``).

- every revision enqueues ``check_grounding:<draft>:<rev>`` in its own unit of work, on the lane
  of its urgency; a ``Drafting`` without a queue refuses before any write;
- the spec's I/O matrix through ``GroundingChecks.check``: clean, rules + AI flags, a Haiku
  refusal served by the Opus fallback, a final refusal, no tuple, a timeout, an uncited claim, a
  citation outside the set, an old revision, a re-run and the DB guard; plus ``LlmFailed``, an
  invalid answer with no fallback, an uncontactable subject and the erasure of AI flags;
- the Lead page's badge and flags, the span autoescaped in a ``<mark>``.
"""

import contextvars
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.drafting.api import Drafting
from nextron.app.drafting import grounding as grounding_module
from nextron.app.drafting.grounding import GroundingCheckOutcome, GroundingChecks
from nextron.app.playbook.api import ActivationTrigger, Playbook
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.db.repos.playbook import test_runs as runs_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency
from nextron.domain.ai_test_gate import TestSet
from nextron.domain.contactability import Blocked, BlockReason
from nextron.domain.draft import RevisionOrigin
from nextron.domain.grounding_check import (
    UNAVAILABLE_FAILED,
    UNAVAILABLE_NO_ANSWER,
    UNAVAILABLE_NO_TUPLE,
    UNAVAILABLE_NOT_CONTACTABLE,
)
from nextron.domain.grounding_rules import Claim, GroundingStatus
from nextron.domain.playbook import PlaybookSnapshot
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmFailed, LlmInvalid, LlmOk, LlmRefused, LlmResult, LlmUsage
from tests.app.test_draft_grounding_rules import GENERATED_BODY, HIRING, PROOF, generated
from tests.app.test_draft_revisions import (
    BODY,
    SUBJECT,
    TRACE,
    append,
    drafted,
    ids_of,
    lead_page,
    real_trace,
    revise,
    save_footer,
    world,  # noqa: F401  (the fixture)
)
from tests.app.test_draft_slots import enrolled
from tests.app.test_enroll import World, queue_on
from tests.fakes.llm import ScriptedLlm

HAIKU: Final = "claude-haiku-5-5"
OPUS: Final = "claude-opus-5-5"
PLAYBOOK: Final = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Playbook.\n")
RESULT: Final = (
    "SELECT status, flags, route_tuple_id, served_model, ai_checked_at FROM grounding_results "
    "WHERE draft_id = ? AND rev = ?"
)
USAGE: Final = LlmUsage(requests=1, cost_micros=10)


def ok(
    claims: list[dict[str, Any]], flags: list[dict[str, Any]] | None = None, *, model: str = HAIKU
) -> LlmOk:
    return LlmOk({"claims": claims, "flags": flags or []}, model=model, usage=USAGE)


def refused(model: str = HAIKU) -> LlmRefused:
    return LlmRefused(None, model=model, usage=USAGE)


def claim(span: str, evidence_id: str | None, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "span": span,
        "evidence_id": evidence_id,
        "fact_id": None,
        "fact_version": None,
        "supported": True,
        "explanation": "The cited block says so.",
    }
    return row | changes


def activate(world: World, *routes: str) -> dict[str, str]:
    """Sync the shipped prompts under ``PLAYBOOK`` and activate each of ``routes`` through a
    stored passing run; each route's tuple id."""
    files, defaults = main.load_prompts()
    with world.units.open():
        Playbook(world.clock).sync_prompts(files, defaults, PLAYBOOK)
    found: dict[str, str] = {}
    for route in routes:
        [(tuple_id,)] = world.sql("SELECT id FROM ai_route_tuples WHERE route = ?", (route,))
        now = world.clock.now()

        def store(chosen: str = str(tuple_id), name: str = route) -> None:
            runs_repo.insert(
                tuple_id=chosen,
                test_set=TestSet.GROUNDING.value,
                outcome="passed",
                error=None,
                case_count=49,
                passed_count=49,
                metrics="[]",
                results="[]",
                cost_micros=0,
                started_at=now,
                finished_at=now,
                cases_sha256=playbook_queries.case_set_fingerprint(TestSet.GROUNDING, name),
            )

        world.transact(store)
        with world.units.open():
            Playbook(world.clock).activate(str(tuple_id), trigger=ActivationTrigger.TEST_PASSED)
        found[route] = str(tuple_id)
    return found


def check(world: World, llm: ScriptedLlm, draft_id: str, rev: int) -> GroundingCheckOutcome:
    with world.units.open():
        return GroundingChecks(world.clock, llm).check(draft_id, rev, Urgency.INTERACTIVE)


def result(
    world: World, draft_id: str, rev: int
) -> tuple[str, list[dict[str, str]], Any, Any, Any]:
    [(status, flags, tuple_id, model, at)] = world.sql(RESULT, (draft_id, rev))
    return status, json.loads(flags), tuple_id, model, at


def codes(flags: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    return [(flag["rule"], flag["span"], flag["source"]) for flag in flags]


def evidence_of(world: World, draft_id: str) -> list[str]:
    [(found,)] = world.sql(
        "SELECT evidence_ids FROM draft_revisions WHERE draft_id = ? AND rev = 1", (draft_id,)
    )
    return list(json.loads(found))


GOOD = (Claim(HIRING, "quote", None), Claim(PROOF, None, ("legal_ai", 1)))


def good_answer(world: World, draft_id: str, *, model: str = HAIKU) -> LlmOk:
    quote, _ = evidence_of(world, draft_id)
    return ok(
        [
            claim(HIRING, quote),
            claim(PROOF, None, fact_id="legal_ai", fact_version=1),
        ],
        model=model,
    )


# The job ------------------------------------------------------------------------------------------


def jobs(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT idempotency_key, lane, payload FROM jobs WHERE type = 'check_grounding' "
        "ORDER BY created_at, idempotency_key"
    )


def test_every_revision_enqueues_its_check_on_the_lane_of_its_urgency(world: World) -> None:
    _, [step_1, step_2] = drafted(world)  # generated at soon: interactive
    revise(world, step_1, "Edited subject", "Hi Zelda,\n\nAn edited note.")  # interactive
    save_footer(world, legal_name="Nextron AI Limited")  # a footer change: bulk

    found = {key: (lane, json.loads(payload)) for key, lane, payload in jobs(world)}
    assert found == {
        f"check_grounding:{step_1}:1": ("interactive", {"v": 1, "draft_id": step_1, "rev": 1}),
        f"check_grounding:{step_2}:1": ("interactive", {"v": 1, "draft_id": step_2, "rev": 1}),
        f"check_grounding:{step_1}:2": ("interactive", {"v": 1, "draft_id": step_1, "rev": 2}),
        f"check_grounding:{step_1}:3": ("bulk", {"v": 1, "draft_id": step_1, "rev": 3}),
        f"check_grounding:{step_2}:2": ("bulk", {"v": 1, "draft_id": step_2, "rev": 2}),
    }
    [(revisions,)] = world.sql("SELECT COUNT(*) FROM draft_revisions")
    assert len(found) == revisions


def test_a_drafting_without_a_queue_refuses_before_any_write(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world, steps=(1,))
    [(draft_id,)] = world.sql("SELECT id FROM drafts WHERE enrollment_id = ?", (enrollment_id,))
    before = world.counts()
    drafting = Drafting(world.clock)

    def write() -> int:
        view = drafting_queries.draft(str(draft_id))
        assert view is not None
        return drafting._append(
            view,
            SUBJECT,
            BODY,
            RevisionOrigin.GENERATED,
            TRACE,
            world.clock.now(),
            urgency=Urgency.SOON,
        )

    with pytest.raises(RuntimeError, match="without a job queue"):
        world.transact(write)
    assert world.counts() == before


def test_a_bulk_urgency_enqueues_on_the_bulk_lane(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    [(draft_id,)] = world.sql("SELECT id FROM drafts WHERE enrollment_id = ?", (enrollment_id,))
    drafting = Drafting(world.clock, queue_on(world.clock))

    def write() -> int:
        view = drafting_queries.draft(str(draft_id))
        assert view is not None
        return drafting._append(
            view,
            SUBJECT,
            BODY,
            RevisionOrigin.GENERATED,
            TRACE,
            world.clock.now(),
            urgency=Urgency.BULK,
        )

    world.transact(write)
    assert [(key, lane) for key, lane, _ in jobs(world)] == [
        (f"check_grounding:{draft_id}:1", "bulk")
    ]


# The I/O matrix -----------------------------------------------------------------------------------


def test_clean_passes_with_the_tuple_and_the_served_model(world: World) -> None:
    tuples = activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(good_answer(world, draft_id))

    assert check(world, llm, draft_id, 1) is GroundingCheckOutcome.RECORDED

    status, flags, tuple_id, model, at = result(world, draft_id, 1)
    assert (status, flags, tuple_id, model) == ("pass", [], tuples["grounding_check"], HAIKU)
    assert at is not None
    [(route_tuple, blocks, urgency)] = llm.calls
    assert (route_tuple.route, route_tuple.model, urgency) == (
        "grounding_check",
        HAIKU,
        Urgency.INTERACTIVE,
    )
    assert route_tuple.playbook == PLAYBOOK  # the pinned snapshot, never the live file
    sources = [(block.source, block.block_id) for block in blocks]
    quote, absence = evidence_of(world, draft_id)
    assert sources == [
        ("context", "context"),
        ("draft", "subject"),
        ("draft", "body"),
        ("evidence", quote),
        ("evidence", absence),
        ("fact", "legal_ai.v1"),
    ]
    texts = {block.block_id: block.text for block in blocks}
    assert texts["context"] == "step: 1\nmarket: us"
    assert texts["body"] == GENERATED_BODY
    assert texts[quote] == "kind: quote\n\nWe are hiring an intake coordinator."
    assert texts[absence] == "kind: absence\n\nLooked for and not found: chat widget"
    assert "@" not in "".join(texts.values())  # no email address beyond the Draft's own text
    with world.units.open(read_only=True):
        found = drafting_queries.grounding_result(draft_id, 1)
    assert found is not None and found.status is GroundingStatus.PASS


def test_rules_and_ai_flags_fail_with_the_rules_first(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    body = GENERATED_BODY + " Circling back."
    assert revise(world, draft_id, "your intake coordinator role", body) == 2
    quote, _ = evidence_of(world, draft_id)
    llm = ScriptedLlm(ok([claim(HIRING, quote, supported=False)]))

    check(world, llm, draft_id, 2)

    status, flags, *_ = result(world, draft_id, 2)
    assert status == "fail"
    assert codes(flags) == [
        ("banned_phrase", "Circling back", "rules"),
        ("unsupported_claim", HIRING, "ai"),
    ]
    assert flags[1]["explanation"] == "The cited block says so."


def test_a_haiku_refusal_is_served_by_the_opus_fallback(world: World) -> None:
    tuples = activate(world, "grounding_check", "grounding_check_fallback")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(refused(), good_answer(world, draft_id, model=OPUS))

    check(world, llm, draft_id, 1)

    status, flags, tuple_id, model, _ = result(world, draft_id, 1)
    assert (status, flags, tuple_id, model) == (
        "pass",
        [],
        tuples["grounding_check_fallback"],
        OPUS,
    )
    assert [(call[0].route, call[0].model) for call in llm.calls] == [
        ("grounding_check", HAIKU),
        ("grounding_check_fallback", OPUS),
    ]
    assert llm.calls[0][1] == llm.calls[1][1]  # the same blocks


@pytest.mark.parametrize("first", [LlmInvalid(model=HAIKU, usage=USAGE), refused()])
def test_a_final_refusal_fails_closed(world: World, first: LlmResult) -> None:
    activate(world, "grounding_check", "grounding_check_fallback")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(first, refused(OPUS))

    check(world, llm, draft_id, 1)

    status, flags, _, model, at = result(world, draft_id, 1)
    assert (status, codes(flags), model) == (
        "fail",
        [("checker_unavailable", "", "ai")],
        OPUS,
    )
    assert flags[0]["explanation"] == UNAVAILABLE_NO_ANSWER
    assert at is not None and len(llm.calls) == 2


def test_an_invalid_haiku_answer_without_a_fallback_fails_closed(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(LlmInvalid(model=HAIKU, usage=USAGE))

    check(world, llm, draft_id, 1)

    status, flags, *_ = result(world, draft_id, 1)
    assert (status, flags[0]["explanation"]) == ("fail", UNAVAILABLE_NO_ANSWER)
    assert len(llm.calls) == 1


def test_no_active_tuple_fails_closed_without_a_call(world: World) -> None:
    activate(world)  # the tuples are recorded; none is active
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm()

    check(world, llm, draft_id, 1)

    status, flags, tuple_id, model, at = result(world, draft_id, 1)
    assert (status, codes(flags), tuple_id, model) == (
        "fail",
        [("checker_unavailable", "", "ai")],
        None,
        None,
    )
    assert flags[0]["explanation"] == UNAVAILABLE_NO_TUPLE
    assert at is not None and llm.calls == []


@pytest.mark.parametrize(
    "failure",
    [
        AdapterError(True, "llm_unavailable", "timed out"),
        LlmFailed(False, "llm_rejected", 400, model=HAIKU, usage=USAGE),
    ],
)
def test_a_timeout_or_a_failed_call_fails_closed_and_isnt_retried(
    world: World, failure: Any
) -> None:
    activate(world, "grounding_check", "grounding_check_fallback")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(failure)

    assert check(world, llm, draft_id, 1) is GroundingCheckOutcome.RECORDED  # the job succeeds

    status, flags, *_ = result(world, draft_id, 1)
    assert (status, flags[0]["rule"], flags[0]["explanation"]) == (
        "fail",
        "checker_unavailable",
        UNAVAILABLE_FAILED,
    )
    assert len(llm.calls) == 1  # no fallback for a failure


def test_an_uncited_claim_fails(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(ok([claim(PROOF, None, supported=False)]))

    check(world, llm, draft_id, 1)

    status, flags, *_ = result(world, draft_id, 1)
    assert (status, codes(flags)) == ("fail", [("uncited_claim", PROOF, "ai")])


def test_an_ai_claim_citing_foreign_evidence_fails_the_rules(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    foreign = "01JD3QZ8X5V9K2M7N4P6R8T0ZZ"
    llm = ScriptedLlm(ok([claim(HIRING, foreign)]))

    check(world, llm, draft_id, 1)

    status, flags, *_ = result(world, draft_id, 1)
    assert status == "fail"
    assert ("citation_outside_set", HIRING, "rules") in codes(flags)


def test_a_result_for_an_old_revision_stays_on_its_row(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    assert revise(world, draft_id, "your intake coordinator role", GENERATED_BODY) == 2
    llm = ScriptedLlm(good_answer(world, draft_id))

    check(world, llm, draft_id, 1)

    assert result(world, draft_id, 1)[0] == "pass"
    status, flags, tuple_id, model, at = result(world, draft_id, 2)
    assert (status, flags, tuple_id, model, at) == ("pending", [], None, None, None)


def test_a_rerun_is_a_no_op_with_one_call(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(good_answer(world, draft_id))
    check(world, llm, draft_id, 1)
    before = result(world, draft_id, 1)
    world.clock.advance(timedelta(minutes=5))

    assert check(world, llm, draft_id, 1) is GroundingCheckOutcome.SKIPPED

    assert result(world, draft_id, 1) == before
    assert len(llm.calls) == 1


def test_an_unknown_revision_is_skipped(world: World) -> None:
    activate(world, "grounding_check")
    llm = ScriptedLlm()
    draft_id = generated(world, GOOD)

    assert check(world, llm, draft_id, 9) is GroundingCheckOutcome.SKIPPED
    assert check(world, llm, "01JD3QZ8X5V9K2M7N4P6R8T0ZZ", 1) is GroundingCheckOutcome.SKIPPED
    assert llm.calls == []


def test_an_uncontactable_subject_gets_no_call(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    asked: list[tuple[Any, ...]] = []

    def blocked(subject: Any, channel: Any, purpose: Any) -> Blocked:
        asked.append((subject, channel, purpose))
        return Blocked((BlockReason.ADDRESS_SUPPRESSED,))

    monkeypatch.setattr(compliance_api, "assert_contactable", blocked)
    llm = ScriptedLlm()

    check(world, llm, draft_id, 1)

    status, flags, *_ = result(world, draft_id, 1)
    assert (status, flags[0]["rule"], flags[0]["explanation"]) == (
        "fail",
        "checker_unavailable",
        UNAVAILABLE_NOT_CONTACTABLE,
    )
    assert llm.calls == []
    assert [(channel, purpose) for _, channel, purpose in asked] == [("email", "cold")]


def test_the_db_refuses_a_pass_without_a_tuple(world: World) -> None:
    _, [step_1, _] = drafted(world)
    connection = sqlite3.connect(world.db_file, isolation_level=None)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ck_grounding_results_pass"):
            connection.execute(
                "UPDATE grounding_results SET status = 'pass', served_model = 'm', "
                "ai_checked_at = '2026-11-18T09:00:00.000000Z' WHERE draft_id = ?",
                (step_1,),
            )
    finally:
        connection.close()


def test_erasure_erases_the_ai_flags_spans_and_explanations(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    body = GENERATED_BODY + " Circling back."
    revise(world, draft_id, "your intake coordinator role", body)
    quote, _ = evidence_of(world, draft_id)
    check(world, ScriptedLlm(ok([claim(HIRING, quote, supported=False)])), draft_id, 2)
    [(contact_id,)] = world.sql(
        "SELECT e.contact_id FROM drafts d JOIN enrollments e ON e.id = d.enrollment_id "
        "WHERE d.id = ?",
        (draft_id,),
    )
    erasures = Erasures(world.clock, Compliance(world.clock), queue=queue_on(world.clock))

    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    status, flags, *_ = result(world, draft_id, 2)
    assert status == "fail"
    assert [(f["rule"], f["span"], f["explanation"], f["source"]) for f in flags] == [
        ("banned_phrase", "[erased]", flags[0]["explanation"], "rules"),
        ("unsupported_claim", "[erased]", "[erased]", "ai"),
    ]
    assert flags[0]["explanation"] != "[erased]"  # code's own text stays



OUTSIDE: Final = "01JD3QZ8X5V9K2M7N4P6R8T0AE"


@pytest.mark.parametrize("ai_cites_outside", [False, True])
def test_a_stored_claim_flag_is_kept_once_after_a_clean_answer(
    world: World, ai_cites_outside: bool
) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, (Claim(HIRING, OUTSIDE, None), Claim(PROOF, None, ("legal_ai", 1))))
    assert codes(result(world, draft_id, 1)[1]) == [("citation_outside_set", HIRING, "rules")]
    quote, _ = evidence_of(world, draft_id)
    llm = ScriptedLlm(
        ok(
            [
                claim(HIRING, OUTSIDE if ai_cites_outside else quote),
                claim(PROOF, None, fact_id="legal_ai", fact_version=1),
            ]
        )
    )

    check(world, llm, draft_id, 1)

    status, flags, *_ = result(world, draft_id, 1)
    assert (status, codes(flags)) == ("fail", [("citation_outside_set", HIRING, "rules")])


def test_a_step_2_check_reads_step_1_current_text_and_each_evidence_kind(world: World) -> None:
    activate(world, "grounding_check")
    lead_id, _, enrollment_id = enrolled(world, steps=(1, 2))
    step_1, step_2 = ids_of(world, enrollment_id)
    trace = real_trace(world, lead_id)
    now = world.clock.now()

    def evidence(kind: str, **fields: Any) -> str:
        row: dict[str, Any] = {
            "quote": None,
            "sought": None,
            "number": None,
            "fact": None,
            "value": None,
        } | fields
        return signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type=kind,
            pages_checked=("https://firm.example.com/about",),
            source="website",
            source_url="https://firm.example.com/about",
            retrieved_at=now,
            extracted_by="ai:extract_signals@v1",
            person_contact_id=None,
            at=now,
            **row,
        )

    counted = world.transact(lambda: evidence("count", sought="lawyers", number=12))
    located = world.transact(
        lambda: evidence("quote", quote="Our office is in Boston.", fact="location", value="Boston")
    )
    assert append(world, step_1, RevisionOrigin.GENERATED, trace) == 1
    assert revise(world, step_1, "Step one now", "Hi Zelda,\n\nThe current step one.") == 2
    cited = replace(trace, evidence_ids=(counted, located, counted))  # a repeat is read once
    assert append(world, step_2, RevisionOrigin.GENERATED, cited) == 1
    llm = ScriptedLlm(ok([]))

    assert check(world, llm, step_2, 1) is GroundingCheckOutcome.RECORDED

    [(_, blocks, _)] = llm.calls
    [(subject, body)] = world.sql(
        "SELECT subject, body FROM draft_revisions WHERE draft_id = ? AND rev = 2", (step_1,)
    )
    assert subject == "Step one now"
    earlier = [
        (block.source, block.block_id, block.text)
        for block in blocks
        if block.source == "earlier_step"
    ]
    assert earlier == [("earlier_step", "step1", f"Step 1\nSubject: {subject}\n\n{body}")]
    texts = {block.block_id: block.text for block in blocks}
    assert texts["context"].startswith("step: 2\n")
    assert [block.block_id for block in blocks if block.source == "evidence"] == [counted, located]
    assert texts[counted] == "kind: count\n\nCounted lawyers: 12"
    assert texts[located] == "kind: quote\n\nOur office is in Boston.\nlocation: Boston"


def test_an_opus_fallback_timeout_fails_closed_with_the_fallback_tuple(world: World) -> None:
    tuples = activate(world, "grounding_check", "grounding_check_fallback")
    draft_id = generated(world, GOOD)
    llm = ScriptedLlm(refused(), AdapterError(True, "llm_unavailable", "timed out"))

    assert check(world, llm, draft_id, 1) is GroundingCheckOutcome.RECORDED

    status, flags, tuple_id, model, at = result(world, draft_id, 1)
    assert (status, codes(flags), tuple_id, model) == (
        "fail",
        [("checker_unavailable", "", "ai")],
        tuples["grounding_check_fallback"],
        None,
    )
    assert flags[0]["explanation"] == UNAVAILABLE_FAILED
    assert at is not None and len(llm.calls) == 2


def test_a_context_gone_at_apply_keeps_the_ai_flags_and_fails_closed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    quote, _ = evidence_of(world, draft_id)
    llm = ScriptedLlm(ok([claim(HIRING, quote, supported=False)]))

    def gone(*args: Any) -> Any:
        raise LookupError("the Draft's context is gone")

    monkeypatch.setattr(grounding_module, "_grounding", gone)

    check(world, llm, draft_id, 1)

    status, flags, *_ = result(world, draft_id, 1)
    assert (status, codes(flags)) == (
        "fail",
        [("unsupported_claim", HIRING, "ai"), ("checker_unavailable", "", "ai")],
    )
    assert flags[1]["explanation"] == UNAVAILABLE_FAILED


class ErasingLlm(ScriptedLlm):
    """A ``ScriptedLlm`` that erases the Contact while the call is in flight."""

    def __init__(self, world: World, contact_id: str, *script: LlmResult) -> None:
        super().__init__(*script)
        self.world = world
        self.contact_id = contact_id

    def run(
        self, route_tuple: RouteTuple, inputs: Sequence[UntrustedBlock], urgency: Urgency
    ) -> LlmResult:
        answer = super().run(route_tuple, inputs, urgency)
        world = self.world
        erasures = Erasures(world.clock, Compliance(world.clock), queue=queue_on(world.clock))
        contextvars.Context().run(  # its own unit of work: no transaction is open here
            world.transact, lambda: erasures.erase_contact(self.contact_id, None, "retention")
        )
        return answer


def test_a_contact_erased_during_the_call_gets_no_ai_flags(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    body = GENERATED_BODY + " Circling back."
    revise(world, draft_id, "your intake coordinator role", body)
    quote, _ = evidence_of(world, draft_id)
    [(contact_id,)] = world.sql(
        "SELECT e.contact_id FROM drafts d JOIN enrollments e ON e.id = d.enrollment_id "
        "WHERE d.id = ?",
        (draft_id,),
    )
    llm = ErasingLlm(world, str(contact_id), ok([claim(HIRING, quote, supported=False)]))

    assert check(world, llm, draft_id, 2) is GroundingCheckOutcome.RECORDED

    status, flags, *_ = result(world, draft_id, 2)
    assert status == "fail"
    assert [(f["rule"], f["span"], f["source"]) for f in flags] == [
        ("banned_phrase", "[erased]", "rules"),
        ("checker_unavailable", "", "ai"),
    ]
    assert flags[1]["explanation"] == UNAVAILABLE_NOT_CONTACTABLE
    assert HIRING not in json.dumps(flags)


# Wiring -------------------------------------------------------------------------------------------


def test_build_app_wires_grounding_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron.bootstrap import load_bootstrap  # noqa: PLC0415
    from tests.fakes.secrets import InMemorySecrets  # noqa: PLC0415
    from tests.fakes.units import FakeUnits  # noqa: PLC0415

    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[GroundingChecks] = []
    people_args: list[tuple[Any, ...]] = []
    registries: list[dict[str, Any]] = []
    real_checks, real_people = main.GroundingChecks, main.PeopleExtractions
    real_job_types = main.build_job_types

    def grounding_checks(*args: Any) -> GroundingChecks:
        built.append(real_checks(*args))
        return built[-1]

    def people(*args: Any) -> Any:
        people_args.append(args)
        return real_people(*args)

    def job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_job_types(**kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "GroundingChecks", grounding_checks)
    monkeypatch.setattr(main, "PeopleExtractions", people)
    monkeypatch.setattr(main, "build_job_types", job_types)

    main.build_app(load_bootstrap(), units=FakeUnits())

    [checks] = built
    [(_, _, llm, *_)] = people_args
    assert checks._llm is llm  # noqa: SLF001 - the app's one LLM gateway
    [given] = registries
    assert given["grounding"]() is checks

# The Lead page ------------------------------------------------------------------------------------


def test_the_lead_page_shows_the_badge_and_each_flag_escaped(world: World) -> None:
    activate(world, "grounding_check")
    lead_id, _, enrollment_id = enrolled(world, steps=(1, 2))
    [step_1, step_2] = [
        str(row[0])
        for row in world.sql(
            "SELECT id FROM drafts WHERE enrollment_id = ? ORDER BY step_no", (enrollment_id,)
        )
    ]
    hostile = "<b>Circling back</b>"
    append(world, step_1, RevisionOrigin.GENERATED, replace(TRACE, cited_signal_id=None))
    append(
        world,
        step_2,
        RevisionOrigin.GENERATED,
        replace(TRACE, cited_signal_id=None),
        body=f"Hi Zelda,\n\n{hostile} on intake.",
    )
    llm = ScriptedLlm(
        ok([], [{"code": "false_urgency", "span": hostile, "explanation": "Pressure <i>."}])
    )
    check(world, llm, step_2, 1)

    page = lead_page(world, lead_id)

    first, second = page.find("section", class_="lead-drafts").find_all("article", class_="draft")
    assert first.find("span", class_="grounding-badge").text == "pending"
    assert not first.find_all("li", class_="grounding-flag")
    assert second.find("span", class_="grounding-badge").text == "fail"
    rows = second.find_all("li", class_="grounding-flag")
    assert [row.find("code").text for row in rows] == ["banned_phrase", "false_urgency"]
    assert [row.find("mark").text for row in rows] == ["Circling back", hostile]
    assert "Pressure <i>." in rows[1].text
    assert not second.find_all("b") and not second.find_all("i")  # autoescaped
