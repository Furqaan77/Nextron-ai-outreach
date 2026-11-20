"""Edit a Draft, then approve it once the recheck passes (FR-27, FR-55, AD-3, AD-4, AD-6,
AD-10, AD-16, AD-18; Story 5.6), on the world of ``tests.app.test_due_queue``
(``tests.app.test_draft_revisions``: a temporary database at head, ``FrozenClock`` at
2026-11-18 10:00 UTC, the shipped seeds, Law-PrivateAI x US active, the footer settings
saved), with ``ScriptedLlm`` and the shipped grounding tuple activated (as in
``tests.app.test_ai_grounding``).

- the spec's I/O matrix through ``Approval.edit``, the ``check_grounding`` and
  ``complete_pending_approval`` jobs (run through the real job types' handlers) and
  ``Approval.complete_pending``: edit, pass, fail (AD-18 stray URL and an AI fail), a newer
  revision, a changed address, an old binding, an approved Draft with a queued OM, one in
  flight, a suppressed Contact and bad text; no-write proven by row counts;
- the flow end to end: edit POST, grounding job, completion job;
- ``drafting`` enqueues the completion only for the pending revision; the job type, its
  handler and its payload; approve and the cascades clear the pending approval;
- the page: the editor form outside the ``hx-disable`` article (no inline script), the card
  states, the "Approved, not yet sent" list, the keymap entries in ``queue-keys.js``;
- ``domain.render.is_major_edit``; and the acceptance checks: every OM has an ``approvals``
  row, and every edit approval's revision is ``operator_edit`` with ``approvals.rev`` equal.
"""

import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from nextron import main
from nextron.app.approval.api import (
    Approval,
    ApproveOutcome,
    ApproveResult,
    PendingApprovalFailed,
    UnknownDraft,
)
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.drafting.api import Drafting
from nextron.app.drafting.grounding import GroundingChecks
from nextron.app.queries import approval as approval_queries
from nextron.app.sending.api import Sending
from nextron.domain import render
from nextron.domain.ai import Urgency
from nextron.domain.draft import COMPLETE_PENDING_APPROVAL_JOB
from nextron.domain.job import InvalidJob, Job, JobGroup, Lane
from nextron.jobs import handlers
from tests.app.test_ai_grounding import (
    GOOD,
    activate,
    claim,
    evidence_of,
    good_answer,
    ok,
)
from tests.app.test_approve import counts as approve_counts
from tests.app.test_approve import nfr_8, oms_of, status_of
from tests.app.test_card_details import raw_text, set_grounding
from tests.app.test_draft_grounding_rules import GENERATED_BODY, HIRING, generated
from tests.app.test_draft_revisions import revise, world
from tests.app.test_due_queue import dashboard_on, queue_page_of, slot, suppress
from tests.app.test_enroll import World, queue_on
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.llm import ScriptedLlm
from tests.fakes.token_codec import FakeTokenCodec
from tests.unit.test_leads_page import Tree

__all__ = ["world"]  # the fixture

PACKAGE = Path(__file__).resolve().parents[2] / "nextron"
SUBJECT: Final = "your intake coordinator role"
EDITED_BODY: Final = GENERATED_BODY.replace("Worth a look?", "Worth a quick look?")
STRAY_URL: Final = GENERATED_BODY + "\n\nSee https://stray.example.com/page"
EXTRA: Final = ("draft_revisions", "jobs")


# Helpers ------------------------------------------------------------------------------------------


def services(world: World) -> tuple[Approval, Drafting]:
    queue = queue_on(world.clock)
    compliance = Compliance(world.clock, codec=FakeTokenCodec())
    drafting = Drafting(world.clock, queue)
    sending = Sending(world.clock, env="test", compliance=compliance)
    return Approval(world.clock, sending=sending, drafting=drafting), drafting


def approval_on(world: World) -> Approval:
    return services(world)[0]


def counts(world: World) -> dict[str, int]:
    found = approve_counts(world)
    for table in EXTRA:
        found[table] = world.sql(f"SELECT COUNT(*) FROM {table}")[0][0]
    return found


def card_of(world: World, draft_id: str) -> approval_queries.QueueCard:
    found = world.read(lambda: approval_queries.card_of(draft_id))
    assert found is not None
    return found


def pending_of(world: World, draft_id: str) -> tuple[Any, Any, Any]:
    [row] = world.sql(
        "SELECT pending_approval_rev, pending_approval_hash, pending_review_ms FROM drafts "
        "WHERE id = ?",
        (draft_id,),
    )
    return row  # type: ignore[return-value]


def edit(
    world: World,
    draft_id: str,
    subject: str = SUBJECT,
    body: str = EDITED_BODY,
    *,
    rev: int | None = None,
    shown_hash: str | None = None,
    approval: Approval | None = None,
    **stamps: Any,
) -> ApproveResult:
    card = card_of(world, draft_id)
    use = approval or approval_on(world)
    with world.units.open():
        return use.edit(
            draft_id,
            card.rev if rev is None else rev,  # type: ignore[arg-type]
            card.shown_hash if shown_hash is None else shown_hash,
            subject,
            body,
            **stamps,
        )


def approve(world: World, draft_id: str) -> ApproveResult:
    card = card_of(world, draft_id)
    assert card.rev is not None
    with world.units.open():
        return approval_on(world).approve(draft_id, card.rev, card.shown_hash)


def complete(world: World, draft_id: str, rev: int) -> ApproveResult | None:
    with world.units.open():
        return approval_on(world).complete_pending(draft_id, rev)


def jobs(world: World, job_type: str) -> list[tuple[str, str, dict[str, Any], str]]:
    return [
        (str(key), str(lane), json.loads(payload), str(status))
        for key, lane, payload, status in world.sql(
            "SELECT idempotency_key, lane, payload, status FROM jobs WHERE type = ? "
            "ORDER BY rowid",
            (job_type,),
        )
    ]


def registry(world: World, llm: ScriptedLlm, approval: Approval | None = None) -> Any:
    queue = queue_on(world.clock)
    chosen = approval or approval_on(world)
    return main.build_job_types(
        alerts=main._unwired,
        backups=main._unwired,
        restore_tests=main._unwired,
        recovery=main._unwired,
        contact_markets=main._unwired,
        crawls=main._unwired,
        discovery=main._unwired,
        research=main._unwired,
        extractions=main._unwired,
        primary=main._unwired,
        test_runs=main._unwired,
        job_boards=main._unwired,
        legal_forms=main._unwired,
        scores=main._unwired,
        grounding=lambda: GroundingChecks(world.clock, llm, queue),
        approval=lambda: chosen,
    )


def run_queued(world: World, job_type: str, job_types: Any) -> int:
    """Run every queued job of ``job_type`` through its real handler, each in its own unit of
    work as the runner does, and mark it done; how many ran."""
    rows = world.sql(
        "SELECT id, lane, payload FROM jobs WHERE type = ? AND status = 'queued' ORDER BY rowid",
        (job_type,),
    )
    handler = job_types.get(job_type).handler
    for job_id, lane, payload in rows:
        with world.units.open():
            handler(Job(str(job_id), job_type, Lane(lane), json.loads(payload), 1))
        world.sql("UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,))
    return len(rows)


def edited_draft(world: World) -> str:
    """A generated Step 1 Draft whose revision 1 passed both checks; its id."""
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    job_types = registry(world, ScriptedLlm(good_answer(world, draft_id)))
    run_queued(world, "check_grounding", job_types)
    assert card_of(world, draft_id).grounding_status == "pass"
    return draft_id


def passed(world: World) -> str:
    """A Step 1 ``slot`` with revision 1's grounding set ``pass``; its id."""
    _, _, _, [step_1] = slot(world)
    set_grounding(world, step_1, "pass")
    return step_1


def text_hash_of(world: World, draft_id: str) -> str:
    return card_of(world, draft_id).shown_hash


@pytest.fixture
def gate_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[object, ...]]]:
    calls: list[tuple[object, ...]] = []
    real = compliance_api.assert_contactable

    def recording(subject: object, channel: object, purpose: object) -> object:
        calls.append((subject, str(channel), str(purpose)))
        return real(subject, channel, purpose)  # type: ignore[arg-type]

    monkeypatch.setattr(compliance_api, "assert_contactable", recording)
    yield calls


# Edit -------------------------------------------------------------------------------------------


def test_an_edit_stores_an_operator_revision_and_a_pending_approval(world: World) -> None:
    step_1 = passed(world)
    served = world.clock.now()
    world.clock.advance(timedelta(seconds=9))
    before = counts(world)

    result = edit(world, step_1, served_at=served, first_served_at=served)

    assert result.outcome is ApproveOutcome.PENDING
    assert result.reasons == ("Recheck pending",)
    [(origin, subject, body)] = world.sql(
        "SELECT origin, subject, body FROM draft_revisions WHERE draft_id = ? AND rev = 2",
        (step_1,),
    )
    assert (origin, subject, body) == ("operator_edit", SUBJECT, EDITED_BODY)
    card = card_of(world, step_1)
    assert card.rev == 2 and card.pending_rev == 2
    assert pending_of(world, step_1) == (2, card.shown_hash, 9000)
    assert jobs(world, "check_grounding")[-1] == (
        f"check_grounding:{step_1}:2",
        "interactive",
        {"v": 1, "draft_id": step_1, "rev": 2},
        "queued",
    )
    after = counts(world)
    assert after["approvals"] == before["approvals"]
    assert after["outbound_messages"] == before["outbound_messages"]
    assert after["draft_revisions"] == before["draft_revisions"] + 1
    assert after["approval_sessions"] == before["approval_sessions"] + 1  # the decision counts
    [(decisions, active_ms)] = world.sql("SELECT decisions, active_ms FROM approval_sessions")
    assert (decisions, active_ms) == (1, 9000)
    assert status_of(world, step_1) == "ready"


def test_an_edit_trims_the_subject_and_keeps_revision_1(world: World) -> None:
    step_1 = passed(world)
    [first] = world.sql("SELECT * FROM draft_revisions WHERE draft_id = ? AND rev = 1", (step_1,))

    edit(world, step_1, "  New subject  ", "New body")

    [(subject,)] = world.sql(
        "SELECT subject FROM draft_revisions WHERE draft_id = ? AND rev = 2", (step_1,)
    )
    assert subject == "New subject"
    assert world.sql(
        "SELECT * FROM draft_revisions WHERE draft_id = ? AND rev = 1", (step_1,)
    ) == [first]


@pytest.mark.parametrize(
    ("subject", "body", "message"),
    [
        ("", EDITED_BODY, "The subject must be 1 to 200 characters"),
        ("x" * 201, EDITED_BODY, "The subject must be 1 to 200 characters"),
        ("two\nlines", EDITED_BODY, "The subject must be one line without control characters"),
        (SUBJECT, "x" * 10_001, "The body must be at most 10,000 characters"),
    ],
)
def test_bad_text_is_refused_with_its_message_and_writes_nothing(
    world: World, subject: str, body: str, message: str
) -> None:
    step_1 = passed(world)
    before = counts(world)

    result = edit(world, step_1, subject, body)

    assert (result.outcome, result.reasons) == (ApproveOutcome.BLOCKED, (message,))
    assert counts(world) == before
    assert pending_of(world, step_1) == (None, None, None)


def test_an_edit_that_changes_nothing_is_refused(world: World) -> None:
    step_1 = passed(world)
    [(subject, body)] = world.sql(
        "SELECT subject, body FROM draft_revisions WHERE draft_id = ?", (step_1,)
    )
    before = counts(world)

    result = edit(world, step_1, f" {subject} ", body)

    assert (result.outcome, result.reasons) == (ApproveOutcome.BLOCKED, ("Nothing changed",))
    assert counts(world) == before


@pytest.mark.parametrize("which", ["rev", "hash"])
def test_an_old_binding_is_stale_and_writes_nothing(world: World, which: str) -> None:
    step_1 = passed(world)
    old = card_of(world, step_1)
    revise(world, step_1, "Newer subject", "Newer body")  # rev 2
    before = counts(world)

    if which == "rev":
        result = edit(world, step_1, rev=1, shown_hash=card_of(world, step_1).shown_hash)
    else:
        result = edit(world, step_1, shown_hash=old.shown_hash)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.STALE,
        ("This Draft changed since you opened it",),
    )
    assert counts(world) == before


def test_an_unknown_draft_raises_and_other_statuses_are_refused(world: World) -> None:
    step_1 = passed(world)
    card = card_of(world, step_1)
    with world.units.open(), pytest.raises(UnknownDraft):
        approval_on(world).edit("01JD0000000000000000000099", 1, card.shown_hash, "s", "b")
    world.sql("UPDATE drafts SET status = 'cancelled' WHERE id = ?", (step_1,))
    before = counts(world)

    with world.units.open():
        result = approval_on(world).edit(step_1, 1, card.shown_hash, SUBJECT, EDITED_BODY)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.BLOCKED,
        ("This Draft is no longer in the queue",),
    )
    assert counts(world) == before


def test_a_card_that_cant_be_bound_is_refused(world: World) -> None:
    step_1 = passed(world)
    card = card_of(world, step_1)
    world.sql(
        "UPDATE contacts SET email = '' WHERE id = (SELECT e.contact_id FROM drafts d JOIN "
        "enrollments e ON e.id = d.enrollment_id WHERE d.id = ?)",
        (step_1,),
    )
    before = counts(world)

    with world.units.open():
        result = approval_on(world).edit(step_1, 1, card.shown_hash, SUBJECT, EDITED_BODY)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.BLOCKED,
        ("This Draft can't be approved yet",),
    )
    assert counts(world) == before


def test_edit_needs_drafting_and_typed_arguments(world: World) -> None:
    step_1 = passed(world)
    bare = Approval(world.clock)
    with world.units.open(), pytest.raises(TypeError, match="Drafting"):
        bare.edit(step_1, 1, "h", "s", "b")
    with world.units.open(), pytest.raises(TypeError):
        approval_on(world).edit(step_1, True, "h", "s", "b")  # type: ignore[arg-type]
    with world.units.open(), pytest.raises(TypeError):
        approval_on(world).edit(step_1, 1, "h", None, "b")  # type: ignore[arg-type]


# An approved Draft ------------------------------------------------------------------------------


def test_editing_an_approved_draft_cancels_its_queued_om_and_returns_it(world: World) -> None:
    step_1 = passed(world)
    approved = approve(world, step_1)
    assert approved.outcome is ApproveOutcome.APPROVED

    result = edit(world, step_1)

    assert result.outcome is ApproveOutcome.PENDING
    assert oms_of(world, step_1) == [(approved.om_id, "cancelled", approved.approval_id)]
    assert status_of(world, step_1) == "ready"
    events = world.events("drafts", step_1)
    assert events[-1][:5] == ("approved", "ready", "edited", approved.approval_id, "operator")
    assert pending_of(world, step_1)[0] == 2
    nfr_8(world)


@pytest.mark.parametrize("om_status", ["sending", "unknown", "sent"])
def test_an_om_in_flight_refuses_the_edit_and_changes_nothing(
    world: World, om_status: str
) -> None:
    step_1 = passed(world)
    approved = approve(world, step_1)
    world.sql(
        "UPDATE outbound_messages SET status = ? WHERE id = ?", (om_status, approved.om_id)
    )
    before = counts(world)

    result = edit(world, step_1)

    assert (result.outcome, result.reasons) == (ApproveOutcome.BLOCKED, ("Already sending",))
    assert counts(world) == before
    assert status_of(world, step_1) == "approved"
    assert oms_of(world, step_1)[0][1] == om_status


def test_bad_text_on_an_approved_draft_rolls_the_un_approve_back(world: World) -> None:
    step_1 = passed(world)
    approved = approve(world, step_1)
    before = counts(world)

    result = edit(world, step_1, "", EDITED_BODY)

    assert result.outcome is ApproveOutcome.BLOCKED
    assert counts(world) == before
    assert status_of(world, step_1) == "approved"
    assert oms_of(world, step_1) == [(approved.om_id, "queued", approved.approval_id)]


# The flow -----------------------------------------------------------------------------------------


def test_a_passing_recheck_approves_the_edit(world: World) -> None:
    draft_id = edited_draft(world)
    served = world.clock.now()
    world.clock.advance(timedelta(seconds=4))
    edited = edit(world, draft_id, served_at=served)
    assert edited.outcome is ApproveOutcome.PENDING
    pending_hash = pending_of(world, draft_id)[1]
    llm = ScriptedLlm(good_answer(world, draft_id))
    job_types = registry(world, llm)

    assert run_queued(world, "check_grounding", job_types) == 1
    assert jobs(world, COMPLETE_PENDING_APPROVAL_JOB) == [
        (
            f"complete_pending_approval:{draft_id}:2",
            "interactive",
            {"v": 1, "draft_id": draft_id, "rev": 2},
            "queued",
        )
    ]
    sessions_before = world.sql("SELECT * FROM approval_sessions")
    assert run_queued(world, COMPLETE_PENDING_APPROVAL_JOB, job_types) == 1

    assert status_of(world, draft_id) == "approved"
    [(approval_id, rev, text_hash, approved_by, review_ms, ratio)] = world.sql(
        "SELECT id, rev, text_hash, approved_by, review_ms, edit_ratio FROM approvals"
    )
    assert (rev, text_hash, approved_by, review_ms) == (2, pending_hash, "operator", 4000)
    assert ratio == pytest.approx(
        render.edit_ratio(_Text(SUBJECT, GENERATED_BODY), _Text(SUBJECT, EDITED_BODY))
    )
    assert ratio > 0
    [(_, om_status, om_approval)] = oms_of(world, draft_id)
    assert (om_status, om_approval) == ("queued", approval_id)
    assert pending_of(world, draft_id) == (None, None, None)
    assert world.events("drafts", draft_id)[-1][:5] == (
        "ready",
        "approved",
        "approved",
        approval_id,
        "operator",
    )
    assert world.sql("SELECT * FROM approval_sessions") == sessions_before  # no session change
    acceptance(world)


class _Text:
    def __init__(self, subject: str, body: str) -> None:
        self.subject = subject
        self.body = body


def test_the_flow_runs_end_to_end_from_the_edit_post(world: World) -> None:
    draft_id = edited_draft(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    page = queue_page_of(dashboard, cookie, b"draft=" + draft_id.encode())
    fields = edit_fields(page)
    fields["body"] = EDITED_BODY.replace("\n", "\r\n")  # as a browser sends a textarea

    reply = post_edit(dashboard, cookie, fields)

    assert reply.status == 200, reply
    after = Tree(reply.text)
    assert after.find("article", class_="queue-card").attrs["data-draft"] == draft_id
    assert [node.text for node in after.find_all("p", class_="queue-notice")] == [
        "Recheck pending"
    ]
    badges = [node.text for node in after.find_all("span", class_="queue-badge")]
    assert "Recheck pending" in badges
    assert not after.find_all("button", class_="queue-approve", type="submit")
    [(body,)] = world.sql(
        "SELECT body FROM draft_revisions WHERE draft_id = ? AND rev = 2", (draft_id,)
    )
    assert body == EDITED_BODY  # the line breaks normalised
    job_types = registry(world, ScriptedLlm(good_answer(world, draft_id)))
    run_queued(world, "check_grounding", job_types)
    run_queued(world, COMPLETE_PENDING_APPROVAL_JOB, job_types)

    assert status_of(world, draft_id) == "approved"
    assert len(oms_of(world, draft_id)) == 1
    listed = queue_page_of(dashboard, cookie)
    [row] = listed.find_all("li", class_="approved-row")
    assert row.find("a").attrs["href"] == f"/queue?draft={draft_id}"
    assert row.find("span", class_="approved-om-status").text == "queued"
    shown = queue_page_of(dashboard, cookie, b"draft=" + draft_id.encode())
    assert shown.find("article", class_="queue-card").attrs["data-draft"] == draft_id
    assert "Already approved" in [
        node.text for node in shown.find_all("p", class_="queue-approve-notice")
    ]
    assert shown.find_all("details", class_="queue-edit")  # the editor is there
    acceptance(world)


def test_a_stray_url_fails_the_recheck_and_clears_the_pending_approval(world: World) -> None:
    draft_id = edited_draft(world)
    edit(world, draft_id, SUBJECT, STRAY_URL)
    job_types = registry(world, ScriptedLlm(good_answer(world, draft_id)))

    run_queued(world, "check_grounding", job_types)
    assert len(jobs(world, COMPLETE_PENDING_APPROVAL_JOB)) == 1
    before = counts(world)
    run_queued(world, COMPLETE_PENDING_APPROVAL_JOB, job_types)

    assert pending_of(world, draft_id) == (None, None, None)
    assert status_of(world, draft_id) == "ready"
    assert counts(world) == before  # only the pending columns changed
    dashboard = dashboard_on(world)
    shown = queue_page_of(dashboard, dashboard.open_session(), b"draft=" + draft_id.encode())
    card = shown.find("article", class_="queue-card")
    assert "grounding-fail" in str(card.attrs.get("class"))
    assert shown.find_all("li", class_="grounding-flag")


def test_an_ai_fail_clears_the_pending_approval(world: World) -> None:
    draft_id = edited_draft(world)
    edit(world, draft_id)
    quote, _ = evidence_of(world, draft_id)
    job_types = registry(world, ScriptedLlm(ok([claim(HIRING, quote, supported=False)])))

    run_queued(world, "check_grounding", job_types)
    run_queued(world, COMPLETE_PENDING_APPROVAL_JOB, job_types)

    assert pending_of(world, draft_id) == (None, None, None)
    assert world.sql("SELECT COUNT(*) FROM approvals") == [(0,)]


def test_a_newer_revision_makes_the_completion_stale(world: World) -> None:
    draft_id = edited_draft(world)
    edit(world, draft_id)
    revise(world, draft_id, SUBJECT, EDITED_BODY + " Thanks.")  # rev 3, e.g. a regenerate
    answers = ScriptedLlm(good_answer(world, draft_id), good_answer(world, draft_id))
    job_types = registry(world, answers)
    run_queued(world, "check_grounding", job_types)  # rev 2's and rev 3's
    [key] = [row[0] for row in jobs(world, COMPLETE_PENDING_APPROVAL_JOB)]
    assert key == f"complete_pending_approval:{draft_id}:2"  # never for rev 3
    before = counts(world)
    pending = pending_of(world, draft_id)

    assert complete(world, draft_id, 2) == ApproveResult(
        ApproveOutcome.STALE, draft_id, ("This Draft changed since you opened it",)
    )
    assert counts(world) == before
    assert pending_of(world, draft_id) == pending
    card = card_of(world, draft_id)
    assert (card.rev, card.pending_rev) == (3, 2)
    dashboard = dashboard_on(world)
    shown = queue_page_of(dashboard, dashboard.open_session(), b"draft=" + draft_id.encode())
    assert "stale" in [node.text for node in shown.find_all("span", class_="queue-badge")]
    assert shown.find("p", class_="queue-pending-notice").text == (
        "This Draft changed since you opened it"
    )
    assert EDITED_BODY + " Thanks." in raw_text(shown.find("pre", class_="draft-body"))


def test_a_changed_address_before_completion_approves_nothing(world: World) -> None:
    step_1 = passed(world)
    edit(world, step_1)
    set_grounding(world, step_1, "pass")
    world.sql(
        "UPDATE contacts SET email = 'other@firm.example.com' WHERE id = (SELECT e.contact_id "
        "FROM drafts d JOIN enrollments e ON e.id = d.enrollment_id WHERE d.id = ?)",
        (step_1,),
    )
    before = counts(world)

    result = complete(world, step_1, 2)

    assert result is not None and result.outcome is ApproveOutcome.STALE
    assert counts(world) == before


def test_a_suppressed_contact_clears_the_pending_approval(
    world: World, gate_calls: list[tuple[object, ...]]
) -> None:
    _, contact_id, _, [step_1] = slot(world)
    set_grounding(world, step_1, "pass")
    edit(world, step_1)
    set_grounding(world, step_1, "pass")
    suppress(world, contact_id)
    gate_calls.clear()
    before = counts(world)

    result = complete(world, step_1, 2)

    assert result is not None and result.outcome is ApproveOutcome.BLOCKED
    assert result.reasons[0] == "Contact is suppressed"
    assert [call[2] for call in gate_calls] == ["cold"]
    assert counts(world) == before
    assert pending_of(world, step_1) == (None, None, None)


def test_completion_does_nothing_without_a_pending_approval_for_that_rev(world: World) -> None:
    step_1 = passed(world)
    before = counts(world)

    assert complete(world, step_1, 1) is None
    assert complete(world, "01JD0000000000000000000099", 1) is None
    assert counts(world) == before
    edit(world, step_1)
    assert complete(world, step_1, 1) is None
    assert pending_of(world, step_1)[0] == 2


def test_a_pending_grounding_writes_nothing(world: World) -> None:
    step_1 = passed(world)
    edit(world, step_1)  # rev 2: rules pass, the AI check pending
    pending = pending_of(world, step_1)
    before = counts(world)

    result = complete(world, step_1, 2)

    assert result is not None and result.reasons == ("Grounding check pending",)
    assert counts(world) == before and pending_of(world, step_1) == pending


def test_a_failed_enqueue_rolls_back_and_raises_so_the_job_retries(world: World) -> None:
    step_1 = passed(world)
    edit(world, step_1)
    set_grounding(world, step_1, "pass")
    pending = pending_of(world, step_1)
    before = counts(world)
    broken = Approval(
        world.clock,
        sending=Sending(world.clock, env="test", compliance=Compliance(world.clock)),  # no codec
    )

    with world.units.open(), pytest.raises(PendingApprovalFailed):
        broken.complete_pending(step_1, 2)

    assert counts(world) == before
    assert pending_of(world, step_1) == pending
    assert status_of(world, step_1) == "ready"


def test_approve_clears_a_pending_approval(world: World) -> None:
    step_1 = passed(world)
    edit(world, step_1)
    set_grounding(world, step_1, "pass")

    assert approve(world, step_1).outcome is ApproveOutcome.APPROVED

    assert pending_of(world, step_1) == (None, None, None)
    assert complete(world, step_1, 2) is None


def test_a_stop_clears_the_pending_approval(world: World) -> None:
    _, _, enrollment_id, [step_1] = slot(world)
    set_grounding(world, step_1, "pass")
    edit(world, step_1)

    cancelled = world.transact(lambda: Approval(world.clock).cancel_pending([enrollment_id]))

    assert cancelled == (step_1,)
    assert status_of(world, step_1) == "cancelled"
    assert pending_of(world, step_1) == (None, None, None)


# Drafting's enqueue --------------------------------------------------------------------------------


def test_grounding_enqueues_the_completion_only_for_the_pending_revision(world: World) -> None:
    activate(world, "grounding_check")
    draft_id = generated(world, GOOD)
    job_types = registry(world, ScriptedLlm(good_answer(world, draft_id)))

    run_queued(world, "check_grounding", job_types)

    assert jobs(world, COMPLETE_PENDING_APPROVAL_JOB) == []


def test_grounding_checks_without_a_queue_refuse_a_pending_revision(world: World) -> None:
    draft_id = edited_draft(world)
    edit(world, draft_id)
    llm = ScriptedLlm(good_answer(world, draft_id))

    with world.units.open(), pytest.raises(RuntimeError, match="job queue"):
        GroundingChecks(world.clock, llm).check(draft_id, 2, Urgency.INTERACTIVE)

    [(ai_checked_at,)] = world.sql(
        "SELECT ai_checked_at FROM grounding_results WHERE draft_id = ? AND rev = 2", (draft_id,)
    )
    assert ai_checked_at is None  # rolled back


# The job type -------------------------------------------------------------------------------------


def test_the_job_type_is_registered_as_specified(world: World) -> None:
    found = main.enqueue_only_job_types().get(COMPLETE_PENDING_APPROVAL_JOB)

    assert (
        found.default_lane,
        found.lanes,
        found.priority,
        found.max_runtime,
        found.retry_limit,
        found.group,
    ) == (
        Lane.INTERACTIVE,
        frozenset({Lane.INTERACTIVE}),
        60,
        timedelta(minutes=2),
        3,
        JobGroup.OTHER,
    )


class Recording:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def complete_pending(self, draft_id: str, rev: int) -> None:
        self.calls.append((draft_id, rev))


DRAFT: Final = "01JD0000000000000000000010"


def test_the_handler_calls_complete_pending_once() -> None:
    recording = Recording()
    handler = handlers.complete_pending_approval(lambda: recording)

    payload = {"v": 1, "draft_id": DRAFT, "rev": 2}
    assert handler(Job("01JD0000000000000000000011", "x", Lane.INTERACTIVE, payload, 1)) is None
    assert recording.calls == [(DRAFT, 2)]


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 1, "rev": 2},
        {"v": 1, "draft_id": "nope", "rev": 2},
        {"v": 1, "draft_id": DRAFT},
        {"v": 1, "draft_id": DRAFT, "rev": 0},
        {"v": 1, "draft_id": DRAFT, "rev": True},
        {"v": 1, "draft_id": DRAFT, "rev": "2"},
    ],
)
def test_the_handler_refuses_a_bad_payload(payload: dict[str, object]) -> None:
    recording = Recording()
    handler = handlers.complete_pending_approval(lambda: recording)

    with pytest.raises(InvalidJob):
        handler(Job("01JD0000000000000000000011", "x", Lane.INTERACTIVE, payload, 1))
    assert recording.calls == []


def test_a_registry_without_approval_fails_the_job() -> None:
    handler = main.build_job_types(
        alerts=main._unwired,
        backups=main._unwired,
        restore_tests=main._unwired,
        recovery=main._unwired,
        contact_markets=main._unwired,
        crawls=main._unwired,
        discovery=main._unwired,
        research=main._unwired,
        extractions=main._unwired,
        primary=main._unwired,
        test_runs=main._unwired,
        job_boards=main._unwired,
        legal_forms=main._unwired,
        scores=main._unwired,
    ).get(COMPLETE_PENDING_APPROVAL_JOB).handler

    with pytest.raises(RuntimeError, match="without approval"):
        handler(
            Job(
                "01JD0000000000000000000011",
                COMPLETE_PENDING_APPROVAL_JOB,
                Lane.INTERACTIVE,
                {"v": 1, "draft_id": DRAFT, "rev": 1},
                1,
            )
        )


# The page -----------------------------------------------------------------------------------------


def post_edit(dashboard: Dashboard, cookie: bytes, fields: dict[str, str]) -> Reply:
    return dashboard.post(
        "/queue/edit",
        cookie=cookie,
        token=dashboard.csrf_token(cookie),
        headers=[(b"content-type", FORM)],
        body=form(**fields),
        query=b"card=1",
    )


def edit_fields(page: Tree) -> dict[str, str]:
    edit_form = page.find("form", class_="queue-edit-form")
    found: dict[str, str] = {}
    for node in edit_form.find_all("input"):
        name = node.attrs.get("name")
        if name and name != "csrf_token":
            found[str(name)] = str(node.attrs.get("value") or "")
    body = raw_text(edit_form.find("textarea"))
    found["body"] = body.removeprefix("\n")  # a browser drops the newline after <textarea>
    return found


def test_the_editor_form_sits_outside_the_article_with_the_current_text(world: World) -> None:
    step_1 = passed(world)
    [(subject, body)] = world.sql(
        "SELECT subject, body FROM draft_revisions WHERE draft_id = ?", (step_1,)
    )
    dashboard = dashboard_on(world)
    page = queue_page_of(dashboard, dashboard.open_session())

    [details] = page.find_all("details", class_="queue-edit")
    [edit_form] = details.find_all("form", class_="queue-edit-form")
    assert edit_form.attrs["method"] == "post"
    assert edit_form.attrs["hx-post"] == "/queue/edit?card=1"
    assert not any("hx-disable" in node.attrs for node in edit_form.ancestors())
    assert any(
        node.attrs.get("name") == "csrf_token" for node in edit_form.find_all("input")
    )
    card = card_of(world, step_1)
    fields = edit_fields(page)
    assert fields == {
        "draft_id": step_1,
        "rev": "1",
        "shown_hash": card.shown_hash,
        "subject": subject,
        "body": body,
    }
    footer = raw_text(edit_form.find("pre", class_="queue-edit-footer"))
    assert footer and card.message is not None and card.message.body.endswith(footer)
    assert "hx-disable" in edit_form.find("div", class_="queue-edit-fields").attrs
    for script in page.find_all("script"):  # no inline script
        assert script.attrs.get("src") and not script.text.strip()


def test_a_refused_edit_post_shows_its_message_and_writes_nothing(world: World) -> None:
    step_1 = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    fields = edit_fields(queue_page_of(dashboard, cookie))
    fields["subject"] = ""
    before = counts(world)

    reply = post_edit(dashboard, cookie, fields)

    assert reply.status == 200, reply
    after = Tree(reply.text)
    assert [node.text for node in after.find_all("p", class_="queue-notice")] == [
        "The subject must be 1 to 200 characters"
    ]
    assert after.find("article", class_="queue-card").attrs["data-draft"] == step_1
    assert counts(world) == before


def test_the_edit_post_refuses_a_bad_form_and_an_unknown_draft(world: World) -> None:
    passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    fields = edit_fields(queue_page_of(dashboard, cookie))
    before = counts(world)

    assert post_edit(dashboard, cookie, {**fields, "extra": "1"}).status == 400
    assert post_edit(dashboard, cookie, {**fields, "body": "x" * 40_001}).status == 400
    unknown = post_edit(dashboard, cookie, {**fields, "draft_id": "01JD0000000000000000000099"})
    assert unknown.status == 404
    assert dashboard.get("/queue/edit", cookie=cookie).status == 405
    bad_token = dashboard.post(
        "/queue/edit",
        cookie=cookie,
        token="wrong",
        headers=[(b"content-type", FORM)],
        body=form(**fields),
    )
    assert bad_token.status == 403
    assert counts(world) == before


def test_the_keymap_maps_e_ctrl_enter_and_escape_from_one_table() -> None:
    source = (PACKAGE / "web" / "static" / "queue-keys.js").read_text(encoding="utf-8")
    [table] = re.findall(r"var KEYMAP = \{(.*?)\};", source, flags=re.DOTALL)
    entries = dict(re.findall(r'"?([\w+]+)"?:\s*"(\w+)"', table))
    assert entries == {
        "a": "approve",
        "e": "edit",
        "ctrl+enter": "submitEdit",
        "escape": "cancelEdit",
    }
    assert source.count("var KEYMAP") == 1
    assert "eval(" not in source and "innerHTML" not in source


def test_the_page_lists_approved_drafts_not_yet_sent(world: World) -> None:
    step_1 = passed(world)
    approved = approve(world, step_1)
    world.sql("UPDATE outbound_messages SET status = 'sent' WHERE id = ?", (approved.om_id,))
    other = passed(world)
    approve(world, other)
    dashboard = dashboard_on(world)

    page = queue_page_of(dashboard, dashboard.open_session())

    hrefs = [node.find("a").attrs["href"] for node in page.find_all("li", class_="approved-row")]
    assert hrefs == [f"/queue?draft={other}"]  # the sent one isn't listed
    assert world.read(approval_queries.approved_unsent)[0].draft_id == other


# Domain and acceptance ----------------------------------------------------------------------------


def test_is_major_edit_is_above_0_20() -> None:
    assert render.MAJOR_EDIT_RATIO == 0.20
    assert render.is_major_edit(0.21) is True
    assert render.is_major_edit(0.20) is False
    assert render.is_major_edit(0.0) is False
    with pytest.raises(TypeError):
        render.is_major_edit(True)  # type: ignore[arg-type]


def acceptance(world: World) -> None:
    """Every OM has an ``approvals`` row; every edit approval's revision is ``operator_edit``
    and its ``approvals.rev`` is that revision."""
    nfr_8(world)
    rows = world.sql(
        "SELECT a.rev, r.origin FROM approvals a JOIN draft_revisions r "
        "ON r.draft_id = a.draft_id AND r.rev = a.rev WHERE a.rev > 1"
    )
    assert rows and all(origin == "operator_edit" for _, origin in rows)
