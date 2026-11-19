"""Each approved Draft held as exactly one queued OutboundMessage (FR-26, AD-4, AD-6, AD-16,
AD-17; Story 5.2), on the world of ``tests.app.test_draft_revisions`` (a temporary database at
head, ``FrozenClock``, the real subscriber registry, the shipped seeds, Law-PrivateAI x US active,
the footer settings saved) with each Draft's revision 1 written and the Draft moved to
``approved`` by plain SQL (Story 5.4's approve isn't built yet).

The spec's I/O matrix: the happy path (one ``queued`` OM, its copy, token and ``events`` row and
the AD-4 snapshot), every refusal (nothing written: rollback proven by the row counts of
``outbound_messages``, ``unsub_tokens`` and ``events``), the partial unique index, re-enqueue after
a cancel, ``cancel`` from every status, erasure and the table's triggers.
"""

import ast
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.queries import sending as sending_queries
from nextron.app.sending import api as sending_api
from nextron.app.sending.api import (
    AlreadyQueued,
    AlreadySending,
    ContactBlocked,
    HashMismatch,
    NotApproved,
    Sending,
    UnsupportedKind,
)
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.repos.sending import outbound_messages as outbound_repo
from nextron.domain import render
from nextron.domain.contactability import Allowed, Blocked, BlockReason
from nextron.domain.notifications import LeadsMerged
from nextron.domain.unsub_token import token_hash
from nextron.ports.errors import AdapterError
from tests.app.test_draft_revisions import FOOTER_SETTINGS, drafted, footer_text
from tests.app.test_enroll import World, build_world, queue_on
from tests.fakes.token_codec import FakeTokenCodec

PACKAGE = Path(__file__).resolve().parents[2] / "nextron"
COUNTED = ("outbound_messages", "unsub_tokens", "events")
STAMP = "2026-11-18T09:00:00.000000Z"
OM_COLUMNS = (
    "draft_id, approval_id, kind, status, to_address, inbox_id, contact_id, lead_id, "
    "enrollment_id, subject, body, approved_hash, unsub_token, tier, lead_score, variant_id, "
    "segment_id, market_id, step_no, created_at, cancelled_at, sent_at"
)


@dataclass(frozen=True, slots=True)
class StandInApproval:
    """What Story 5.4's ``approvals`` row will carry."""

    approval_id: str
    draft_id: str
    rev: int
    text_hash: str


@dataclass
class Held:
    """The world, a codec-bearing ``Sending`` and its fake codec."""

    world: World
    codec: FakeTokenCodec
    sending: Sending

    def enqueue(self, draft_id: str, approval: StandInApproval) -> str:
        return self.world.transact(lambda: self.sending.enqueue(draft_id, approval))

    def cancel(self, om_id: str) -> bool:
        return self.world.transact(lambda: self.sending.cancel(om_id))

    def counts(self) -> dict[str, int]:
        return {table: self.world.sql(f"SELECT COUNT(*) FROM {table}")[0][0] for table in COUNTED}

    def om(self, om_id: str) -> dict[str, Any]:
        names = [name.strip() for name in OM_COLUMNS.split(",")]
        [row] = self.world.sql(f"SELECT {OM_COLUMNS} FROM outbound_messages WHERE id = ?", (om_id,))
        return dict(zip(names, row, strict=True))

    def oms(self, draft_id: str) -> list[tuple[str, str]]:
        return [
            (str(row[0]), str(row[1]))
            for row in self.world.sql(
                "SELECT id, status FROM outbound_messages WHERE draft_id = ? ORDER BY rowid",
                (draft_id,),
            )
        ]

    def events(self, om_id: str) -> list[tuple[Any, ...]]:
        return self.world.sql(
            "SELECT from_status, to_status, cause, cause_ref, actor, at FROM events "
            "WHERE entity = 'outbound_messages' AND entity_id = ? ORDER BY rowid",
            (om_id,),
        )


@pytest.fixture
def held(db_engine: Engine) -> Held:
    world = build_world(db_engine)
    with world.units.open():
        Setup(world.clock).save(FOOTER_SETTINGS)
    codec = FakeTokenCodec()
    compliance = Compliance(world.clock, codec=codec)
    return Held(world, codec, Sending(world.clock, env="test", compliance=compliance))


def approve(world: World, draft_id: str) -> StandInApproval:
    """Move the Draft to ``approved`` (plain SQL) and return an approval of its revision 1."""
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft_id,))
    [(rev, digest)] = world.sql(
        "SELECT d.current_rev, r.text_hash FROM drafts d JOIN draft_revisions r "
        "ON r.draft_id = d.id AND r.rev = d.current_rev WHERE d.id = ?",
        (draft_id,),
    )
    return StandInApproval(new_id(), draft_id, int(rev), str(digest))


def approved(held: Held, steps: tuple[int, ...] = (1, 2)) -> tuple[str, list[str]]:
    """A new enrolled Contact with revision 1 on each Draft: (contact, draft ids by Step)."""
    return drafted(held.world, steps)


def raw(world: World, statement: str, params: tuple[object, ...] = ()) -> None:
    connection = sqlite3.connect(world.db_file, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement, params)
    finally:
        connection.close()


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


def test_an_approved_step_1_becomes_one_queued_om(
    held: Held, gate_calls: list[tuple[object, ...]]
) -> None:
    world = held.world
    contact_id, [step_1, _] = approved(held)
    [(lead_id, segment_id)] = world.sql(
        "SELECT l.id, l.segment_id FROM leads l JOIN contacts c ON c.lead_id = l.id "
        "WHERE c.id = ?",
        (contact_id,),
    )
    world.sql(
        "UPDATE leads SET fit_score = 60, signal_score = 30, lead_score = 72, tier = 'A', "
        "tier_computed_at = ? WHERE id = ?",
        (STAMP, lead_id),
    )
    approval = approve(world, step_1)
    before = held.counts()
    gate_calls.clear()  # the setup's own gate calls

    om_id = held.enqueue(step_1, approval)

    assert held.counts() == {
        "outbound_messages": before["outbound_messages"] + 1,
        "unsub_tokens": before["unsub_tokens"] + 1,
        "events": before["events"] + 1,
    }
    om = held.om(om_id)
    [(email, market_id)] = world.sql(
        "SELECT email, market_id FROM contacts WHERE id = ?", (contact_id,)
    )
    [(enrollment_id, inbox_id, variant_id)] = world.sql(
        "SELECT e.id, e.sending_inbox_id, e.variant_id FROM drafts d JOIN enrollments e "
        "ON e.id = d.enrollment_id WHERE d.id = ?",
        (step_1,),
    )
    [(subject, body, snapshot_id)] = world.sql(
        "SELECT subject, body, footer_snapshot_id FROM draft_revisions WHERE draft_id = ?",
        (step_1,),
    )
    composed_body = body + "\n\n" + footer_text(world, snapshot_id)
    assert om["to_address"] == email
    assert (om["subject"], om["body"]) == (subject, composed_body)
    assert render.text_hash(email, inbox_id, subject, composed_body) == approval.text_hash
    assert {key: om[key] for key in ("draft_id", "approval_id", "kind", "status")} == {
        "draft_id": step_1,
        "approval_id": approval.approval_id,
        "kind": "step",
        "status": "queued",
    }
    assert (om["inbox_id"], om["contact_id"], om["lead_id"], om["enrollment_id"]) == (
        inbox_id,
        contact_id,
        lead_id,
        enrollment_id,
    )
    assert om["approved_hash"] == approval.text_hash
    assert (om["tier"], om["lead_score"], om["variant_id"]) == ("A", 72, variant_id)
    assert (om["segment_id"], om["market_id"], om["step_no"]) == (segment_id, market_id, 1)
    assert (om["cancelled_at"], om["sent_at"]) == (None, None)
    assert om["created_at"] == world.clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    [(token_row_hash,)] = world.sql("SELECT token_hash FROM unsub_tokens WHERE om_id = ?", (om_id,))
    assert token_row_hash == token_hash(om["unsub_token"])
    assert len(held.codec.encoded) == 1
    assert held.events(om_id) == [
        (None, "queued", "enqueued", approval.approval_id, "system:sending", om["created_at"])
    ]
    assert [call[1:] for call in gate_calls] == [("email", "cold")]
    view = world.read(lambda: sending_queries.outbound_message(om_id))
    assert view is not None and view.status == "queued" and view.body == composed_body
    live = world.read(lambda: sending_queries.live_outbound_of_draft(step_1))
    assert live is not None and live.id == om_id


def test_a_later_step_is_gated_as_a_follow_up_and_without_a_score_has_no_tier(
    held: Held, gate_calls: list[tuple[object, ...]]
) -> None:
    _, [_, step_2] = approved(held)
    approval = approve(held.world, step_2)
    gate_calls.clear()

    om_id = held.enqueue(step_2, approval)

    om = held.om(om_id)
    assert (om["tier"], om["lead_score"], om["step_no"]) == (None, None, 2)
    assert [call[1:] for call in gate_calls] == [("email", "follow_up")]


def test_a_recontact_draft_is_gated_as_a_recontact(
    held: Held, gate_calls: list[tuple[object, ...]]
) -> None:
    _, [step_1] = approved(held, (1,))
    held.world.sql("UPDATE drafts SET kind = 'recontact' WHERE id = ?", (step_1,))
    approval = approve(held.world, step_1)
    gate_calls.clear()

    om_id = held.enqueue(step_1, approval)

    assert held.om(om_id)["kind"] == "recontact"
    assert [call[1:] for call in gate_calls] == [("email", "recontact")]


def test_the_draft_object_or_its_id_both_work(held: Held) -> None:
    @dataclass(frozen=True)
    class Ref:
        id: str

    _, [step_1, step_2] = approved(held)
    first_approval = approve(held.world, step_1)
    first = held.world.transact(lambda: held.sending.enqueue(Ref(step_1), first_approval))
    second = held.enqueue(step_2, approve(held.world, step_2))

    assert held.om(first)["draft_id"] == step_1
    assert held.om(second)["draft_id"] == step_2


# Refusals -----------------------------------------------------------------------------------------


def refused(held: Held, draft_id: str, approval: StandInApproval, error: type[Exception]) -> None:
    before = held.counts()
    with pytest.raises(error):
        held.enqueue(draft_id, approval)
    assert held.counts() == before


def test_a_changed_email_after_approval_is_a_hash_mismatch(held: Held) -> None:
    contact_id, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    held.world.sql(
        "UPDATE contacts SET email = 'other@' || substr(email, instr(email, '@') + 1) "
        "WHERE id = ?",
        (contact_id,),
    )

    refused(held, step_1, approval, HashMismatch)
    assert held.oms(step_1) == []
    assert held.codec.encoded == []


def test_an_approval_with_another_hash_is_a_hash_mismatch(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    wrong = StandInApproval(approval.approval_id, step_1, approval.rev, "0" * 64)

    refused(held, step_1, wrong, HashMismatch)
    assert held.oms(step_1) == []


def test_a_second_enqueue_is_already_queued(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    om_id = held.enqueue(step_1, approval)

    again = StandInApproval(new_id(), step_1, approval.rev, approval.text_hash)
    refused(held, step_1, again, AlreadyQueued)
    assert held.oms(step_1) == [(om_id, "queued")]


def test_a_raw_second_live_insert_is_refused(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    copy = f"SELECT ?, {OM_COLUMNS} FROM outbound_messages WHERE id = ?"

    with pytest.raises(sqlite3.IntegrityError):
        raw(
            held.world,
            f"INSERT INTO outbound_messages (id, {OM_COLUMNS}) {copy}",
            (new_id(), om_id),
        )
    with pytest.raises(sqlite3.IntegrityError):  # nor can a replace delete the first
        raw(
            held.world,
            f"INSERT OR REPLACE INTO outbound_messages (id, {OM_COLUMNS}) {copy}",
            (new_id(), om_id),
        )
    connection = sqlite3.connect(held.world.db_file, isolation_level=None)
    try:  # the partial unique index stands alone too, without the trigger
        connection.execute("DROP TRIGGER trg_outbound_messages_insert_new")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                f"INSERT INTO outbound_messages (id, {OM_COLUMNS}) {copy}", (new_id(), om_id)
            )
    finally:
        connection.close()
    assert held.oms(step_1) == [(om_id, "queued")]


def test_after_a_cancel_the_draft_can_be_enqueued_again(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    first = held.enqueue(step_1, approval)
    assert held.cancel(first) is True

    again = StandInApproval(new_id(), step_1, approval.rev, approval.text_hash)
    second = held.enqueue(step_1, again)

    assert held.oms(step_1) == [(first, "cancelled"), (second, "queued")]
    tokens = held.world.sql(
        "SELECT COUNT(*) FROM unsub_tokens WHERE om_id IN (?, ?)", (first, second)
    )
    assert tokens == [(2,)]


@pytest.mark.parametrize(
    ("kind", "detach"), [("reply", False), ("rebook", True), ("channel", True)]
)
def test_a_draft_without_a_bound_inbox_is_unsupported(held: Held, kind: str, detach: bool) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    held.world.sql("UPDATE drafts SET kind = ? WHERE id = ?", (kind, step_1))
    if detach:
        held.world.sql(
            "UPDATE drafts SET enrollment_id = NULL, step_no = NULL, day_offset = NULL "
            "WHERE id = ?",
            (step_1,),
        )

    refused(held, step_1, approval, UnsupportedKind)
    assert held.oms(step_1) == []


def test_a_contact_without_an_email_is_a_hash_mismatch(
    held: Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    contact_id, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    held.world.sql(
        "UPDATE contacts SET email = NULL, address_class = NULL WHERE id = ?", (contact_id,)
    )
    # the real gate blocks an address-less Contact first; past it, enqueue still refuses
    monkeypatch.setattr(compliance_api, "assert_contactable", lambda *args: Allowed())

    refused(held, step_1, approval, HashMismatch)
    assert held.oms(step_1) == []
    assert held.codec.encoded == []


def test_a_non_ulid_approval_id_is_a_value_error(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    bad = StandInApproval("not-a-ulid", step_1, approval.rev, approval.text_hash)

    refused(held, step_1, bad, ValueError)
    assert held.oms(step_1) == []


def test_an_unknown_draft_is_a_lookup_error(held: Held) -> None:
    unknown = new_id()

    refused(held, unknown, StandInApproval(new_id(), unknown, 1, "0" * 64), LookupError)


def test_a_draft_whose_enrollment_is_unknown_is_unsupported(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    held.world.sql("UPDATE drafts SET enrollment_id = ? WHERE id = ?", (new_id(), step_1))

    refused(held, step_1, approval, UnsupportedKind)
    assert held.oms(step_1) == []


def test_a_ready_draft_is_not_approved(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    held.world.sql("UPDATE drafts SET status = 'ready' WHERE id = ?", (step_1,))

    refused(held, step_1, approval, NotApproved)


@pytest.mark.parametrize("field", ["rev", "draft_id"])
def test_an_approval_of_another_revision_or_draft_is_not_approved(held: Held, field: str) -> None:
    _, [step_1, step_2] = approved(held)
    approval = approve(held.world, step_1)
    approve(held.world, step_2)
    other = (
        StandInApproval(approval.approval_id, step_1, 2, approval.text_hash)
        if field == "rev"
        else StandInApproval(approval.approval_id, step_2, 1, approval.text_hash)
    )

    refused(held, step_1, other, NotApproved)
    assert held.oms(step_1) == []


def test_a_suppressed_contact_is_blocked(held: Held) -> None:
    contact_id, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    [(email,)] = held.world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))
    with held.world.units.open():
        Compliance(held.world.clock).add_value(email)
    held.world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (step_1,))  # 5.4's cascade
    before = held.counts()

    with pytest.raises(ContactBlocked) as raised:
        held.enqueue(step_1, approval)

    assert BlockReason.ADDRESS_SUPPRESSED in raised.value.reasons
    assert held.counts() == before
    assert held.oms(step_1) == []


def test_without_a_compliance_enqueue_raises_type_error(held: Held) -> None:
    _, [step_1, _] = approved(held)
    bare = Sending(held.world.clock, env="test")
    approval = approve(held.world, step_1)
    before = held.counts()

    with pytest.raises(TypeError, match="Compliance"):
        held.world.transact(lambda: bare.enqueue(step_1, approval))

    assert held.counts() == before


def test_a_failing_codec_rolls_everything_back(held: Held) -> None:
    _, [step_1, _] = approved(held)
    held.codec.fail = AdapterError(False, "unsub_key_missing", "no key")
    before = held.counts()

    with pytest.raises(AdapterError):
        held.enqueue(step_1, approve(held.world, step_1))

    assert held.counts() == before


def test_a_failure_after_the_token_rolls_the_token_back(
    held: Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, [step_1, _] = approved(held)

    def broken(*args: object, **kwargs: object) -> str:
        raise RuntimeError("disk full")

    monkeypatch.setattr(outbound_repo, "insert", broken)
    before = held.counts()

    with pytest.raises(RuntimeError, match="disk full"):
        held.enqueue(step_1, approve(held.world, step_1))

    assert held.codec.encoded  # the token was issued, then rolled back
    assert held.counts() == before


# Cancel -------------------------------------------------------------------------------------------


def test_cancel_moves_a_queued_om_to_cancelled_once(held: Held) -> None:
    _, [step_1, _] = approved(held)
    approval = approve(held.world, step_1)
    om_id = held.enqueue(step_1, approval)

    assert held.cancel(om_id) is True

    om = held.om(om_id)
    now = held.world.clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert (om["status"], om["cancelled_at"]) == ("cancelled", now)
    assert held.events(om_id)[-1] == (
        "queued",
        "cancelled",
        "unapproved",
        approval.approval_id,
        "system:sending",
        now,
    )
    before = held.counts()
    assert held.cancel(om_id) is False  # a repeat is a no-op
    assert held.counts() == before
    assert held.world.read(lambda: sending_queries.live_outbound_of_draft(step_1)) is None


@pytest.mark.parametrize("status", ["sending", "unknown", "sent"])
def test_cancel_past_queued_is_already_sending(held: Held, status: str) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    held.world.sql("UPDATE outbound_messages SET status = ? WHERE id = ?", (status, om_id))
    before = held.counts()

    with pytest.raises(AlreadySending, match="already sending"):
        held.cancel(om_id)

    assert held.om(om_id)["status"] == status
    assert held.counts() == before


def test_cancel_that_loses_the_race_is_already_sending(
    held: Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    monkeypatch.setattr(outbound_repo, "set_status", lambda *args, **kwargs: False)
    before = held.counts()

    with pytest.raises(AlreadySending, match="already sending"):
        held.cancel(om_id)

    assert held.om(om_id)["status"] == "queued"
    assert held.counts() == before
    assert len(held.events(om_id)) == 1  # only the enqueue's


def test_cancel_of_an_unknown_om_is_a_lookup_error(held: Held) -> None:
    for unknown in (new_id(), "not-a-ulid"):
        with pytest.raises(LookupError):
            held.cancel(unknown)


# Erasure ------------------------------------------------------------------------------------------


def test_erasing_the_contact_tombstones_the_copy_and_keeps_the_rest(held: Held) -> None:
    world = held.world
    contact_id, [step_1, step_2] = approved(held)
    queued = held.enqueue(step_1, approve(world, step_1))
    cancelled = held.enqueue(step_2, approve(world, step_2))
    held.cancel(cancelled)
    _, [other_1, _] = approved(held)
    other = held.enqueue(other_1, approve(world, other_1))
    kept = (
        "draft_id", "approval_id", "kind", "status", "inbox_id", "approved_hash", "tier",
        "lead_score", "variant_id", "segment_id", "market_id", "step_no", "created_at",
        "cancelled_at", "sent_at",
    )  # fmt: skip
    before = {om_id: held.om(om_id) for om_id in (queued, cancelled)}
    erasures = Erasures(world.clock, Compliance(world.clock), queue=queue_on(world.clock))

    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    for om_id, was in before.items():
        now = held.om(om_id)
        copy = [now[c] for c in ("to_address", "subject", "body", "unsub_token")]
        assert copy == ["[erased]"] * 4
        assert {c: now[c] for c in kept} == {c: was[c] for c in kept}
    assert held.om(other)["to_address"] != "[erased]"  # another Contact's: kept
    rows = world.sql("SELECT * FROM outbound_messages ORDER BY id")
    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))
    assert world.sql("SELECT * FROM outbound_messages ORDER BY id") == rows  # no change


def test_sending_declares_the_table_tombstoned() -> None:
    assert sending_api.ERASURE["outbound_messages"].coverage == "tombstoned"


# A Lead merge -------------------------------------------------------------------------------------


def test_a_lead_merge_moves_the_oms_to_the_survivor_and_keeps_the_copy(held: Held) -> None:
    world = held.world
    contact_id, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(world, step_1))
    [(merged,)] = world.sql("SELECT lead_id FROM contacts WHERE id = ?", (contact_id,))
    survivor, _ = world.contact()
    was = held.om(om_id)

    world.transact(lambda: sending_api.reparent_lead(LeadsMerged(survivor, merged)))

    now = held.om(om_id)
    assert now["lead_id"] == survivor
    assert {k: v for k, v in now.items() if k != "lead_id"} == {
        k: v for k, v in was.items() if k != "lead_id"
    }


# The table ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE outbound_messages SET body = 'something else' WHERE id = ?",
        "UPDATE outbound_messages SET to_address = 'x@example.com' WHERE id = ?",
        "UPDATE outbound_messages SET approved_hash = '" + "0" * 64 + "' WHERE id = ?",
        "UPDATE outbound_messages SET inbox_id = NULL WHERE id = ?",
        "UPDATE outbound_messages SET tier = 'B' WHERE id = ?",
        "UPDATE outbound_messages SET created_at = '2026-01-01T00:00:00.000000Z' WHERE id = ?",
        "UPDATE outbound_messages SET kind = 'recontact' WHERE id = ?",
        "UPDATE outbound_messages SET approval_id = '01JZZZZZZZZZZZZZZZZZZZZZZZ' WHERE id = ?",
        "UPDATE outbound_messages SET contact_id = NULL WHERE id = ?",
        "UPDATE outbound_messages SET step_no = 2 WHERE id = ?",
        "DELETE FROM outbound_messages WHERE id = ?",
    ],
)
def test_the_copy_and_the_snapshot_are_fixed(held: Held, statement: str) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    was = held.om(om_id)

    with pytest.raises(sqlite3.IntegrityError, match="fixed"):
        raw(held.world, statement, (om_id,))

    assert held.om(om_id) == was


def test_the_status_and_its_times_stay_updatable(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))

    raw(
        held.world,
        "UPDATE outbound_messages SET status = 'sent', sent_at = ? WHERE id = ?",
        (STAMP, om_id),
    )

    assert (held.om(om_id)["status"], held.om(om_id)["sent_at"]) == ("sent", STAMP)


def test_the_cancelled_check_pairs_the_status_and_its_time(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))

    with pytest.raises(sqlite3.IntegrityError, match="cancelled"):
        raw(held.world, "UPDATE outbound_messages SET status = 'cancelled' WHERE id = ?", (om_id,))

    assert held.om(om_id)["status"] == "queued"


def test_a_cancelled_om_is_never_revived(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    held.cancel(om_id)
    was = held.om(om_id)

    with pytest.raises(sqlite3.IntegrityError, match="final"):
        raw(
            held.world,
            "UPDATE outbound_messages SET status = 'queued', cancelled_at = NULL WHERE id = ?",
            (om_id,),
        )

    assert held.om(om_id) == was


@pytest.mark.parametrize("status", ["queued", "sending", "unknown", "cancelled"])
def test_a_sent_om_never_moves(held: Held, status: str) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    raw(
        held.world,
        "UPDATE outbound_messages SET status = 'sent', sent_at = ? WHERE id = ?",
        (STAMP, om_id),
    )
    was = held.om(om_id)
    cancelled_at = STAMP if status == "cancelled" else None

    with pytest.raises(sqlite3.IntegrityError, match="final"):
        raw(
            held.world,
            "UPDATE outbound_messages SET status = ?, cancelled_at = ? WHERE id = ?",
            (status, cancelled_at, om_id),
        )

    assert held.om(om_id) == was


def test_an_erased_value_is_never_restored(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    raw(held.world, "UPDATE outbound_messages SET body = '[erased]' WHERE id = ?", (om_id,))
    was = held.om(om_id)

    with pytest.raises(sqlite3.IntegrityError, match="fixed"):
        raw(held.world, "UPDATE outbound_messages SET body = 'real text' WHERE id = ?", (om_id,))

    assert held.om(om_id) == was


def test_the_lead_id_stays_updatable(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    other_lead, _ = held.world.contact()

    raw(held.world, "UPDATE outbound_messages SET lead_id = ? WHERE id = ?", (other_lead, om_id))

    assert held.om(om_id)["lead_id"] == other_lead


def test_insert_or_replace_cannot_overwrite_a_cancelled_om(held: Held) -> None:
    _, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))
    held.cancel(om_id)
    was = held.om(om_id)

    with pytest.raises(sqlite3.IntegrityError, match="fixed"):
        raw(
            held.world,
            f"INSERT OR REPLACE INTO outbound_messages (id, {OM_COLUMNS}) "
            f"SELECT id, {OM_COLUMNS} FROM outbound_messages WHERE id = ?",
            (om_id,),
        )

    assert held.om(om_id) == was
    assert held.oms(step_1) == [(om_id, "cancelled")]


# The boundaries -----------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_only_app_sending_imports_the_om_repository_and_nothing_here_calls_mail() -> None:
    repo = "nextron.db.repos.sending.outbound_messages"
    importers = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if repo in _imports(path)
    )
    assert importers == ["app/queries/sending.py", "app/sending/api.py"]
    for relative in ("app/sending/api.py", "db/repos/sending/outbound_messages.py"):
        source = (PACKAGE / relative).read_text(encoding="utf-8")
        assert "MailPort" not in source.replace("no ``MailPort``", "")
        imported = _imports(PACKAGE / relative)
        assert not any(name.startswith("nextron.ports.mail") for name in imported)


def test_the_export_withholds_the_token(held: Held) -> None:
    contact_id, [step_1, _] = approved(held)
    om_id = held.enqueue(step_1, approve(held.world, step_1))

    [row] = held.world.read(lambda: sending_queries.export_pii(contact_id))["outbound_messages"]

    assert row["id"] == om_id and "unsub_token" not in row
    assert held.world.read(lambda: sending_queries.export_pii("nope")) == {"outbound_messages": ()}

