"""The Approval Queue's due queue and its page (FR-26, AD-13, AD-16; Story 5.1), on the world of
``tests.app.test_draft_revisions`` (a temporary database at head, ``FrozenClock`` at
2026-11-18 10:00 UTC, the shipped seeds, Law-PrivateAI x US active, the footer settings saved):

- **Candidates.** ``ready`` Drafts with a revision; cold ones due within 24 h (inclusive) on an
  ``active`` Enrollment; responsive ones whatever their due time.
- **Groups.** Replies, Tier A Step 1 (newest live Event, then Lead Score), other Step 1 and
  recontact, follow-ups (Lead Score), with the deterministic tie-breaks.
- **Held back.** Live Eligibility per Contact (called once each); ineligible, unknown and
  unlinked Contacts are held back and not counted.
- **Empty state.** The next due time of any ``ready`` Draft, or none.
- **Read-only.** A build writes nothing.
- **The page.** Group headers with counts, one card ("k of N"), clamping, ``no-store``,
  ``hx-disable`` and autoescaping, the UK privacy line, Held back, the empty state and the
  session's elapsed time.
"""

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nextron.app.compliance.api import Compliance
from nextron.app.queries import approval as approval_queries
from nextron.app.queries.approval import (
    GROUP_FOLLOW_UPS,
    GROUP_ORDER,
    GROUP_OTHER_STEP_1,
    GROUP_REPLIES,
    GROUP_TIER_A,
    DueQueue,
    QueueCard,
    QueueGroup,
)
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.domain.contactability import KeyKind, key_hash
from nextron.domain.draft import DraftKind, RevisionOrigin
from nextron.domain.eligibility import Decision, Reason, ReasonKind
from nextron.web import queue_page
from tests.app.test_draft_revisions import TRACE, append, ids_of, move_to_uk, world
from tests.app.test_draft_slots import enrolled
from tests.app.test_enroll import World
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Tree

__all__ = ["world"]  # the fixture

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
ELIGIBLE = Decision(True, "us_b2b", ())


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Recorder:
    """An ``eligibility`` callable: eligible unless told otherwise; records each call."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.answers: dict[str, Decision | Exception] = {}

    def __call__(self, contact_id: str) -> Decision:
        self.calls.append(contact_id)
        answer = self.answers.get(contact_id, ELIGIBLE)
        if isinstance(answer, Exception):
            raise answer
        return answer


def build(world: World, eligibility: Callable[[str], Decision] | None = None) -> DueQueue:
    check = eligibility if eligibility is not None else Recorder()
    return world.read(lambda: approval_queries.due_queue(world.clock.now(), check))


def slot(
    world: World, steps: tuple[int, ...] = (1,), *, text: bool = True
) -> tuple[str, str, str, list[str]]:
    """A new enrolled Lead and Contact, each Draft with revision 1 unless ``text`` is false:
    (lead, contact, enrollment, Draft ids by Step)."""
    lead_id, contact_id, enrollment_id = enrolled(world, steps=steps)
    ids = ids_of(world, enrollment_id)
    if text:
        for draft_id in ids:
            append(world, draft_id, RevisionOrigin.GENERATED, TRACE)
    return lead_id, contact_id, enrollment_id, ids


def set_due(world: World, draft_id: str, at: datetime | None) -> None:
    world.sql(
        "UPDATE drafts SET due_at = ? WHERE id = ?",
        (None if at is None else stamp(at), draft_id),
    )


def set_score(world: World, lead_id: str, tier: str, score: int) -> None:
    world.sql(
        "UPDATE leads SET fit_score = ?, signal_score = 0, lead_score = ?, tier = ?, "
        "tier_computed_at = ?, score_segment_version = 1 WHERE id = ?",
        (score, score, tier, stamp(NOW), lead_id),
    )


def add_event(world: World, lead_id: str, age: int, tier: int = 1) -> None:
    event_date = (NOW - timedelta(days=age)).date().isoformat()
    world.sql(
        "INSERT INTO signals (id, lead_id, signal_type, signal_class, tier, score_only, "
        "event_date, first_seen_at, retracted_at) VALUES (?, ?, 'funding', 'event', ?, 0, ?, ?, "
        "NULL)",
        (new_id(), lead_id, tier, event_date, stamp(NOW - timedelta(days=age))),
    )


def copy_revision(world: World, source: str, target: str) -> None:
    """Give ``target`` revision 1, a copy of ``source``'s, and point it there."""
    columns = [
        str(row[1])
        for row in world.sql("PRAGMA table_info(draft_revisions)")
        if row[1] not in ("id", "draft_id")
    ]
    listed = ", ".join(columns)
    world.sql(
        f"INSERT INTO draft_revisions (id, draft_id, {listed}) SELECT ?, ?, {listed} "
        "FROM draft_revisions WHERE draft_id = ? AND rev = 1",
        (new_id(), target, source),
    )
    world.sql("UPDATE drafts SET current_rev = 1 WHERE id = ?", (target,))


def responsive(
    world: World,
    enrollment_id: str | None,
    source: str,
    *,
    kind: str = "reply",
    created: datetime = NOW,
) -> str:
    """A hand-inserted responsive Draft with a copy of ``source``'s text."""
    draft_id = new_id()
    world.sql(
        "INSERT INTO drafts (id, kind, enrollment_id, step_no, day_offset, current_rev, due_at, "
        "generic, stale, created_at, updated_at, status, reapproval_required, event_expired) "
        "VALUES (?, ?, ?, NULL, NULL, NULL, NULL, 0, 0, ?, ?, 'ready', 0, 0)",
        (draft_id, kind, enrollment_id, stamp(created), stamp(created)),
    )
    copy_revision(world, source, draft_id)
    return draft_id


def suppress(world: World, contact_id: str) -> None:
    """An ``active`` address entry for the Contact, written raw: the cascade would cancel its
    Drafts, and the queue must hold back a due Draft whose Contact is suppressed now."""
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    world.sql(
        "INSERT INTO suppression_entries (id, kind, value_hash, status, source, created_at) "
        "VALUES (?, 'address', ?, 'active', 'operator', ?)",
        (new_id(), key_hash(KeyKind.ADDRESS, str(email)), stamp(NOW)),
    )


def card_ids(queue: DueQueue) -> dict[str, list[str]]:
    return {group.key: [card.draft_id for card in group.cards] for group in queue.groups}


def dump(world: World) -> str:
    connection = sqlite3.connect(world.db_file)
    try:
        return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
    finally:
        connection.close()


# Groups and order ---------------------------------------------------------------------------------


def test_the_groups_come_in_queue_order(world: World) -> None:
    a_lead, _, _, [a_step] = slot(world)
    set_score(world, a_lead, "A", 50)
    b_lead, _, _, [b_step] = slot(world)
    set_score(world, b_lead, "B", 90)
    _, _, _, [recontact] = slot(world)
    world.sql("UPDATE drafts SET kind = 'recontact' WHERE id = ?", (recontact,))
    _, _, follow_enrollment, [first, second] = slot(world, (1, 2))
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (first,))
    set_due(world, second, NOW)
    reply = responsive(world, follow_enrollment, a_step)

    queue = build(world)

    assert [group.key for group in queue.groups] == list(GROUP_ORDER)
    assert [group.label for group in queue.groups] == [
        "Replies",
        "Tier A Step 1",
        "Other Step 1 and recontact",
        "Follow-ups",
    ]
    found = card_ids(queue)
    assert found[GROUP_REPLIES] == [reply]
    assert found[GROUP_TIER_A] == [a_step]
    assert found[GROUP_OTHER_STEP_1] == [b_step, recontact]  # score 90, then no score
    assert found[GROUP_FOLLOW_UPS] == [second]
    assert queue.total == 5
    assert [card.draft_id for card in queue.cards] == [reply, a_step, b_step, recontact, second]
    assert queue.held_back == ()


def test_a_tier_a_recontact_is_in_the_other_step_1_group(world: World) -> None:
    lead_id, _, _, [draft_id] = slot(world)
    set_score(world, lead_id, "A", 99)
    world.sql("UPDATE drafts SET kind = 'recontact' WHERE id = ?", (draft_id,))

    assert card_ids(build(world))[GROUP_OTHER_STEP_1] == [draft_id]


def test_tier_a_goes_newest_live_event_first_then_by_score(world: World) -> None:
    leads: dict[str, tuple[str, str]] = {}
    for name, score, age in (
        ("old_event", 99, 20),
        ("low_score", 60, 3),
        ("high_score", 80, 3),
        ("no_event", 100, None),
        ("expired_event", 100, 200),  # a Tier 1 Event this old isn't live
    ):
        lead_id, _, _, [draft_id] = slot(world)
        set_score(world, lead_id, "A", score)
        if age is not None:
            add_event(world, lead_id, age)
        leads[name] = (lead_id, draft_id)

    found = card_ids(build(world))[GROUP_TIER_A]

    by_name = {draft: name for name, (_, draft) in leads.items()}
    # no live Event sorts after; among those, by score (both 100) then due_at, then id
    tail = sorted((leads["no_event"][1], leads["expired_event"][1]))
    assert [by_name[draft] for draft in found[:3]] == ["high_score", "low_score", "old_event"]
    assert found[3:] == tail


def test_follow_ups_go_by_score_with_a_missing_score_last(world: World) -> None:
    drafts = []
    for score in (40, None, 75):
        lead_id, _, _, [first, second] = slot(world, (1, 2))
        world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (first,))
        set_due(world, second, NOW + timedelta(hours=1))
        if score is not None:
            set_score(world, lead_id, "B", score)
        drafts.append(second)

    assert card_ids(build(world))[GROUP_FOLLOW_UPS] == [drafts[2], drafts[0], drafts[1]]


def test_replies_go_oldest_first(world: World) -> None:
    _, _, enrollment_id, [source] = slot(world)
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (source,))
    newer = responsive(world, enrollment_id, source, created=NOW - timedelta(minutes=1))
    older = responsive(world, enrollment_id, source, kind="rebook", created=NOW - timedelta(hours=2))
    channel = responsive(world, enrollment_id, source, kind="channel", created=NOW)

    assert card_ids(build(world))[GROUP_REPLIES] == [older, newer, channel]


# Candidates ---------------------------------------------------------------------------------------


def test_the_24_hour_boundary_is_inclusive(world: World) -> None:
    _, _, _, [inside] = slot(world)
    _, _, _, [outside] = slot(world)
    set_due(world, inside, NOW + timedelta(hours=24))
    set_due(world, outside, NOW + timedelta(hours=24, seconds=1))

    queue = build(world)

    assert [card.draft_id for card in queue.cards] == [inside]


@pytest.mark.parametrize(
    "enrollment_update",
    [
        "status = 'paused', pause_cause = 'operator'",
        "status = 'stopped', stop_cause = 'operator_stop', ended_at = '2026-11-18T09:30:00.000000Z'",
        "status = 'completed', ended_at = '2026-11-18T09:30:00.000000Z'",
    ],
)
def test_an_enrollment_that_isnt_active_keeps_its_drafts_out(
    world: World, enrollment_update: str
) -> None:
    _, _, enrollment_id, _ = slot(world)
    world.sql(f"UPDATE enrollments SET {enrollment_update} WHERE id = ?", (enrollment_id,))

    queue = build(world)

    assert queue.total == 0 and queue.held_back == ()


@pytest.mark.parametrize(
    "enrollment_update",
    [
        "status = 'paused', pause_cause = 'operator'",
        "status = 'stopped', stop_cause = 'operator_stop', ended_at = '2026-11-18T09:30:00.000000Z'",
    ],
)
def test_a_reply_on_an_enrollment_that_isnt_active_is_still_a_reply(
    world: World, enrollment_update: str
) -> None:
    _, _, enrollment_id, [source] = slot(world)
    reply = responsive(world, enrollment_id, source)
    world.sql(f"UPDATE enrollments SET {enrollment_update} WHERE id = ?", (enrollment_id,))

    queue = build(world)

    assert card_ids(queue) == {
        GROUP_REPLIES: [reply],
        GROUP_TIER_A: [],
        GROUP_OTHER_STEP_1: [],
        GROUP_FOLLOW_UPS: [],
    }
    assert queue.held_back == ()


def test_excluded_drafts_are_neither_shown_nor_held_back(world: World) -> None:
    _, _, _, [first, no_due] = slot(world, (1, 2))  # Step 2 has no due_at yet
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (first,))
    _, _, _, [no_text] = slot(world, text=False)
    _, _, _, [rejected] = slot(world)
    world.sql("UPDATE drafts SET status = 'rejected' WHERE id = ?", (rejected,))
    recorder = Recorder()

    queue = build(world, recorder)

    assert queue.total == 0 and queue.held_back == ()
    assert recorder.calls == []
    assert world.sql("SELECT status, due_at FROM drafts WHERE id = ?", (no_due,)) == [
        ("ready", None)
    ]
    assert world.sql("SELECT status, current_rev FROM drafts WHERE id = ?", (no_text,)) == [
        ("ready", None)
    ]


# Held back ----------------------------------------------------------------------------------------


def test_an_ineligible_contact_is_held_back_with_its_reasons(world: World) -> None:
    lead_id, held_contact, _, [held] = slot(world)
    _, _, _, [shown] = slot(world)
    reason = Reason(ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED, "The address is suppressed")
    recorder = Recorder()
    recorder.answers[held_contact] = Decision(False, None, (reason,))

    queue = build(world, recorder)

    assert [card.draft_id for card in queue.cards] == [shown]
    assert queue.total == 1
    [found] = queue.held_back
    assert found.card.draft_id == held
    assert found.card.lead_id == lead_id
    assert [(each.code, each.text) for each in found.reasons] == [
        ("address_or_domain_suppressed", "The address is suppressed")
    ]


def test_an_unknown_contact_and_an_unlinked_reply_are_held_back(world: World) -> None:
    _, contact_id, _, [unknown] = slot(world)
    unlinked = responsive(world, None, unknown)
    recorder = Recorder()
    recorder.answers[contact_id] = LookupError("gone")

    queue = build(world, recorder)

    assert queue.total == 0
    reasons = {held.card.draft_id: [r.code for r in held.reasons] for held in queue.held_back}
    assert reasons == {unknown: ["contact_unknown"], unlinked: ["no_contact_link"]}


def test_an_ineligible_decision_without_reasons_reads_not_eligible(world: World) -> None:
    _, contact_id, _, [draft_id] = slot(world)
    recorder = Recorder()
    # a real Decision can't be ineligible without a reason; the query still fails closed
    stand_in = SimpleNamespace(eligible=False, reasons=())
    recorder.answers[contact_id] = stand_in  # type: ignore[assignment]

    queue = build(world, recorder)

    assert queue.total == 0
    [held] = queue.held_back
    assert held.card.draft_id == draft_id
    assert [(r.code, r.text) for r in held.reasons] == [("not_eligible", "Not eligible")]


def test_a_contact_whose_eligibility_raises_value_error_is_held_back(world: World) -> None:
    _, contact_id, _, [held_draft] = slot(world)
    _, _, _, [shown] = slot(world)
    recorder = Recorder()
    recorder.answers[contact_id] = ValueError("merged")

    queue = build(world, recorder)

    assert [card.draft_id for card in queue.cards] == [shown]
    [held] = queue.held_back
    assert held.card.draft_id == held_draft
    assert [(r.code, r.text) for r in held.reasons] == [
        ("not_checkable", "Eligibility couldn't be checked")
    ]


def test_a_merged_lead_is_held_back_by_the_real_gate_and_the_page_still_renders(
    world: World,
) -> None:
    survivor, _, _, [shown] = slot(world)
    merged, _, _, [held_draft] = slot(world)
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, merged))

    queue = build(world, Compliance(world.clock).evaluate_eligibility)

    assert [card.draft_id for card in queue.cards] == [shown]
    [held] = queue.held_back
    assert held.card.draft_id == held_draft
    assert [reason.code for reason in held.reasons] == ["not_checkable"]
    dashboard = dashboard_on(world)
    page = queue_page_of(dashboard, dashboard.open_session())
    reasons = [node.text for node in page.find_all("span", class_="held-back-reason")]
    assert reasons == ["Eligibility couldn't be checked"]


def test_a_suppressed_contact_is_held_back_by_the_real_gate(world: World) -> None:
    _, contact_id, _, [draft_id] = slot(world)
    suppress(world, contact_id)

    compliance = Compliance(world.clock)
    queue = build(world, compliance.evaluate_eligibility)

    assert queue.total == 0
    [held] = queue.held_back
    assert held.card.draft_id == draft_id
    assert "address_or_domain_suppressed" in [reason.code for reason in held.reasons]


def test_eligibility_is_asked_once_per_contact(world: World) -> None:
    _, contact_id, enrollment_id, [first, second] = slot(world, (1, 2))
    set_due(world, second, NOW + timedelta(hours=2))
    responsive(world, enrollment_id, first)
    recorder = Recorder()

    queue = build(world, recorder)

    assert queue.total == 3
    assert recorder.calls == [contact_id]


# Cards --------------------------------------------------------------------------------------------


def test_a_card_holds_the_lead_contact_and_exact_message(world: World) -> None:
    lead_id, contact_id, _, [draft_id] = slot(world)
    set_score(world, lead_id, "A", 88)
    [(email, name)] = world.sql("SELECT email, name FROM contacts WHERE id = ?", (contact_id,))
    [(snapshot_id, subject, body)] = world.sql(
        "SELECT footer_snapshot_id, subject, body FROM draft_revisions WHERE draft_id = ?",
        (draft_id,),
    )
    [(footer,)] = world.sql("SELECT text FROM footer_snapshots WHERE id = ?", (snapshot_id,))

    [card] = build(world).cards

    [(lead_name,)] = world.sql("SELECT name FROM leads WHERE id = ?", (lead_id,))
    assert card.lead_name == lead_name
    assert card.segment_name is not None and card.segment_name.strip()
    assert card.market_code == "us"
    assert (card.tier, card.lead_score) == ("A", 88)
    assert (card.contact_name, card.address_class) == (name, "published")
    assert card.message is not None
    assert card.message.to_address == email
    assert card.message.subject == subject
    assert card.message.body == body + "\n\n" + footer
    assert card.blocked is None


def test_a_uk_card_ends_with_the_footer_and_its_privacy_line(world: World) -> None:
    _, contact_id, _, [draft_id] = slot(world, text=False)
    move_to_uk(world, contact_id)
    append(world, draft_id, RevisionOrigin.GENERATED, TRACE)

    [card] = build(world).cards

    assert card.market_code == "uk"
    assert card.message is not None
    assert "Your details came from your firm's website." in card.message.body
    [(footer,)] = world.sql(
        "SELECT f.text FROM footer_snapshots f JOIN draft_revisions r "
        "ON r.footer_snapshot_id = f.id WHERE r.draft_id = ?",
        (draft_id,),
    )
    assert card.message.body.endswith("\n\n" + footer)


def test_a_card_says_when_the_footer_blocks_sending(world: World) -> None:
    slot(world)
    world.sql("DELETE FROM settings WHERE key = 'postal_address'")

    [card] = build(world).cards

    assert card.blocked == "Sending blocked: footer incomplete (Postal address)"


# Empty state and read-only ------------------------------------------------------------------------


def test_an_empty_queue_names_the_next_due_time(world: World) -> None:
    _, _, _, [later] = slot(world)
    set_due(world, later, NOW + timedelta(days=3))

    queue = build(world)

    assert queue.total == 0
    assert queue.next_due_at == NOW + timedelta(days=3)


def test_nothing_scheduled_without_a_due_ready_draft(world: World) -> None:
    _, _, _, [first] = slot(world, text=False)
    set_due(world, first, None)

    queue = build(world)

    assert queue.total == 0 and queue.next_due_at is None
    assert [len(group.cards) for group in queue.groups] == [0, 0, 0, 0]


def test_building_the_queue_writes_nothing(world: World) -> None:
    lead_id, _, enrollment_id, [first] = slot(world)
    set_score(world, lead_id, "A", 70)
    add_event(world, lead_id, 2)
    responsive(world, enrollment_id, first)
    responsive(world, None, first)
    before = dump(world)

    build(world, Compliance(world.clock).evaluate_eligibility)

    assert dump(world) == before


# The page -----------------------------------------------------------------------------------------


def dashboard_on(world: World) -> Dashboard:
    return Dashboard(units=world.units, clock=FrozenClock(world.clock.now()))


def queue_page_of(dashboard: Dashboard, cookie: bytes, query: bytes = b"") -> Tree:
    reply = dashboard.get("/queue", cookie=cookie, query=query)
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return Tree(reply.text)


def test_the_page_shows_headers_one_card_and_held_back(world: World) -> None:
    lead_id, _, _, [first] = slot(world)
    world.sql("UPDATE leads SET name = '<b>Bold</b> & Co' WHERE id = ?", (lead_id,))
    set_score(world, lead_id, "A", 90)
    _, _, _, [second] = slot(world)
    held_lead, held_contact, _, _ = slot(world)
    world.sql("UPDATE leads SET name = 'Held Firm' WHERE id = ?", (held_lead,))
    suppress(world, held_contact)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()

    page = queue_page_of(dashboard, cookie)

    headers = [node.text for node in page.find_all("li", class_="queue-group")]
    assert headers == [
        "Replies (0)",
        "Tier A Step 1 (1)",
        "Other Step 1 and recontact (1)",
        "Follow-ups (0)",
    ]
    assert page.find("span", class_="queue-n-of-n").text == "1 of 2"
    card = page.find("article", class_="queue-card")
    assert card.attrs["data-draft"] == first
    assert "hx-disable" in card.attrs
    assert card.find("h2").text == "<b>Bold</b> & Co"
    assert not card.find_all("b")
    assert all(page.disabled("<b>Bold</b>"))
    assert page.find("a", class_="queue-next").attrs["href"] == f"/queue?draft={second}"
    assert not page.find_all("a", class_="queue-previous")
    held = page.find("section", class_="queue-held-back")
    assert "hx-disable" in held.attrs
    [row] = held.find_all("li", class_="held-back-row")
    assert row.find("span", class_="held-back-name").text == "Held Firm"
    # Story 5.4: the Approve form, without its submit button here (Story 5.6 adds the editor's)
    [approve_form, edit_form] = page.find_all("form")
    assert "queue-approve-form" in str(approve_form.attrs["class"]).split()
    assert "queue-edit-form" in str(edit_form.attrs["class"]).split()
    assert not approve_form.find_all("button")
    # Story 5.3: no grounding result yet, so only a disabled Approve button renders
    [button] = [
        node
        for node in page.find_all("button")
        if "queue-approve" in str(node.attrs.get("class")).split()
    ]
    assert "disabled" in button.attrs and "queue-approve" in str(button.attrs["class"]).split()
    assert ("/queue", "Queue") in [
        (node.attrs.get("href"), node.text)
        for node in page.find("header", class_="site-header").find_all("a")
    ]
    second_page = queue_page_of(dashboard, cookie, b"card=2")
    assert second_page.find("article", class_="queue-card").attrs["data-draft"] == second


@pytest.mark.parametrize(
    ("query", "shown"), [(b"card=2", "2 of 2"), (b"card=99", "2 of 2"), (b"card=x", "1 of 2"),
                         (b"card=0", "1 of 2"), (b"card=-3", "1 of 2")]
)
def test_the_card_parameter_is_clamped(world: World, query: bytes, shown: str) -> None:
    slot(world)
    slot(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()

    page = queue_page_of(dashboard, cookie, query)

    assert page.find("span", class_="queue-n-of-n").text == shown


def test_the_last_card_links_back_only(world: World) -> None:
    _, _, _, [first] = slot(world)
    _, _, _, [second] = slot(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()

    # both are Other Step 1 without a score: by due_at, then the Draft id
    rows = world.sql("SELECT due_at, id FROM drafts WHERE id IN (?, ?)", (first, second))
    last = max(rows)[1]

    page = queue_page_of(dashboard, cookie, b"card=2")

    first_shown = min(rows)[1]
    assert page.find("a", class_="queue-previous").attrs["href"] == f"/queue?draft={first_shown}"
    assert not page.find_all("a", class_="queue-next")
    assert page.find("article", class_="queue-card").attrs["data-draft"] == last
    assert page.find("section", class_="queue-held-back").find("p").text == "Nothing held back."


def test_the_page_shows_a_uk_card_with_its_privacy_line(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, contact_id, _, [draft_id] = slot(world, text=False)
    move_to_uk(world, contact_id)
    append(world, draft_id, RevisionOrigin.GENERATED, TRACE)
    dashboard = dashboard_on(world)
    monkeypatch.setattr(dashboard.compliance, "evaluate_eligibility", lambda _: ELIGIBLE)
    cookie = dashboard.open_session()

    page = queue_page_of(dashboard, cookie)

    body = page.find("pre", class_="draft-body").text
    assert "Your details came from your firm's website." in body
    assert [node.text for node in page.find_all("dd")][2] == "UK"


def test_the_empty_page_says_queue_clear_and_the_next_due_time(world: World) -> None:
    _, _, _, [later] = slot(world)
    set_due(world, later, datetime(2026, 11, 21, 15, 30, tzinfo=UTC))
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/New_York"})
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()

    page = queue_page_of(dashboard, cookie)

    assert page.find("p", class_="queue-clear").text == "Queue clear"
    assert (
        page.find("p", class_="queue-next-due").text
        == "Next due: 2026-11-21 10:30 America/New_York"
    )
    assert not page.find_all("article", class_="queue-card")


def test_the_empty_page_says_nothing_scheduled(world: World) -> None:
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()

    page = queue_page_of(dashboard, cookie)

    assert page.find("p", class_="queue-next-due").text == "Nothing scheduled"


def test_the_page_shows_the_sessions_elapsed_time(world: World) -> None:
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    dashboard.clock.set(dashboard.clock.now() + timedelta(hours=1, minutes=5, seconds=59))

    page = queue_page_of(dashboard, cookie)

    assert page.find("p", class_="queue-elapsed").text == "Session time: 1h 05m"


def test_the_page_writes_nothing(world: World) -> None:
    slot(world)
    dashboard = dashboard_on(world)
    cookie = dashboard.open_session()
    before = dump(world)

    queue_page_of(dashboard, cookie)

    assert dump(world) == before


def test_the_page_shows_the_message_header_and_the_footer_block(world: World) -> None:
    _, contact_id, _, [draft_id] = slot(world)
    world.sql("DELETE FROM settings WHERE key = 'postal_address'")
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    [(subject,)] = world.sql("SELECT subject FROM draft_revisions WHERE draft_id = ?", (draft_id,))
    dashboard = dashboard_on(world)

    page = queue_page_of(dashboard, dashboard.open_session())

    assert (
        page.find("p", class_="draft-blocked").text
        == "Sending blocked: footer incomplete (Postal address)"
    )
    assert page.find("p", class_="draft-to").text == f"To: {email}"
    assert page.find("p", class_="draft-subject").text == f"Subject: {subject}"
    assert not page.find_all("p", class_="draft-no-text")


def test_the_page_lists_each_held_back_reason(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    held_lead, held_contact, _, _ = slot(world)
    world.sql("UPDATE leads SET name = 'Held Firm' WHERE id = ?", (held_lead,))
    held = Decision(
        False,
        None,
        (
            Reason(ReasonKind.ADDRESS_NOT_SENDABLE, "The address can't be sent to"),
            Reason(ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED, "<i>The domain</i> is suppressed"),
        ),
    )
    dashboard = dashboard_on(world)
    monkeypatch.setattr(
        dashboard.compliance,
        "evaluate_eligibility",
        lambda contact: held if contact == held_contact else ELIGIBLE,
    )

    page = queue_page_of(dashboard, dashboard.open_session())

    [row] = page.find("section", class_="queue-held-back").find_all("li", class_="held-back-row")
    assert row.find("span", class_="held-back-name").text == "Held Firm"
    assert [node.text for node in row.find_all("span", class_="held-back-reason")] == [
        "The address can't be sent to",
        "<i>The domain</i> is suppressed",
    ]
    assert not page.find_all("i")
    assert page.find("p", class_="queue-clear").text == "Queue clear"


def test_the_next_due_time_shows_in_utc_without_a_sender_timezone(world: World) -> None:
    _, _, _, [later] = slot(world)
    set_due(world, later, datetime(2026, 11, 21, 15, 30, tzinfo=UTC))
    world.sql("DELETE FROM settings WHERE key = 'sender_timezone'")
    dashboard = dashboard_on(world)

    page = queue_page_of(dashboard, dashboard.open_session())

    assert page.find("p", class_="queue-next-due").text == "Next due: 2026-11-21 15:30 UTC"


def test_a_card_without_a_message_says_no_text_yet(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # unreachable through the query (a reviewable Draft has a revision, and the revision's
    # footer snapshot is a required foreign key), so the page gets a queue with such a card
    card = QueueCard(
        draft_id=new_id(), kind=DraftKind.STEP, step_no=1, rev=None, due_at=NOW, created_at=NOW
    )
    queue = DueQueue(
        groups=(QueueGroup(GROUP_OTHER_STEP_1, "Other Step 1 and recontact", (card,)),),
        held_back=(),
        total=1,
        next_due_at=NOW,
    )
    monkeypatch.setattr(approval_queries, "due_queue", lambda now, eligibility: queue)
    dashboard = dashboard_on(world)

    page = queue_page_of(dashboard, dashboard.open_session())

    assert page.find("p", class_="draft-no-text").text == "No text yet"
    assert not page.find_all("p", class_="draft-to")
    assert page.find("article", class_="queue-card").find("h2").text == "—"


# The page's helpers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "total", "index"),
    [(None, 3, 1), ("2", 3, 2), ("9", 3, 3), ("0", 3, 1), ("x", 3, 1), ("2.5", 3, 1),
     ("5", 0, 1), ("9" * 5000, 3, 1)],
)
def test_card_index(raw: str | None, total: int, index: int) -> None:
    assert queue_page.card_index(raw, total) == index


@pytest.mark.parametrize(
    ("minutes", "text"), [(0, "0h 00m"), (59, "0h 59m"), (61, "1h 01m"), (25 * 60, "25h 00m"),
                          (-5, "0h 00m")]
)
def test_elapsed_text(minutes: int, text: str) -> None:
    assert queue_page.elapsed_text(NOW, NOW + timedelta(minutes=minutes)) == text


def test_local_text_falls_back_to_utc() -> None:
    assert queue_page.local_text(NOW, None) == "2026-11-18 10:00 UTC"
    assert queue_page.local_text(NOW, "Not/AZone") == "2026-11-18 10:00 UTC"
    assert queue_page.local_text(NOW, "America") == "2026-11-18 10:00 UTC"  # a tzdata folder
    assert queue_page.local_text(NOW, "Europe/London") == "2026-11-18 10:00 Europe/London"
