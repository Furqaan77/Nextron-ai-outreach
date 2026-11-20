"""Approve a Draft bound to the exact text shown (FR-26, FR-55, AD-4, AD-6, AD-13, AD-16; Story
5.4), on the world of ``tests.app.test_due_queue`` (``tests.app.test_draft_revisions``: a
temporary database at head, ``FrozenClock`` at 2026-11-18 10:00 UTC, the shipped seeds,
Law-PrivateAI x US active, the footer settings saved).

The spec's I/O matrix through ``Approval.approve`` and ``POST /queue/approve``: the happy path
(an ``approvals`` row, the Draft ``approved`` with its ``operator`` event, one ``queued`` OM),
the purposes per kind, every refusal (old rev, changed hash, twice, grounding, footer,
suppressed, reply, enqueue failure) writing nothing (row counts of ``approvals``,
``approval_sessions``, ``events``, ``outbound_messages`` and ``unsub_tokens``), the metrics and
sessions, the cascade un-approving on a stop or a suppression, ``approval_of``, the tables'
triggers and index, the page (form, keymap script, notices, 405/403/404/400) and NFR-8: every
OM has an ``approvals`` row. Plus ``domain.render.edit_ratio`` (incl. its timing bound) and
``domain.draft.contact_purpose``.
"""

import logging
import random
import sqlite3
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from nextron.app.approval import api as approval_api
from nextron.app.approval.api import Approval, ApproveOutcome, ApproveResult, UnknownDraft
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.queries import approval as approval_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.sending.api import Sending
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.domain import render
from nextron.domain.contactability import Allowed, Blocked, BlockReason, ContactSubject, Purpose
from nextron.domain.draft import DraftKind, contact_purpose
from nextron.ports.errors import AdapterError
from tests.app.test_card_details import hidden, set_grounding
from tests.app.test_draft_revisions import revise, world
from tests.app.test_due_queue import (
    NOW,
    dashboard_on,
    queue_page_of,
    responsive,
    slot,
    stamp,
    suppress,
)
from tests.app.test_enroll import World
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.token_codec import FakeTokenCodec
from tests.unit.test_leads_page import Tree

__all__ = ["world"]  # the fixture

PACKAGE = Path(__file__).resolve().parents[2] / "nextron"
COUNTED = ("approvals", "approval_sessions", "events", "outbound_messages", "unsub_tokens")


# Helpers ------------------------------------------------------------------------------------------


def approval_on(world: World, codec: FakeTokenCodec | None = None) -> Approval:
    compliance = Compliance(world.clock, codec=codec or FakeTokenCodec())
    return Approval(world.clock, sending=Sending(world.clock, env="test", compliance=compliance))


def counts(world: World) -> dict[str, int]:
    return {table: world.sql(f"SELECT COUNT(*) FROM {table}")[0][0] for table in COUNTED}


def card_of(world: World, draft_id: str) -> approval_queries.QueueCard:
    found = world.read(lambda: approval_queries.card_of(draft_id))
    assert found is not None
    return found


def passed(world: World, steps: tuple[int, ...] = (1,)) -> tuple[str, str, str, list[str]]:
    """``slot`` with each revision's grounding ``pass``: (lead, contact, enrollment, drafts)."""
    lead_id, contact_id, enrollment_id, ids = slot(world, steps)
    for draft_id in ids:
        set_grounding(world, draft_id, "pass")
    return lead_id, contact_id, enrollment_id, ids


def approve(
    world: World,
    draft_id: str,
    *,
    rev: int | None = None,
    shown_hash: str | None = None,
    approval: Approval | None = None,
    **stamps: object,
) -> ApproveResult:
    card = card_of(world, draft_id)
    use = approval or approval_on(world)
    with world.units.open():
        return use.approve(
            draft_id,
            card.rev if rev is None else rev,  # type: ignore[arg-type]
            card.shown_hash if shown_hash is None else shown_hash,
            **stamps,  # type: ignore[arg-type]
        )


def status_of(world: World, draft_id: str) -> str:
    return str(world.sql("SELECT status FROM drafts WHERE id = ?", (draft_id,))[0][0])


def oms_of(world: World, draft_id: str) -> list[tuple[str, str, str]]:
    return [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in world.sql(
            "SELECT id, status, approval_id FROM outbound_messages WHERE draft_id = ? "
            "ORDER BY rowid",
            (draft_id,),
        )
    ]


def sessions(world: World) -> list[tuple[object, ...]]:
    return world.sql(
        "SELECT started_at, ended_at, decisions, active_ms FROM approval_sessions ORDER BY rowid"
    )


def copy_grounding(world: World, source: str, target: str) -> None:
    """Give ``target`` a copy of ``source``'s grounding result (plain SQL, no foreign keys)."""
    raw(
        world,
        "INSERT INTO grounding_results (id, draft_id, rev, status, flags, rules_version, "
        "route_tuple_id, served_model, checked_at, ai_checked_at) SELECT "
        f"'{new_id()}', '{target}', rev, status, flags, rules_version, route_tuple_id, "
        f"served_model, checked_at, ai_checked_at FROM grounding_results WHERE draft_id = "
        f"'{source}'",
    )


def nfr_8(world: World) -> None:
    """NFR-8: every OutboundMessage has an ``approvals`` row for its Draft."""
    orphans = world.sql(
        "SELECT om.id FROM outbound_messages om LEFT JOIN approvals a "
        "ON a.id = om.approval_id AND a.draft_id = om.draft_id WHERE a.id IS NULL"
    )
    assert orphans == []


@pytest.fixture
def gate_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[object, ...]]]:
    """Record each gate call (subject, channel, purpose), answering as the real gate."""
    calls: list[tuple[object, ...]] = []
    real = compliance_api.assert_contactable

    def recording(subject: object, channel: object, purpose: object) -> Allowed | Blocked:
        calls.append((subject, str(channel), str(purpose)))
        return real(subject, channel, purpose)  # type: ignore[arg-type]

    monkeypatch.setattr(compliance_api, "assert_contactable", recording)
    yield calls


# Happy path ---------------------------------------------------------------------------------------


def test_an_approved_step_1_is_one_approval_one_event_and_one_queued_om(
    world: World, gate_calls: list[tuple[object, ...]]
) -> None:
    _, contact_id, _, [step_1] = passed(world)
    card = card_of(world, step_1)
    before = counts(world)
    gate_calls.clear()

    result = approve(world, step_1)

    assert result.outcome is ApproveOutcome.APPROVED and result.reasons == ()
    assert result.queue_clear is True
    after = counts(world)
    assert after == {
        "approvals": before["approvals"] + 1,
        "approval_sessions": before["approval_sessions"] + 1,
        "events": before["events"] + 2,  # the Draft's move and the OM's creation
        "outbound_messages": before["outbound_messages"] + 1,
        "unsub_tokens": before["unsub_tokens"] + 1,
    }
    [row] = world.sql(
        "SELECT id, draft_id, rev, text_hash, approved_by, approved_at, review_ms, edit_ratio "
        "FROM approvals"
    )
    now = stamp(world.clock.now())
    assert row == (result.approval_id, step_1, 1, card.shown_hash, "operator", now, None, 0.0)
    assert status_of(world, step_1) == "approved"
    assert world.events("drafts", step_1)[-1] == (
        "ready",
        "approved",
        "approved",
        result.approval_id,
        "operator",
        now,
    )
    assert oms_of(world, step_1) == [(result.om_id, "queued", result.approval_id)]
    [(approved_hash,)] = world.sql(
        "SELECT approved_hash FROM outbound_messages WHERE id = ?", (result.om_id,)
    )
    assert approved_hash == card.shown_hash
    # approve's own gate call, then enqueue's, then the queue-clear check's Eligibility
    assert [call[1:] for call in gate_calls[:2]] == [("email", "cold"), ("email", "cold")]
    assert gate_calls[0][0] == ContactSubject(contact_id)
    view = world.read(lambda: approval_queries.approval_of(step_1))
    assert view == approval_queries.ApprovalRow(
        str(result.approval_id), step_1, 1, world.clock.now(), card.shown_hash
    )
    nfr_8(world)


@pytest.mark.parametrize(
    ("steps", "kind", "purpose"),
    [((1, 2), "step", "follow_up"), ((1,), "recontact", "recontact")],
)
def test_a_follow_up_and_a_recontact_are_gated_with_their_purpose(
    world: World,
    gate_calls: list[tuple[object, ...]],
    steps: tuple[int, ...],
    kind: str,
    purpose: str,
) -> None:
    _, _, _, ids = passed(world, steps)
    draft_id = ids[-1]
    world.sql("UPDATE drafts SET kind = ?, due_at = ? WHERE id = ?", (kind, stamp(NOW), draft_id))
    gate_calls.clear()

    result = approve(world, draft_id)

    assert result.outcome is ApproveOutcome.APPROVED
    assert [call[2] for call in gate_calls[:2]] == [purpose, purpose]
    assert status_of(world, draft_id) == "approved"
    nfr_8(world)


# Refusals write nothing ---------------------------------------------------------------------------


def refused(world: World, draft_id: str, **kwargs: object) -> ApproveResult:
    before = counts(world)
    status = status_of(world, draft_id)
    result = approve(world, draft_id, **kwargs)  # type: ignore[arg-type]
    assert result.outcome is not ApproveOutcome.APPROVED
    assert counts(world) == before
    assert status_of(world, draft_id) == status
    assert result.approval_id is None and result.om_id is None
    return result


def test_an_old_rev_is_stale(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    old = card_of(world, step_1)
    revise(world, step_1)
    set_grounding(world, step_1, "pass")

    result = refused(world, step_1, rev=1, shown_hash=old.shown_hash)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.STALE,
        ("This Draft changed since you opened it",),
    )


def test_a_changed_email_is_a_stale_hash(world: World) -> None:
    _, contact_id, _, [step_1] = passed(world)
    old = card_of(world, step_1)
    world.sql(
        "UPDATE contacts SET email = 'new.address@acmelaw.com' WHERE id = ?", (contact_id,)
    )

    result = refused(world, step_1, shown_hash=old.shown_hash)

    assert result.outcome is ApproveOutcome.STALE
    assert card_of(world, step_1).shown_hash != old.shown_hash


def test_a_second_approval_is_already_approved(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    card = card_of(world, step_1)
    assert approve(world, step_1).outcome is ApproveOutcome.APPROVED

    result = refused(world, step_1, rev=card.rev, shown_hash=card.shown_hash)

    assert (result.outcome, result.reasons) == (ApproveOutcome.BLOCKED, ("Already approved",))
    nfr_8(world)


def test_a_cancelled_draft_is_no_longer_in_the_queue(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    card = card_of(world, step_1)
    world.sql("UPDATE drafts SET status = 'cancelled' WHERE id = ?", (step_1,))

    result = refused(world, step_1, rev=card.rev, shown_hash=card.shown_hash)

    assert result.reasons == ("This Draft is no longer in the queue",)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("fail", "Grounding check failed"),
        ("pending", "Grounding check pending"),
        (None, "Grounding check pending"),
    ],
)
def test_grounding_that_isnt_pass_blocks(world: World, status: str | None, reason: str) -> None:
    _, _, _, [step_1] = slot(world)
    if status is None:
        world.sql("DELETE FROM grounding_results WHERE draft_id = ?", (step_1,))
    else:
        flags = (("word_limit", "intake", "Too long"),) if status == "fail" else ()
        set_grounding(world, step_1, status, flags)

    result = refused(world, step_1)

    assert (result.outcome, result.reasons) == (ApproveOutcome.BLOCKED, (reason,))


def test_an_incomplete_footer_blocks_with_its_notice(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    card = card_of(world, step_1)
    world.sql("UPDATE settings SET value = ' ' WHERE key = 'legal_name'")
    blocked = card_of(world, step_1).blocked
    assert blocked

    result = refused(world, step_1, rev=card.rev, shown_hash=card.shown_hash)

    assert (result.outcome, result.reasons) == (ApproveOutcome.BLOCKED, (blocked,))


def test_a_suppressed_contact_is_blocked_with_the_gates_reasons(world: World) -> None:
    _, contact_id, _, [step_1] = passed(world)
    suppress(world, contact_id)  # raw: no cascade

    result = refused(world, step_1)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.BLOCKED,
        ("Contact is suppressed", "address_suppressed"),
    )


def test_a_reply_is_gated_as_a_reply_then_refused(
    world: World, gate_calls: list[tuple[object, ...]]
) -> None:
    _, _, enrollment_id, [step_1] = passed(world)
    reply = responsive(world, enrollment_id, step_1, kind="reply")
    copy_grounding(world, step_1, reply)
    gate_calls.clear()

    result = refused(world, reply, shown_hash="0" * 64)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.BLOCKED,
        ("This Draft can't be approved yet",),
    )
    assert [call[1:] for call in gate_calls] == [("email", "reply")]


def test_a_rebook_is_gated_as_a_rebook_then_refused(
    world: World, gate_calls: list[tuple[object, ...]]
) -> None:
    _, _, enrollment_id, [step_1] = passed(world)
    rebook = responsive(world, enrollment_id, step_1, kind="rebook")
    copy_grounding(world, step_1, rebook)
    gate_calls.clear()

    result = refused(world, rebook, shown_hash="0" * 64)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.BLOCKED,
        ("This Draft can't be approved yet",),
    )
    assert [call[1:] for call in gate_calls] == [("email", "rebook")]


def gate_raises_once(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """The gate's next call raises ``error``; later calls answer as the real gate."""
    real = compliance_api.assert_contactable
    raised: list[bool] = []

    def once(subject: object, channel: object, purpose: object) -> Allowed | Blocked:
        if not raised:
            raised.append(True)
            raise error
        return real(subject, channel, purpose)  # type: ignore[arg-type]

    monkeypatch.setattr(compliance_api, "assert_contactable", once)


@pytest.mark.parametrize("error", [LookupError("unknown Contact"), ValueError("merged Lead")])
def test_a_gate_that_cannot_look_up_the_contact_blocks(
    world: World, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _, _, _, [step_1] = passed(world)
    gate_raises_once(monkeypatch, error)

    result = refused(world, step_1)

    assert (result.outcome, result.reasons) == (
        ApproveOutcome.BLOCKED,
        ("This Draft can't be approved yet",),
    )


def test_an_unknown_draft_is_a_lookup_error(world: World) -> None:
    approval = approval_on(world)
    for draft_id in ("01JD3QZ8X5V9K2M7N4P6R8T0AE", "not-a-ulid"):
        with world.units.open(), pytest.raises(UnknownDraft):
            approval.approve(draft_id, 1, "0" * 64)


def test_a_failing_enqueue_rolls_everything_back(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    codec = FakeTokenCodec()
    codec.fail = AdapterError(False, "unsub_key_missing", "no key")

    result = refused(world, step_1, approval=approval_on(world, codec))

    assert result.outcome is ApproveOutcome.BLOCKED
    assert result.reasons == ("The message couldn't be queued (AdapterError)",)
    assert status_of(world, step_1) == "ready"
    assert sessions(world) == []


def test_a_domain_refusal_from_enqueue_shows_its_message(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _, [step_1] = passed(world)
    real = compliance_api.assert_contactable
    calls: list[str] = []

    def second_blocks(subject: object, channel: object, purpose: object) -> Allowed | Blocked:
        calls.append(str(purpose))
        if len(calls) == 2:  # enqueue's own call: the Contact was suppressed in between
            return Blocked((BlockReason.ADDRESS_SUPPRESSED,))
        return real(subject, channel, purpose)  # type: ignore[arg-type]

    monkeypatch.setattr(compliance_api, "assert_contactable", second_blocks)

    result = refused(world, step_1)

    assert result.reasons == ("the Contact may not be emailed: address_suppressed",)


def test_approve_needs_a_sending_and_typed_arguments(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    with world.units.open(), pytest.raises(TypeError, match="Sending"):
        Approval(world.clock).approve(step_1, 1, "x")
    with world.units.open(), pytest.raises(TypeError, match="rev"):
        approval_on(world).approve(step_1, True, "x")


# Metrics and sessions -----------------------------------------------------------------------------


def test_review_ms_and_edit_ratio_are_recorded(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    passed(world)  # a second card keeps the queue from clearing
    revise(world, step_1, body="Hi Zelda,\n\nA much shorter note.")
    set_grounding(world, step_1, "pass")
    served = world.clock.now() - timedelta(seconds=12, milliseconds=345)

    result = approve(world, step_1, served_at=served)

    assert result.queue_clear is False
    [(rev, review_ms, ratio)] = world.sql(
        "SELECT rev, review_ms, edit_ratio FROM approvals WHERE id = ?", (result.approval_id,)
    )
    assert (rev, review_ms) == (2, 12_345)
    first = world.read(lambda: drafting_queries.revision(step_1, 1))
    second = world.read(lambda: drafting_queries.revision(step_1, 2))
    assert first is not None and second is not None
    assert ratio == pytest.approx(render.edit_ratio(first, second))
    assert 0 < ratio < 1


def test_sessions_open_count_close_when_idle_and_when_the_queue_clears(world: World) -> None:
    ids = [passed(world)[3][0] for _ in range(4)]
    start = world.clock.now()
    first_served = start - timedelta(minutes=3)

    approve(world, ids[0], served_at=start - timedelta(seconds=10), first_served_at=first_served)
    assert sessions(world) == [(stamp(first_served), None, 1, 10_000)]

    world.clock.set(start + timedelta(minutes=5))
    approve(world, ids[1], served_at=start + timedelta(minutes=4), first_served_at=first_served)
    assert sessions(world) == [(stamp(first_served), None, 2, 70_000)]

    # 20 minutes without a decision: the open session closes at its last decision
    world.clock.set(start + timedelta(minutes=25))
    approve(world, ids[2], first_served_at=first_served)
    last = stamp(start + timedelta(minutes=5))
    second = stamp(start + timedelta(minutes=25))
    assert sessions(world) == [
        (stamp(first_served), last, 2, 70_000),
        (second, None, 1, 0),  # the first serve is before that end, no stamp: now; 0 ms
    ]

    world.clock.set(start + timedelta(minutes=26))
    result = approve(world, ids[3], served_at=start + timedelta(minutes=25, seconds=30))
    assert result.queue_clear is True
    assert sessions(world)[-1] == (second, stamp(start + timedelta(minutes=26)), 2, 30_000)


def test_a_session_opened_after_one_closed_starts_at_this_cards_serve(world: World) -> None:
    [first] = passed(world)[3]
    start = world.clock.now()
    first_served = start - timedelta(minutes=2)
    assert approve(world, first, first_served_at=first_served).queue_clear is True
    [second] = passed(world)[3]
    served = start + timedelta(hours=1)
    world.clock.set(served + timedelta(seconds=9))

    approve(world, second, served_at=served, first_served_at=first_served)

    assert sessions(world) == [
        (stamp(first_served), stamp(start), 1, 0),
        (stamp(served), stamp(served + timedelta(seconds=9)), 1, 9_000),  # not the gap
    ]


def test_a_serve_before_the_closed_sessions_end_starts_the_new_one_now(world: World) -> None:
    [first] = passed(world)[3]
    start = world.clock.now()
    assert approve(world, first).queue_clear is True
    [second] = passed(world)[3]
    world.clock.set(start + timedelta(minutes=1))

    approve(world, second, served_at=start - timedelta(seconds=5))

    assert sessions(world)[-1][:2] == (
        stamp(start + timedelta(minutes=1)),
        stamp(start + timedelta(minutes=1)),
    )


def test_active_ms_takes_at_most_the_idle_setting_per_decision(world: World) -> None:
    [step_1] = passed(world)[3]
    served = world.clock.now() - timedelta(minutes=40)

    result = approve(world, step_1, served_at=served)

    [(review_ms,)] = world.sql(
        "SELECT review_ms FROM approvals WHERE id = ?", (result.approval_id,)
    )
    assert review_ms == 40 * 60_000  # the raw review time
    assert [row[3] for row in sessions(world)] == [15 * 60_000]  # PT15M, the default idle


def test_a_queue_clear_after_the_clock_moved_back_closes_at_the_start(world: World) -> None:
    ids = [passed(world)[3][0] for _ in range(2)]
    start = world.clock.now()
    assert approve(world, ids[0]).queue_clear is False
    world.clock.set(start - timedelta(minutes=1))

    assert approve(world, ids[1]).queue_clear is True

    assert sessions(world) == [(stamp(start), stamp(start), 2, 0)]


def test_the_idle_setting_is_read(world: World) -> None:
    ids = [passed(world)[3][0] for _ in range(3)]
    with world.units.open():
        Setup(world.clock).save({"approval_session_idle": "PT1H"})
    start = world.clock.now()
    approve(world, ids[0])
    world.clock.set(start + timedelta(minutes=30))

    approve(world, ids[1])

    assert [row[1] for row in sessions(world)] == [None]  # 30 minutes is within PT1H


# The cascade --------------------------------------------------------------------------------------


def test_a_stop_cancels_the_queued_om_then_the_approved_draft(world: World) -> None:
    _, _, enrollment_id, [step_1] = passed(world)
    result = approve(world, step_1)

    world.stop(enrollment_id)

    assert status_of(world, step_1) == "cancelled"
    assert oms_of(world, step_1) == [(result.om_id, "cancelled", result.approval_id)]
    assert world.events("outbound_messages", str(result.om_id))[-1][:5] == (
        "queued",
        "cancelled",
        "unapproved",
        result.approval_id,
        "system:sending",
    )
    assert world.events("drafts", step_1)[-1][:5] == (
        "approved",
        "cancelled",
        "sequence_stopped",
        enrollment_id,
        "system:approval",
    )
    assert world.read(lambda: approval_queries.approval_of(step_1)) is None
    nfr_8(world)


def test_a_suppression_cancels_an_approved_drafts_om(world: World) -> None:
    _, contact_id, _, [step_1] = passed(world)
    result = approve(world, step_1)
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))

    with world.units.open():
        Compliance(world.clock).add_value(str(email))

    assert status_of(world, step_1) == "cancelled"
    assert oms_of(world, step_1)[0][1] == "cancelled"
    assert result.om_id


@pytest.mark.parametrize("om_status", ["sending", "unknown"])
def test_a_stop_leaves_an_om_in_flight_and_its_draft_approved(
    world: World, om_status: str, caplog: pytest.LogCaptureFixture
) -> None:
    _, _, enrollment_id, [step_1] = passed(world)
    result = approve(world, step_1)
    world.sql(
        "UPDATE outbound_messages SET status = ? WHERE id = ?", (om_status, result.om_id)
    )
    caplog.set_level(logging.WARNING, logger=approval_api.__name__)

    world.stop(enrollment_id)

    assert status_of(world, step_1) == "approved"
    assert oms_of(world, step_1)[0][1] == om_status
    [record] = [r for r in caplog.records if r.getMessage() == "approval_cascade_in_flight"]
    assert (getattr(record, "om_id"), getattr(record, "draft_id")) == (result.om_id, step_1)


def test_a_stop_skips_a_sent_om_silently(world: World, caplog: pytest.LogCaptureFixture) -> None:
    _, _, enrollment_id, [step_1] = passed(world)
    result = approve(world, step_1)
    world.sql("UPDATE outbound_messages SET status = 'sent' WHERE id = ?", (result.om_id,))
    caplog.set_level(logging.WARNING, logger=approval_api.__name__)

    world.stop(enrollment_id)

    assert status_of(world, step_1) == "approved"
    assert oms_of(world, step_1)[0][1] == "sent"
    assert not [r for r in caplog.records if r.getMessage() == "approval_cascade_in_flight"]


def test_the_suppression_backstop_cancels_an_approved_draft_of_an_ended_enrollment(
    world: World,
) -> None:
    _, _, enrollment_id, [step_1] = passed(world)
    result = approve(world, step_1)
    world.sql(
        "UPDATE enrollments SET status = 'completed', ended_at = ? WHERE id = ?",
        (stamp(NOW), enrollment_id),
    )

    with world.units.open():
        Compliance(world.clock).add_value("someone-else@other.com")

    assert status_of(world, step_1) == "cancelled"
    assert oms_of(world, step_1) == [(result.om_id, "cancelled", result.approval_id)]


# The tables ---------------------------------------------------------------------------------------


def raw(world: World, statement: str) -> None:
    connection = sqlite3.connect(world.db_file, isolation_level=None)
    try:
        connection.execute(statement)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE approvals SET review_ms = 1",
        "DELETE FROM approvals",
        "INSERT OR REPLACE INTO approvals SELECT * FROM approvals",
    ],
)
def test_approvals_are_append_only(world: World, statement: str) -> None:
    _, _, _, [step_1] = passed(world)
    approve(world, step_1)

    with pytest.raises(sqlite3.IntegrityError, match="never changed or removed"):
        raw(world, statement)


def test_at_most_one_session_is_open(world: World) -> None:
    raw(
        world,
        "INSERT INTO approval_sessions VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0AA', "
        "'2026-11-18T09:00:00.000000Z', NULL, 0, 0)",
    )

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        raw(
            world,
            "INSERT INTO approval_sessions VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0AB', "
            "'2026-11-18T09:00:00.000000Z', NULL, 0, 0)",
        )
    raw(
        world,
        "INSERT INTO approval_sessions VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0AC', "
        "'2026-11-18T09:00:00.000000Z', '2026-11-18T09:30:00.000000Z', 0, 0)",
    )


def test_approval_of_is_none_unless_approved(world: World) -> None:
    _, _, _, [step_1] = passed(world)

    assert world.read(lambda: approval_queries.approval_of(step_1)) is None
    assert world.read(lambda: approval_queries.approval_of("not-a-ulid")) is None


# The page -----------------------------------------------------------------------------------------


def post_approve(
    dashboard: Dashboard,
    cookie: bytes,
    fields: dict[str, str],
    *,
    query: bytes = b"",
    token: str | None = None,
) -> Reply:
    return dashboard.post(
        "/queue/approve",
        cookie=cookie,
        token=token if token is not None else dashboard.csrf_token(cookie),
        headers=[(b"content-type", FORM)],
        body=form(**fields),
        query=query,
    )


def fields_of(page: Tree) -> dict[str, str]:
    return {name: hidden(page, name) for name in ("draft_id", "rev", "shown_hash")}


def test_the_page_has_the_approve_form_outside_the_article_and_loads_the_keymap(
    world: World,
) -> None:
    _, _, _, [step_1] = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()

    page = queue_page_of(dashboard, cookie)

    [approve_form] = page.find_all("form", class_="queue-approve-form")
    assert approve_form.attrs["method"] == "post"
    assert approve_form.attrs["hx-post"] == "/queue/approve?card=1"
    assert not any("hx-disable" in node.attrs for node in approve_form.ancestors())
    assert "hx-disable" in page.find("article", class_="queue-card").attrs
    assert fields_of(page) == {
        "draft_id": step_1,
        "rev": "1",
        "shown_hash": card_of(world, step_1).shown_hash,
    }
    [button] = approve_form.find_all("button", class_="queue-approve")
    assert button.attrs["type"] == "submit" and "disabled" not in button.attrs
    assert [node.attrs.get("src") for node in page.find_all("script")] == [
        "/static/htmx-2.0.11.min.js",
        "/static/queue-keys.js",
    ]
    assert all("defer" in node.attrs for node in page.find_all("script"))


def test_a_post_approves_and_focuses_the_next_card(world: World) -> None:
    _, _, _, [first] = passed(world)
    _, _, _, [second] = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    page = queue_page_of(dashboard, cookie)
    shown = fields_of(page)
    served = dashboard.clock.now()  # the GET's serve stamp
    dashboard.clock.set(served + timedelta(seconds=7))

    reply = post_approve(dashboard, cookie, shown, query=b"card=1")

    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    after = Tree(reply.text)
    approved_id = shown["draft_id"]
    other = second if approved_id == first else first
    assert after.find("article", class_="queue-card").attrs["data-draft"] == other
    assert after.find("span", class_="queue-n-of-n").text == "1 of 1"
    assert not after.find_all("div", class_="queue-notices")
    assert status_of(world, approved_id) == "approved"
    [(review_ms,)] = world.sql("SELECT review_ms FROM approvals")
    assert review_ms == 7_000  # from the GET's serve stamp
    assert sessions(world) == [(stamp(served), None, 1, 7_000)]  # started at the GET
    nfr_8(world)

    # the last card: Queue clear, and the session closed
    final = post_approve(dashboard, cookie, fields_of(after), query=b"card=1")
    assert Tree(final.text).find("p", class_="queue-clear").text == "Queue clear"
    assert [row[1] is not None for row in sessions(world)] == [True]


def test_a_stale_post_rerenders_the_same_card_with_the_notice(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    shown = fields_of(queue_page_of(dashboard, cookie))
    revise(world, step_1)
    set_grounding(world, step_1, "pass")
    before = counts(world)

    reply = post_approve(dashboard, cookie, shown)

    assert reply.status == 200
    page = Tree(reply.text)
    assert [node.text for node in page.find_all("p", class_="queue-notice")] == [
        "This Draft changed since you opened it"
    ]
    assert fields_of(page)["rev"] == "2"
    assert fields_of(page)["shown_hash"] == card_of(world, step_1).shown_hash
    assert counts(world) == before


def test_a_blocked_post_shows_its_reasons(world: World) -> None:
    _, contact_id, _, [step_1] = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    shown = fields_of(queue_page_of(dashboard, cookie))
    suppress(world, contact_id)

    reply = post_approve(dashboard, cookie, shown)

    page = Tree(reply.text)
    # the suppressed Contact's card is held back, no longer due: the notices name the Draft
    assert [node.text for node in page.find_all("p", class_="queue-notice")] == [
        "The Draft you approved: Contact is suppressed",
        "address_suppressed",
    ]
    assert status_of(world, step_1) == "ready"


@pytest.mark.parametrize("error", [LookupError("unknown Contact"), ValueError("merged Lead")])
def test_a_post_whose_gate_cannot_look_up_the_contact_shows_the_block(
    world: World, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _, _, _, [step_1] = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    shown = fields_of(queue_page_of(dashboard, cookie))
    before = counts(world)
    gate_raises_once(monkeypatch, error)

    reply = post_approve(dashboard, cookie, shown)

    assert reply.status == 200
    assert [node.text for node in Tree(reply.text).find_all("p", class_="queue-notice")] == [
        "This Draft can't be approved yet"
    ]
    assert counts(world) == before
    assert status_of(world, step_1) == "ready"


def test_a_refusal_for_a_draft_no_longer_due_names_it_not_the_card_shown(
    world: World,
) -> None:
    passed(world)
    passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    shown = fields_of(queue_page_of(dashboard, cookie))
    after = Tree(post_approve(dashboard, cookie, shown, query=b"card=1").text)
    other = after.find("article", class_="queue-card").attrs["data-draft"]
    before = counts(world)

    reply = post_approve(dashboard, cookie, shown, query=b"card=1")  # the same Draft again

    assert reply.status == 200
    page = Tree(reply.text)
    [notices] = page.find_all("div", class_="queue-notices")
    assert [node.text for node in notices.find_all("p", class_="queue-notice")] == [
        "The Draft you approved: Already approved"
    ]
    assert not any(
        node.tag in ("article", "form") or "queue-approve" in (node.attrs.get("class") or "")
        for node in notices.ancestors()
    )
    assert page.find("article", class_="queue-card").attrs["data-draft"] == other
    assert fields_of(page)["draft_id"] == other
    assert not page.find_all("div", class_="queue-approve-blocked")
    assert counts(world) == before


def test_bad_requests_change_nothing(world: World) -> None:
    _, _, _, [step_1] = passed(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    shown = fields_of(queue_page_of(dashboard, cookie))
    before = counts(world)

    assert dashboard.get("/queue/approve", cookie=cookie).status == 405
    assert post_approve(dashboard, cookie, shown, token="wrong").status == 403
    unknown = post_approve(dashboard, cookie, {**shown, "draft_id": "01JD3QZ8X5V9K2M7N4P6R8T0AE"})
    assert unknown.status == 404
    for broken in (
        {**shown, "rev": "one"},
        {"draft_id": step_1, "rev": "1"},
        {**shown, "extra": "x"},
    ):
        assert post_approve(dashboard, cookie, broken).status == 400

    assert counts(world) == before
    assert status_of(world, step_1) == "ready"


def test_the_keymap_is_served_and_has_one_table() -> None:
    dashboard = Dashboard()
    cookie = dashboard.open_session()

    reply = dashboard.get("/static/queue-keys.js", cookie=cookie)

    assert reply.status == 200
    assert reply.header(b"content-type") == b"text/javascript; charset=utf-8"
    source = (PACKAGE / "web" / "static" / "queue-keys.js").read_text("utf-8")
    assert source.count("var KEYMAP") == 1 and 'a: "approve"' in source
    assert "eval(" not in source and "innerHTML" not in source


def test_nothing_under_approval_calls_the_mail_port() -> None:
    for path in sorted((PACKAGE / "app" / "approval").rglob("*.py")):
        source = path.read_text("utf-8")
        assert "MailPort" not in source.replace("``MailPort``", ""), path
        assert "ports.mail" not in source, path


# Domain -------------------------------------------------------------------------------------------


class _Text:
    def __init__(self, subject: str, body: str) -> None:
        self.subject = subject
        self.body = body


def _dp(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        row = [i]
        for j, char_b in enumerate(b, start=1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = row
    return previous[-1]


def test_levenshtein_matches_the_textbook_distance() -> None:
    chance = random.Random(54)
    for _ in range(2000):
        a = "".join(chance.choice("ab\n é") for _ in range(chance.randint(0, 14)))
        b = "".join(chance.choice("ab\n é") for _ in range(chance.randint(0, 14)))
        assert render.levenshtein(a, b) == _dp(a, b), (a, b)
    assert render.levenshtein("kitten", "sitting") == 3


def test_edit_ratio() -> None:
    first = _Text("Subject", "Body text here")
    assert render.edit_ratio(first, first) == 0.0
    assert render.edit_ratio(_Text("", ""), _Text("", "")) == 0.0
    assert render.edit_ratio(first, _Text("Subject", "Body text HERE")) == pytest.approx(4 / 22)
    assert render.edit_ratio(_Text("a", ""), _Text("a" * 50, "b" * 50)) == 1.0  # capped


def test_edit_ratio_is_fast_for_3000_character_texts() -> None:
    chance = random.Random(3000)
    a = _Text("Subject", "".join(chance.choice("abcdefgh \n") for _ in range(3000)))
    b = _Text("Other", "".join(chance.choice("abcdefgh \n") for _ in range(3000)))
    best = min(_timed(a, b) for _ in range(3))

    assert best < 0.5  # an O(n*m) Python loop takes seconds; loose enough for a busy machine


def _timed(a: _Text, b: _Text) -> float:
    started = time.perf_counter()
    render.edit_ratio(a, b)
    return time.perf_counter() - started


@pytest.mark.parametrize(
    ("kind", "step_no", "purpose"),
    [
        (DraftKind.STEP, 1, Purpose.COLD),
        (DraftKind.STEP, 2, Purpose.FOLLOW_UP),
        (DraftKind.STEP, 4, Purpose.FOLLOW_UP),
        (DraftKind.RECONTACT, 1, Purpose.RECONTACT),
        (DraftKind.REPLY, None, Purpose.REPLY),
        (DraftKind.REBOOK, None, Purpose.REBOOK),
        (DraftKind.CHANNEL, None, None),
        ("nonsense", None, None),
    ],
)
def test_contact_purpose(kind: str, step_no: int | None, purpose: Purpose | None) -> None:
    assert contact_purpose(kind, step_no) == purpose
