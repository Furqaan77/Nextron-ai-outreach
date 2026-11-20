"""Segment fallback copy for hand-picked Tier C Leads (FR-22, AD-7, AD-16; Story 4.19), on the
world of ``tests.app.test_draft_revisions`` (a temporary database at head, the real subscriber
registry, the shipped seeds, Law-PrivateAI x US, the footer settings saved), with the shipped
Approved Company Facts and Segment fallback templates synced.

- the spec's I/O matrix through ``StepDrafts.draft``: Tier C Step 1 (no AI call, revision 1
  ``generated`` from the template, ``drafts.generic`` set), Tier A unchanged, no template, a
  missing value, a blocked subject;
- Steps 2 to 4 through ``fallback.write_generic`` (the thread's subject);
- the grounding: ``signal_count`` waived and recorded, the AI check's rerun keeping the waiver;
- ``_append``'s flag: an edit keeps it and the waiver, a route-made regenerate clears it;
- the Lead page's "generic" badge.
"""

import dataclasses
import json
import logging
from datetime import timedelta
from typing import Any, Final

import pytest

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.drafting import fallback
from nextron.app.drafting.api import Drafting
from nextron.app.drafting.fallback import GenericOutcome, generic_copy, write_generic
from nextron.app.drafting.generation import StepDraftOutcome
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.setup.api import Setup, check_seed
from nextron.domain.ai import Urgency
from nextron.domain.campaign import validate_campaigns
from nextron.domain.contactability import Blocked, BlockReason
from nextron.domain.draft import RevisionOrigin
from nextron.domain.fallback_copy import FallbackTemplate, validate_fallback_templates
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from tests.app.test_ai_grounding import activate as activate_grounding
from tests.app.test_ai_grounding import check
from tests.app.test_ai_grounding import ok as grounding_ok
from tests.app.test_draft_grounding_rules import lead_with_signals, seed_facts
from tests.app.test_draft_revisions import (
    lead_page,
    real_trace,
    revise,
    save_footer,
    world,  # noqa: F401  (the fixture)
)
from tests.app.test_draft_slots import enrolled
from tests.app.test_enroll import World, queue_on, shipped
from tests.app.test_step_drafting import activate, answer, draft, failure, ok, revisions, step_1
from tests.fakes.llm import ScriptedLlm

SUBJECT: Final = "Client data and AI tools"
LEGAL_AI: Final = (
    "Built an AI legal assistant platform for research and document help for a legal-tech "
    "venture (Legal AI)."
)
GENERIC_ROW: Final = "SELECT generic FROM drafts WHERE id = ?"
GROUNDING: Final = (
    "SELECT status, flags, waived_rules FROM grounding_results WHERE draft_id = ? ORDER BY rev"
)


def templates() -> tuple[FallbackTemplate, ...]:
    return shipped(main.FALLBACK_TEMPLATES_FILE_NAME, validate_fallback_templates)


def sync_templates(world: World, chosen: tuple[FallbackTemplate, ...] | None = None) -> None:
    """The shipped facts (once), then ``chosen`` (the shipped templates by default)."""
    if not world.sql("SELECT 1 FROM company_facts LIMIT 1"):
        seed_facts(world)
    checked = check_seed(
        shipped(main.SEGMENTS_FILE_NAME, validate_segments),
        shipped(main.MARKETS_FILE_NAME, validate_markets),
        shipped(main.SEQUENCES_FILE_NAME, validate_sequences),
        shipped(main.OFFERS_FILE_NAME, validate_offers),
        shipped(main.CAMPAIGNS_FILE_NAME, validate_campaigns),
        fallback_templates=templates() if chosen is None else chosen,
    )
    world.transact(
        lambda: Setup(world.clock).sync_fallback_templates(checked, world.clock.now())
    )


def tier_c(world: World, steps: tuple[int, ...] = (1, 2)) -> tuple[str, str, str, list[str]]:
    """A new enrolled Contact whose Lead has no Signal (Tier C): (lead, contact, enrollment,
    draft ids by Step)."""
    lead_id, contact_id, enrollment_id = enrolled(world, steps=steps)
    rows = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? ORDER BY step_no", (enrollment_id,)
    )
    return lead_id, contact_id, enrollment_id, [str(row[0]) for row in rows]


def generic(world: World, draft_id: str) -> int:
    [(flag,)] = world.sql(GENERIC_ROW, (draft_id,))
    return int(flag)


def lead_name(world: World, lead_id: str) -> str:
    [(name,)] = world.sql("SELECT name FROM leads WHERE id = ?", (lead_id,))
    return str(name)


def write(world: World, draft_id: str, urgency: Urgency = Urgency.BULK) -> GenericOutcome:
    """``write_generic`` for the Draft ``draft_id`` in one unit of work."""

    def run() -> GenericOutcome:
        view = drafting_queries.draft(draft_id)
        assert view is not None and view.enrollment_id is not None
        enrollment = sequencing_queries.enrollment(view.enrollment_id)
        assert enrollment is not None
        contact = research_queries.contact(enrollment.contact_id)
        assert contact is not None
        return write_generic(
            view, enrollment, contact, world.clock.now(), urgency, queue=queue_on(world.clock)
        )

    return world.transact(run)


# Step 1 -------------------------------------------------------------------------------------------


def test_a_tier_c_step_1_is_filled_from_the_template_with_no_ai_call(world: World) -> None:
    sync_templates(world)
    lead_id, _, enrollment_id, [step_1_id, _] = tier_c(world)
    llm = ScriptedLlm()  # no answer scripted, and no draft_sequence tuple is active

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.DRAFTED

    assert llm.calls == []
    [row] = revisions(world, step_1_id)
    body = (
        f"Hi Zelda,\n\nMany firms want AI help with research and drafting, but cannot send "
        f"client information to public tools.\n\nRecent work: {LEGAL_AI}\n\nWould a private "
        f"setup be worth a look at {lead_name(world, lead_id)}?"
    )
    assert row == (
        1,
        "generated",
        SUBJECT,
        body,
        "[]",
        json.dumps([["legal_ai", 1]]),
        None,
        None,
        None,
        None,
        None,
    )
    assert generic(world, step_1_id) == 1
    assert failure(world, step_1_id) == (None, None, 1)
    assert world.sql(GROUNDING, (step_1_id,)) == [("pending", "[]", '["signal_count"]')]
    assert world.sql(
        "SELECT idempotency_key, lane FROM jobs WHERE type = 'check_grounding'"
    ) == [(f"check_grounding:{step_1_id}:1", "interactive")]


def test_a_tier_a_step_1_still_goes_to_the_route(world: World) -> None:
    sync_templates(world)
    activate(world, "draft_sequence")
    _, _, enrollment_id, draft_id, event, quote = step_1(world)
    llm = ScriptedLlm(ok(answer(event, quote)))

    assert draft(world, llm, enrollment_id) is StepDraftOutcome.DRAFTED

    assert len(llm.calls) == 1 and generic(world, draft_id) == 0
    assert world.sql(GROUNDING, (draft_id,)) == [("pending", "[]", "[]")]


def test_generic_copy_follows_the_tier(world: World) -> None:
    lead_id, _, enrollment_id, _ = tier_c(world)

    def decided() -> bool:
        enrollment = sequencing_queries.enrollment(enrollment_id)
        assert enrollment is not None
        return generic_copy(enrollment, world.clock.now())

    assert world.transact(decided) is True
    lead_with_signals(world, lead_id)  # a live Tier 1 Event: Tier A
    assert world.transact(decided) is False
    world.clock.set(world.clock.now() + timedelta(days=400))  # the Event is no longer live
    assert world.transact(decided) is True


def test_no_template_fails_not_composable_with_no_call(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    _, _, enrollment_id, [draft_id, _] = tier_c(world)  # no template synced
    llm = ScriptedLlm()

    with caplog.at_level(logging.INFO, logger=fallback.__name__):
        assert draft(world, llm, enrollment_id) is StepDraftOutcome.FAILED

    assert llm.calls == [] and revisions(world, draft_id) == []
    at, reason, rev = failure(world, draft_id)
    assert (reason, rev) == ("not_composable", None) and at is not None
    [record] = [r for r in caplog.records if r.message == fallback.GENERIC_DRAFT_FAILED]
    assert (record.draft_id, record.detail) == (draft_id, "no_template")  # type: ignore[attr-defined]


def test_a_missing_value_fails_not_composable(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    with_role = tuple(
        dataclasses.replace(each, body=each.body + "\n\nAs {role}, you know this.")
        if each.step == 1
        else each
        for each in templates()
    )
    sync_templates(world, with_role)
    _, _, enrollment_id, [draft_id, _] = tier_c(world)  # the Contact has no role

    with caplog.at_level(logging.INFO, logger=fallback.__name__):
        assert draft(world, ScriptedLlm(), enrollment_id) is StepDraftOutcome.FAILED

    assert revisions(world, draft_id) == [] and failure(world, draft_id)[1] == "not_composable"
    [record] = [r for r in caplog.records if r.message == fallback.GENERIC_DRAFT_FAILED]
    assert record.detail == "missing_value"  # type: ignore[attr-defined]


def test_a_blocked_subject_writes_one_event_and_nothing_else(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_templates(world)
    _, _, enrollment_id, [draft_id, _] = tier_c(world)
    monkeypatch.setattr(
        compliance_api,
        "assert_contactable",
        lambda *_: Blocked((BlockReason.ADDRESS_SUPPRESSED,)),
    )

    assert draft(world, ScriptedLlm(), enrollment_id) is StepDraftOutcome.BLOCKED

    assert revisions(world, draft_id) == [] and generic(world, draft_id) == 0
    assert failure(world, draft_id) == (None, None, None)
    assert world.events("drafts", draft_id)[-1][:2] == ("ready", "drafting_blocked")


def test_write_generic_runs_the_gate_itself(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_templates(world)
    _, _, enrollment_id, [_, step_2_id] = tier_c(world)
    asked: list[tuple[Any, ...]] = []

    def blocked(*args: Any) -> Blocked:
        asked.append(args[1:])
        return Blocked((BlockReason.ADDRESS_SUPPRESSED,))

    monkeypatch.setattr(compliance_api, "assert_contactable", blocked)

    assert write(world, step_2_id) is GenericOutcome.BLOCKED

    assert asked == [("email", "cold")] and revisions(world, step_2_id) == []
    assert world.events("drafts", step_2_id)[-1][1:4] == (
        "drafting_blocked",
        str(BlockReason.ADDRESS_SUPPRESSED),
        enrollment_id,
    )


# Steps 2 to 4 -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "fact"), [(2, "aba_formal_opinion_512"), (3, "mp_online_digital"), (4, None)]
)
def test_a_follow_up_takes_the_thread_subject_and_its_own_template(
    world: World, step: int, fact: str | None
) -> None:
    sync_templates(world)
    lead_id, _, _, draft_ids = tier_c(world, steps=(1, 2, 3, 4))

    assert write(world, draft_ids[step - 1]) is GenericOutcome.DRAFTED

    [row] = revisions(world, draft_ids[step - 1])
    assert row[:3] == (1, "generated", SUBJECT)
    assert row[3].startswith("Hi Zelda,") and lead_name(world, lead_id) in row[3]
    assert json.loads(row[5]) == ([] if fact is None else [[fact, 1]])
    assert generic(world, draft_ids[step - 1]) == 1
    # signal_count applies only on Step 1: nothing waived, and nothing flagged
    assert world.sql(GROUNDING, (draft_ids[step - 1],)) == [("pending", "[]", "[]")]
    [(lane,)] = world.sql("SELECT lane FROM jobs WHERE type = 'check_grounding'")
    assert lane == "bulk"


# The grounding ------------------------------------------------------------------------------------


def test_the_ai_check_runs_and_keeps_the_waiver(world: World) -> None:
    sync_templates(world)
    activate_grounding(world, "grounding_check")
    _, _, enrollment_id, [draft_id, _] = tier_c(world)
    assert draft(world, ScriptedLlm(), enrollment_id) is StepDraftOutcome.DRAFTED
    claim = {
        "span": LEGAL_AI,
        "evidence_id": None,
        "fact_id": "legal_ai",
        "fact_version": 1,
        "supported": True,
        "explanation": "The fact says so.",
    }
    llm = ScriptedLlm(grounding_ok([claim]))

    check(world, llm, draft_id, 1)

    assert len(llm.calls) == 1
    [(status, flags, waived)] = world.sql(GROUNDING, (draft_id,))
    assert (status, json.loads(flags), waived) == ("pass", [], '["signal_count"]')


def test_an_edit_keeps_the_flag_and_the_waiver(world: World) -> None:
    sync_templates(world)
    _, _, enrollment_id, [draft_id, _] = tier_c(world)
    draft(world, ScriptedLlm(), enrollment_id)

    assert revise(world, draft_id, SUBJECT, "Hi Zelda,\n\nWorth a look?") == 2

    assert generic(world, draft_id) == 1
    assert world.sql(GROUNDING, (draft_id,))[1] == ("pending", "[]", '["signal_count"]')


def test_a_route_made_regenerate_clears_the_flag(world: World) -> None:
    sync_templates(world)
    lead_id, _, enrollment_id, [draft_id, _] = tier_c(world)
    draft(world, ScriptedLlm(), enrollment_id)
    trace = real_trace(world, lead_id)  # a stored route tuple
    drafting = Drafting(world.clock, queue_on(world.clock))

    def regenerate() -> int:
        view = drafting_queries.draft(draft_id)
        assert view is not None
        return drafting._append(
            view,
            SUBJECT,
            "Hi Zelda,\n\nWorth a look?",
            RevisionOrigin.REGENERATED,
            trace,
            world.clock.now(),
            urgency=Urgency.INTERACTIVE,
        )

    assert world.transact(regenerate) == 2

    assert generic(world, draft_id) == 0
    assert world.sql(GROUNDING, (draft_id,))[1][2] == "[]"


def test_a_footer_change_keeps_the_flag(world: World) -> None:
    sync_templates(world)
    _, _, enrollment_id, [draft_id, _] = tier_c(world)
    draft(world, ScriptedLlm(), enrollment_id)

    save_footer(world, legal_name="Nextron AI Holdings LLC")

    assert [row[1] for row in revisions(world, draft_id)] == ["generated", "footer_change"]
    assert generic(world, draft_id) == 1
    assert world.sql(GROUNDING, (draft_id,))[1][2] == '["signal_count"]'


# The Lead page ------------------------------------------------------------------------------------


def test_the_lead_page_shows_a_generic_badge_only_on_a_generic_draft(world: World) -> None:
    sync_templates(world)
    lead_id, _, enrollment_id, [draft_id, step_2_id] = tier_c(world)
    draft(world, ScriptedLlm(), enrollment_id)

    page = lead_page(world, lead_id)

    first, second = page.find("section", class_="lead-drafts").find_all("article", class_="draft")
    assert first["data-draft"] == draft_id and second["data-draft"] == step_2_id
    [badge] = first.find_all("span", class_="badge-generic")
    assert badge.text == "generic"
    assert second.find_all("span", class_="badge-generic") == []


def test_the_shipped_templates_sync_against_the_shipped_facts(world: World) -> None:
    sync_templates(world)

    [(count,)] = world.sql("SELECT COUNT(*) FROM fallback_templates")
    assert count == 16
