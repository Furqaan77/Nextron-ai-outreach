"""``compliance.assert_sendable`` and ``SendGate.check`` (FR-31, FR-19, FR-23, FR-33, FR-34,
FR-50, AD-11, AD-16, NFR-7; Story 6.9) on the world of ``tests.app.test_enroll`` (a temporary
database at head, ``FrozenClock``, the real subscribers, the shipped seeds, Law-PrivateAI x US
active) with the footer settings and ``sender_timezone`` saved, a Step 1 Draft with revision 1
marked ``approved``, its inbox ``active`` with 20 days of recorded warm-up and a passing domain
check. Story 6.9's seams (``current_approval``, ``thread_quiet``, ``cold_sent_on``,
``sent_total_on``) are monkeypatched to good answers, except where a test shows their real
``None`` failing closed:

- every fact good passes; each row of the I/O matrix fails its own predicate;
- a responsive OM skips the cold predicates;
- the Enrollment binds the OM's Contact and inbox; an erased Contact, a brand inbox address, an
  unknown-zone basis and a footer snapshot for another Market each fail their predicate; a fact
  read that raises fails closed;
- ``assert_sendable`` writes nothing; ``SendGate.check`` raises one ``send_hash_mismatch`` alert
  per OM, only while the OM's approval is its Draft's current one, and nothing else;
- the module imports no repository and no higher owner (AST);
- NFR-7: no setting and no form field names a predicate or offers to disable one;
- the Lead page's Stop Sequence: shown only while an Enrollment is live, CSRF, the confirmation
  step, the stopped Enrollment and its cancelled ``ready`` Drafts, and the notice for a first
  click after the Enrollment ended.
"""

import ast
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance import send_gate as gate_module
from nextron.app.compliance.api import Compliance, SendGate, assert_sendable
from nextron.app.compliance.send_gate import SEND_HASH_MISMATCH, hash_mismatch_text
from nextron.app.notify.api import Notify
from nextron.app.queries import approval as approval_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import replies as replies_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries.approval import ApprovalView
from nextron.app.sending.api import Sending
from nextron.app.setup.api import Campaigns, Setup
from nextron.db.models.base import new_id
from nextron.db.repos.sending import checks as checks_repo
from nextron.domain import settings_schema
from nextron.domain.contact_market import TzBasis
from nextron.domain.contactability import Blocked, BlockReason
from nextron.domain.domain_auth import AuthItem, AuthReason, encode_details
from nextron.domain.enrollment import PauseCause
from nextron.domain.markets import validate_markets
from nextron.domain.send_gate import (
    COLD_KINDS,
    PREDICATES,
    GateResult,
    OmKind,
    Predicate,
    SendCandidate,
)
from tests.app.test_draft_revisions import FOOTER_SETTINGS, drafted
from tests.app.test_enroll import US_CAMPAIGN, World, build_world, queue_on, shipped
from tests.contracts.support import PACKAGE
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Tree

P = Predicate
NOW = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)  # a Wednesday: 11:00 in New York
APPROVED_AT = NOW - timedelta(hours=1)
WARMUP = ("2026-10-29", "Manual", "12", "0", "5", "30")  # 20 days before NOW
COLD_ONLY = tuple(p.name for p in PREDICATES if p.cold_only)
SEAMS_UNBUILT = (P.APPROVAL_LIVE, P.HASH_MATCHES, P.THREAD_QUIET, P.UNDER_DAILY_CAP)


class Seams:
    """Good answers for Story 6.9's seams; a test changes them."""

    def __init__(self) -> None:
        self.approval: ApprovalView | None = None
        self.quiet: bool | None = True
        self.cold_sent: int | None = 0
        self.sent_total: int | None = 0
        self.thread_since: list[datetime] = []

    def current_approval(self, draft_id: str) -> ApprovalView | None:
        if self.approval is None or self.approval.draft_id != draft_id:
            return None
        return self.approval

    def thread_quiet(self, thread_id: str | None, since: datetime) -> bool | None:
        self.thread_since.append(since)
        return self.quiet

    def cold_sent_on(self, inbox_id: str, local_date: date) -> int | None:
        return self.cold_sent

    def sent_total_on(self, inbox_id: str, local_date: date) -> int | None:
        return self.sent_total


class Gate:
    """The world, its Step 1 OM and the seams."""

    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = world
        self.monkeypatch = monkeypatch
        self.seams = Seams()
        world.clock.set(NOW)
        with world.units.open():
            Setup(world.clock).save({**FOOTER_SETTINGS, "sender_timezone": "Europe/London"})
        self.contact_id, [self.step_1, self.step_2] = drafted(world)
        [(self.enrollment_id, self.inbox_id, self.lead_id)] = world.sql(
            "SELECT e.id, e.sending_inbox_id, e.lead_id FROM drafts d JOIN enrollments e "
            "ON e.id = d.enrollment_id WHERE d.id = ?",
            (self.step_1,),
        )
        world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (self.step_1,))
        world.sql("UPDATE sending_inboxes SET status = 'active' WHERE id = ?", (self.inbox_id,))
        with world.units.open():
            Sending(world.clock, env="prod").record_warmup(self.inbox_id, *WARMUP)
        self.check(NOW - timedelta(hours=2))
        [(text_hash,)] = world.sql(
            "SELECT text_hash FROM draft_revisions WHERE draft_id = ? AND rev = 1", (self.step_1,)
        )
        self.approval_id = new_id()
        self.seams.approval = ApprovalView(self.approval_id, self.step_1, 1, APPROVED_AT, text_hash)
        self.om = SendCandidate(
            om_id=new_id(),
            kind=OmKind.STEP,
            draft_id=self.step_1,
            approval_id=self.approval_id,
            sending_inbox_id=self.inbox_id,
            contact_id=self.contact_id,
            enrollment_id=self.enrollment_id,
            thread_id=None,
        )
        self.patch_seams()

    def patch_seams(self) -> None:
        self.monkeypatch.setattr(approval_queries, "current_approval", self.seams.current_approval)
        self.monkeypatch.setattr(replies_queries, "thread_quiet", self.seams.thread_quiet)
        self.monkeypatch.setattr(sending_queries, "cold_sent_on", self.seams.cold_sent_on)
        self.monkeypatch.setattr(sending_queries, "sent_total_on", self.seams.sent_total_on)

    def check(self, at: datetime, passed: bool = True) -> None:
        [(domain_id,)] = self.world.sql(
            "SELECT sending_domain_id FROM sending_inboxes WHERE id = ?", (self.inbox_id,)
        )
        reasons = {item: AuthReason.OK for item in AuthItem}
        self.world.transact(
            lambda: checks_repo.insert(
                sending_domain_id=domain_id,
                checked_at=at,
                spf_ok=passed,
                dkim_ok=True,
                dmarc_ok=True,
                aligned_ok=True,
                passed=passed,
                details=encode_details(
                    reasons if passed else {**reasons, AuthItem.SPF: AuthReason.SPF_MISSING}
                ),
            )
        )

    def failing(self, om: SendCandidate | None = None, now: datetime = NOW) -> tuple[P, ...]:
        return self.result(om, now).failing

    def result(self, om: SendCandidate | None = None, now: datetime = NOW) -> GateResult:
        with self.world.units.open(read_only=True):
            return assert_sendable(om or self.om, now)


@pytest.fixture
def gate(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Gate:
    return Gate(build_world(db_engine), monkeypatch)


# The matrix -------------------------------------------------------------------------------------


def test_every_fact_good_passes(gate: Gate) -> None:
    result = gate.result()
    assert result.failing == () and result.passed
    assert gate.seams.thread_since == [APPROVED_AT]


def test_the_real_seams_fail_closed(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the shipped seams answer None until 5.4, 6.8 and 6.10

    assert gate.failing() == (*SEAMS_UNBUILT, P.UNDER_TOTAL_CEILING)
    assert approval_queries.current_approval(gate.step_1) is None
    assert replies_queries.thread_quiet(None, NOW) is None
    with gate.world.units.open(read_only=True):
        assert sending_queries.cold_sent_on(gate.inbox_id, NOW.date()) is None
        assert sending_queries.sent_total_on(gate.inbox_id, NOW.date()) is None


def test_approval_expiry(gate: Gate) -> None:
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_at=NOW - timedelta(hours=72))
    assert gate.failing() == (P.APPROVAL_LIVE,)
    gate.seams.approval = replace(approval, approved_at=NOW - timedelta(hours=71, minutes=59))
    assert gate.failing() == ()


def test_an_approval_that_isnt_current(gate: Gate) -> None:
    assert gate.failing(replace(gate.om, approval_id=new_id())) == (P.APPROVAL_LIVE,)
    gate.world.sql("UPDATE drafts SET status = 'ready' WHERE id = ?", (gate.step_1,))
    assert gate.failing() == (P.APPROVAL_LIVE,)


def test_a_hash_that_doesnt_match_the_revision(gate: Gate) -> None:
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_hash="f" * 64)
    assert gate.failing() == (P.HASH_MATCHES,)
    gate.seams.approval = replace(approval, rev=2)  # approved another revision
    assert gate.failing() == (P.HASH_MATCHES,)


def test_a_hash_that_no_longer_recomputes(gate: Gate) -> None:
    gate.world.sql(
        "UPDATE contacts SET email = 'zelda.new@' || substr(email, instr(email, '@') + 1) "
        "WHERE id = ?",
        (gate.contact_id,),
    )
    # the new address has no Provenance Record either, so live Eligibility fails too
    assert gate.failing() == (P.HASH_MATCHES, P.MARKET_ELIGIBLE)
    other = gate.world.inbox("mac@nextron-mail.com")  # the OM moved to another inbox
    gate.world.sql("UPDATE sending_inboxes SET status = 'active' WHERE id = ?", (other,))
    assert P.HASH_MATCHES in gate.failing(replace(gate.om, sending_inbox_id=other))


@pytest.mark.parametrize("status", ["paused", "auth_failed", "connecting", "removed"])
def test_an_inbox_that_isnt_active(gate: Gate, status: str) -> None:
    gate.world.sql("UPDATE sending_inboxes SET status = ? WHERE id = ?", (status, gate.inbox_id))
    found = gate.failing()
    if status == "removed":  # no longer listed: no warm-up or caps to read either
        assert found == (P.INBOX_READY, P.UNDER_DAILY_CAP, P.WARMUP_OK, P.UNDER_TOTAL_CEILING)
    else:  # an inbox that isn't active has no cold cap
        assert found == (P.INBOX_READY, P.UNDER_DAILY_CAP)


def test_an_unknown_inbox(gate: Gate) -> None:
    assert P.INBOX_READY in gate.failing(replace(gate.om, sending_inbox_id=new_id()))


def test_no_passing_domain_check_within_26_hours(gate: Gate) -> None:
    assert gate.failing(now=NOW + timedelta(hours=24)) == (P.INBOX_READY,)  # 26 h since it
    gate.check(NOW - timedelta(minutes=30), passed=False)
    assert gate.failing() == (P.INBOX_READY,)


def test_recovery_mode_fails_the_inbox(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_queries, "recovery_mode", lambda: True)
    assert gate.failing() == (P.INBOX_READY,)


@pytest.mark.parametrize("key", ["legal_name", "postal_address"])
def test_a_blank_footer_setting(gate: Gate, key: str) -> None:
    gate.world.sql("DELETE FROM settings WHERE key = ?", (key,))
    assert gate.failing() == (P.FOOTER_COMPLETE,)


def test_a_footer_setting_changed_after_approval(gate: Gate) -> None:
    with gate.world.units.open():
        Setup(gate.world.clock).save({"postal_address": "2 Low Street, London"})
    assert gate.failing() == (P.FOOTER_COMPLETE,)


def test_a_contact_that_moved_market(gate: Gate) -> None:
    gate.world.sql(
        "UPDATE contacts SET market_id = (SELECT id FROM markets WHERE code = 'uk') WHERE id = ?",
        (gate.contact_id,),
    )
    found = gate.failing()
    assert P.FOOTER_COMPLETE in found  # the snapshot is the US footer
    assert P.HASH_MATCHES not in found


def test_a_disabled_market(gate: Gate) -> None:
    markets = [
        replace(market, enabled=False) if market.code == "us" else market
        for market in shipped(main.MARKETS_FILE_NAME, validate_markets)
    ]
    with gate.world.units.open():
        Setup(gate.world.clock).sync_markets(markets)
    assert gate.failing() == (P.MARKET_ELIGIBLE,)
    reply = replace(gate.om, kind=OmKind.REPLY)
    assert gate.failing(reply) == (P.MARKET_ELIGIBLE,)


def test_live_eligibility_that_fails(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    real = compliance_api.live_eligibility

    def ineligible(contact_id: str, now: datetime) -> Any:
        return replace(real(contact_id, now), eligible=False)

    monkeypatch.setattr(compliance_api, "live_eligibility", ineligible)
    assert gate.failing() == (P.MARKET_ELIGIBLE,)
    assert gate.failing(replace(gate.om, kind=OmKind.REPLY)) == ()  # a responsive kind


def test_suppressed_after_approval(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []

    def blocked(subject: object, channel: object, purpose: object) -> Blocked:
        calls.append((subject, channel, purpose))
        return Blocked((BlockReason.ADDRESS_SUPPRESSED,))

    monkeypatch.setattr(compliance_api, "assert_contactable", blocked)
    # the live Eligibility reads the same gate, so a cold OM fails both
    assert gate.failing() == (P.CONTACTABLE, P.MARKET_ELIGIBLE)
    assert [call[1:] for call in calls] == [("email", "cold"), ("email", "cold")]
    calls.clear()
    assert gate.failing(replace(gate.om, kind=OmKind.REPLY)) == (P.CONTACTABLE,)
    assert [call[1:] for call in calls] == [("email", "reply")]


def test_a_real_suppression_blocks_the_send(gate: Gate) -> None:
    [(email,)] = gate.world.sql("SELECT email FROM contacts WHERE id = ?", (gate.contact_id,))
    with gate.world.units.open():
        Compliance(gate.world.clock).add_value(email)
    assert P.CONTACTABLE in gate.failing()


@pytest.mark.parametrize(
    ("kind", "step", "purpose"),
    [
        (OmKind.STEP, 2, "follow_up"),
        (OmKind.RECONTACT, 1, "recontact"),
        (OmKind.REBOOK, 1, "rebook"),
    ],
)
def test_the_contactable_purpose_follows_the_kind(
    gate: Gate, monkeypatch: pytest.MonkeyPatch, kind: OmKind, step: int, purpose: str
) -> None:
    purposes: list[object] = []
    real = compliance_api.assert_contactable

    def recording(subject: Any, channel: Any, chosen: Any) -> Any:
        purposes.append(str(chosen))
        return real(subject, channel, chosen)

    monkeypatch.setattr(compliance_api, "assert_contactable", recording)
    draft_id = gate.step_1 if step == 1 else gate.step_2
    gate.result(replace(gate.om, kind=kind, draft_id=draft_id))
    assert purposes[0] == purpose
    assert len(purposes) == (2 if kind in COLD_KINDS else 1)


def test_a_paused_enrollment(gate: Gate) -> None:
    gate.world.transact(
        lambda: gate.world.sequencing.pause(gate.enrollment_id, PauseCause.OPERATOR)
    )
    assert gate.failing() == (P.ENROLLMENT_ACTIVE,)


def test_a_stopped_enrollment(gate: Gate) -> None:
    gate.world.stop(gate.enrollment_id)
    assert gate.failing() == (P.ENROLLMENT_ACTIVE,)
    no_enrollment = replace(gate.om, enrollment_id=None)  # no Campaign to read either
    assert gate.failing(no_enrollment) == (P.ENROLLMENT_ACTIVE, P.CAMPAIGN_ACTIVE)


def test_a_completed_enrollment(gate: Gate) -> None:
    gate.world.sql(
        "UPDATE enrollments SET status = 'completed', ended_at = ? WHERE id = ?",
        ("2026-11-18T16:00:00.000000Z", gate.enrollment_id),
    )
    assert gate.failing() == (P.ENROLLMENT_ACTIVE,)


@pytest.mark.parametrize("quiet", [False, None])
def test_thread_activity_after_approval(gate: Gate, quiet: bool | None) -> None:
    gate.seams.quiet = quiet
    assert gate.failing() == (P.THREAD_QUIET,)


def test_a_paused_campaign(gate: Gate) -> None:
    campaign_id = gate.world.campaign(US_CAMPAIGN)
    with gate.world.units.open():
        Campaigns(gate.world.clock).pause(campaign_id)
    assert gate.failing() == (P.CAMPAIGN_ACTIVE,)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 18, 15, 59, tzinfo=UTC),  # 10:59 New York, unknown US zone
        datetime(2026, 11, 18, 22, 0, tzinfo=UTC),  # 17:00 New York
        datetime(2026, 11, 21, 16, 0, tzinfo=UTC),  # Saturday
    ],
)
def test_outside_the_send_window(gate: Gate, now: datetime) -> None:
    gate.check(now - timedelta(minutes=5))
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_at=now - timedelta(hours=1))
    assert gate.failing(now=now) == (P.IN_SEND_WINDOW,)


def test_a_known_zone_uses_the_full_window(gate: Gate) -> None:
    gate.world.sql(
        "UPDATE contacts SET tz = 'America/Chicago', tz_basis = 'office_location' WHERE id = ?",
        (gate.contact_id,),
    )
    early = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)  # 08:00 Chicago, 09:00 New York
    gate.check(early - timedelta(minutes=5))
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_at=early - timedelta(hours=1))
    assert gate.failing(now=early) == ()


def test_the_daily_cap(gate: Gate) -> None:
    with gate.world.units.open(read_only=True):
        [cap] = sending_queries.cold_daily_caps(NOW.date()).values()
    assert cap == 12
    gate.seams.cold_sent = cap
    assert gate.failing() == (P.UNDER_DAILY_CAP,)
    gate.seams.cold_sent = cap - 1
    assert gate.failing() == ()
    gate.seams.cold_sent = None
    assert gate.failing() == (P.UNDER_DAILY_CAP,)


def test_warm_up_under_14_days(gate: Gate) -> None:
    with gate.world.units.open():
        Sending(gate.world.clock, env="prod").record_warmup(
            gate.inbox_id, "2026-11-05", "Manual", "12", "0", "5", "30"
        )
    # 13 days: no cold cap yet either
    assert gate.failing() == (P.UNDER_DAILY_CAP, P.WARMUP_OK)


def test_the_total_ceiling(gate: Gate) -> None:
    with gate.world.units.open():
        Sending(gate.world.clock, env="prod").record_warmup(
            gate.inbox_id, "2026-10-29", "Manual", "12", "10", "5", "30"
        )
    gate.seams.sent_total = 40  # 40 sent + 10 warm-up tool sends = the ceiling of 50
    assert gate.failing() == (P.UNDER_TOTAL_CEILING,)
    gate.seams.sent_total = 39
    assert gate.failing() == ()
    gate.seams.sent_total = None
    assert gate.failing() == (P.UNDER_TOTAL_CEILING,)


def test_an_unset_sender_timezone_fails_the_day_counts(gate: Gate) -> None:
    gate.world.sql("DELETE FROM settings WHERE key = 'sender_timezone'")
    assert gate.failing() == (P.UNDER_DAILY_CAP, P.WARMUP_OK, P.UNDER_TOTAL_CEILING)


@pytest.mark.parametrize("kind", [OmKind.REPLY, OmKind.REBOOK])
def test_a_responsive_om_skips_every_cold_predicate(gate: Gate, kind: OmKind) -> None:
    # every cold fact bad: Saturday, the Enrollment stopped, the Campaign paused, 5 days of
    # warm-up, and the thread and sent-count seams unknown
    saturday = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
    gate.check(saturday - timedelta(minutes=5))
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_at=saturday - timedelta(hours=1))
    gate.seams.quiet = gate.seams.cold_sent = gate.seams.sent_total = None
    gate.world.stop(gate.enrollment_id)
    campaign_id = gate.world.campaign(US_CAMPAIGN)
    with gate.world.units.open():
        Campaigns(gate.world.clock).pause(campaign_id)
    with gate.world.units.open():
        Sending(gate.world.clock, env="prod").record_warmup(
            gate.inbox_id, "2026-11-13", "Manual", "12", "0", "5", "30"
        )
    assert gate.failing(now=saturday) == COLD_ONLY

    responsive = replace(gate.om, kind=kind, enrollment_id=None)
    assert gate.failing(responsive, saturday) == ()


def test_a_bad_candidate_or_now(gate: Gate) -> None:
    with pytest.raises(TypeError), gate.world.units.open(read_only=True):
        assert_sendable(object(), NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError), gate.world.units.open(read_only=True):
        assert_sendable(gate.om, NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(gate.om, kind="channel")


def test_the_enrollment_must_bind_the_oms_inbox_and_contact(gate: Gate) -> None:
    other = gate.world.inbox("mac@nextron-mail.com")
    gate.world.sql("UPDATE sending_inboxes SET status = 'active' WHERE id = ?", (other,))
    _, other_contact = gate.world.contact()
    for om in (
        replace(gate.om, sending_inbox_id=other),
        replace(gate.om, contact_id=other_contact),
    ):
        found = gate.failing(om)
        assert P.ENROLLMENT_ACTIVE in found and P.CAMPAIGN_ACTIVE in found


def test_an_erased_contact_fails_the_hash(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    real = research_queries.contact

    def erased(contact_id: str) -> Any:
        found = real(contact_id)
        return None if found is None else replace(found, erased_at=NOW - timedelta(minutes=5))

    monkeypatch.setattr(research_queries, "contact", erased)
    assert P.HASH_MATCHES in gate.failing()


def test_a_brand_inbox_address_fails_the_inbox(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = sending_queries.inboxes

    def branded() -> Any:
        return tuple(
            replace(inbox, address="mac@mail.nextronai.com")
            if inbox.id == gate.inbox_id
            else inbox
            for inbox in real()
        )

    monkeypatch.setattr(sending_queries, "inboxes", branded)
    assert gate.failing() == (P.INBOX_READY,)


@pytest.mark.parametrize("basis", [TzBasis.NO_MARKET, TzBasis.US_UNKNOWN_FALLBACK])
def test_an_unknown_zone_basis_uses_the_unknown_window(
    gate: Gate, monkeypatch: pytest.MonkeyPatch, basis: TzBasis
) -> None:
    real = research_queries.contact

    def unknown(contact_id: str) -> Any:
        found = real(contact_id)
        return None if found is None else replace(found, tz="America/Chicago", tz_basis=basis)

    monkeypatch.setattr(research_queries, "contact", unknown)
    early = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)  # 08:00 Chicago, 09:00 New York
    gate.check(early - timedelta(minutes=5))
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_at=early - timedelta(hours=1))
    assert gate.failing(now=early) == (P.IN_SEND_WINDOW,)  # Chicago's zone is not trusted


def test_a_footer_snapshot_for_another_market(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    [(uk,)] = gate.world.sql("SELECT id FROM markets WHERE code = 'uk'")
    real = drafting_queries.footer_snapshot

    def elsewhere(snapshot_id: str) -> Any:
        found = real(snapshot_id)
        return None if found is None else replace(found, market_id=uk)

    monkeypatch.setattr(drafting_queries, "footer_snapshot", elsewhere)
    assert gate.failing() == (P.FOOTER_COMPLETE,)  # the text still matches


def test_a_fact_read_that_raises_fails_closed(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad(*args: object) -> Any:
        raise ValueError("a stored plan that no longer decodes")

    monkeypatch.setattr(gate_module, "warmup_days", bad)
    monkeypatch.setattr(gate_module, "tool_send_figure", bad)
    assert gate.failing() == (P.WARMUP_OK, P.UNDER_TOTAL_CEILING)

    def missing() -> Any:
        raise LookupError("no inbox list")

    monkeypatch.setattr(sending_queries, "inboxes", missing)
    found = gate.failing()
    assert P.INBOX_READY in found and P.WARMUP_OK in found


# Writes and the alert ---------------------------------------------------------------------------


def test_assert_sendable_writes_nothing(gate: Gate) -> None:
    before = gate.world.counts()
    gate.result()
    gate.seams.approval = None
    assert gate.failing() == (P.APPROVAL_LIVE, P.HASH_MATCHES, P.THREAD_QUIET)
    assert gate.world.counts() == before


def gate_check(gate: Gate, om: SendCandidate) -> GateResult:
    clock = FrozenClock(NOW)
    send_gate = SendGate(clock, Notify(clock, queue_on(clock), alert_ports={}))
    return gate.world.transact(lambda: send_gate.check(om))


def test_a_hash_mismatch_raises_one_alert_per_om(gate: Gate) -> None:
    approval = gate.seams.approval
    assert approval is not None
    gate.seams.approval = replace(approval, approved_hash="f" * 64)
    before = gate.world.counts()

    assert gate_check(gate, gate.om).failing == (P.HASH_MATCHES,)
    assert gate_check(gate, gate.om).failing == (P.HASH_MATCHES,)

    after = gate.world.counts()
    gained = {table: after[table] - before[table] for table in after if after[table] != before[table]}
    assert gained == {"alerts": 1, "notifications": 1, "jobs": 1, "events": 2}
    [(kind, subject, text)] = gate.world.sql("SELECT kind, subject, text FROM notifications")
    assert (kind, subject) == (SEND_HASH_MISMATCH, gate.om.om_id)
    assert text == hash_mismatch_text(gate.om.om_id)
    assert re.sub(r"[0-9A-HJKMNP-TV-Z]{26}", "<id>", text) == (
        "Send blocked: message <id> no longer matches its approved text. Approve its Draft again."
    )
    [(alert_subject,)] = gate.world.sql("SELECT subject FROM alerts")
    assert alert_subject == gate.om.om_id

    other = replace(gate.om, om_id=new_id())
    gate_check(gate, other)
    assert len(gate.world.sql("SELECT id FROM notifications")) == 2


def test_no_current_approval_raises_no_alert(gate: Gate) -> None:
    approval = gate.seams.approval
    assert approval is not None
    before = gate.world.counts()
    gate.seams.approval = None  # the shipped seam, until Story 5.4
    assert P.HASH_MATCHES in gate_check(gate, gate.om).failing
    # a mismatched approval that isn't the OM's: no approved text of this OM to mismatch
    gate.seams.approval = replace(approval, approved_hash="f" * 64)
    not_current = replace(gate.om, approval_id=new_id())
    assert P.HASH_MATCHES in gate_check(gate, not_current).failing
    assert gate.world.counts() == before


def test_another_failure_raises_no_alert(gate: Gate) -> None:
    gate.seams.quiet = False
    before = gate.world.counts()
    assert gate_check(gate, gate.om).failing == (P.THREAD_QUIET,)
    assert gate_check(gate, gate.om).passed is False
    assert gate.world.counts() == before
    gate.seams.quiet = True
    assert gate_check(gate, gate.om).passed
    assert gate.world.counts() == before
    assert gate.world.sql("SELECT status FROM drafts WHERE id = ?", (gate.step_1,)) == [
        ("approved",)
    ]


# The module's imports (AD-3, c6) ----------------------------------------------------------------


def test_the_gate_imports_no_repository_and_no_higher_owner() -> None:
    path = PACKAGE / "app" / "compliance" / "send_gate.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not [name for name in imported if name.startswith("nextron.db.repos")]
    higher = ("sequencing", "approval", "drafting", "sending", "replies", "pipeline")
    assert not [
        name for name in imported if any(name.startswith(f"nextron.app.{o}") for o in higher)
    ]
    allowed = (
        "nextron.app.queries",
        "nextron.app.compliance",
        "nextron.db.uow",
        "nextron.domain",
        "nextron.ports",
    )
    assert all(
        name.startswith(allowed) for name in imported if name.startswith("nextron")
    ), imported
    assert gate_module.compliance_api is compliance_api


# NFR-7: nothing disables or weakens a predicate -----------------------------------------------

WEAKENING = re.compile(r"disable|skip|bypass|override", re.IGNORECASE)


def test_no_setting_names_a_predicate_or_weakens_the_gate() -> None:
    names = {str(predicate) for predicate in Predicate}
    for found in settings_schema.declared():
        texts = (str(found.key), found.label)
        assert not any(name in text.lower() for name in names for text in texts), found.key
        assert not any(WEAKENING.search(text) for text in texts), found.key


def test_no_form_field_names_a_predicate() -> None:
    names = {str(predicate) for predicate in Predicate}
    fields = set()
    for template in (PACKAGE / "web" / "templates").glob("*.html"):
        text = template.read_text(encoding="utf-8")
        fields.update(re.findall(r"""name=["']([^"']+)["']""", text))
        fields.update(re.findall(r"""hx-vals='([^']*)'""", text))
    assert fields  # the scan read the forms
    assert not [field for field in fields if any(name in field for name in names)]


# Stop Sequence on the Lead page (Story 6.9) -------------------------------------------------------


def dashboard_of(world: World) -> tuple[Dashboard, bytes]:
    dashboard = Dashboard(units=world.units, clock=FrozenClock(world.clock.now()))
    return dashboard, dashboard.open_session()


def sequence_panel(dashboard: Dashboard, cookie: bytes, lead_id: str) -> Any:
    reply = dashboard.get(f"/leads/{lead_id}", cookie=cookie)
    assert reply.status == 200, reply
    found = Tree(reply.text).find_all("section", id="lead-sequence")
    return found[0] if found else None


def post_stop(
    dashboard: Dashboard, cookie: bytes, lead_id: str, body: bytes = b"", token: bool = True
) -> Any:
    return dashboard.post(
        f"/leads/{lead_id}/stop-sequence",
        cookie=cookie,
        token=dashboard.csrf_token(cookie) if token else None,
        headers=[(b"content-type", FORM)],
        body=body,
    )


@pytest.fixture
def world(db_engine: Engine) -> World:
    found = build_world(db_engine)
    with found.units.open():
        Setup(found.clock).save(FOOTER_SETTINGS)
    return found


def test_stop_sequence_asks_then_stops_and_cancels_the_ready_drafts(world: World) -> None:
    _, [step_1, step_2] = drafted(world)
    [(enrollment_id, lead_id)] = world.sql(
        "SELECT enrollment_id, (SELECT lead_id FROM enrollments WHERE id = enrollment_id) "
        "FROM drafts WHERE id = ?",
        (step_1,),
    )
    dashboard, cookie = dashboard_of(world)

    panel = sequence_panel(dashboard, cookie, lead_id)
    button = panel.find("form")
    assert (button.attrs["method"], button.attrs["hx-post"]) == (
        "post",
        f"/leads/{lead_id}/stop-sequence",
    )
    assert button.attrs["hx-target"] == "#lead-sequence"
    assert button.find("input", name="csrf_token").attrs["type"] == "hidden"
    assert button.find("button").text == "Stop Sequence"

    assert post_stop(dashboard, cookie, lead_id, token=False).status == 403
    before = world.counts()
    asked = post_stop(dashboard, cookie, lead_id)
    assert asked.status == 200
    assert world.counts() == before  # the first click writes nothing
    question = Tree(asked.text).find("section", id="lead-sequence")
    assert question.find("p", role="alert").text == (
        "Stop this Lead's Sequence? Its ready Drafts are cancelled; an approved Draft stays "
        "approved but is blocked from sending while the Sequence is stopped."
    )
    confirm = question.find("form")
    assert confirm.find("input", name="confirm").attrs["value"] == "yes"
    assert confirm.find("button").text == "Yes, stop the Sequence"
    assert world.sql("SELECT status FROM enrollments WHERE id = ?", (enrollment_id,)) == [
        ("active",)
    ]

    assert post_stop(dashboard, cookie, lead_id, form(confirm="yes"), token=False).status == 403
    stopped = post_stop(dashboard, cookie, lead_id, form(confirm="yes"))

    assert stopped.status == 200
    done = Tree(stopped.text).find("section", id="lead-sequence")
    assert done.find("p", role="status").text == (
        "Sequence stopped. Its ready Drafts were cancelled; "
        "approved Drafts are blocked from sending."
    )
    assert done.find_all("form") == []
    assert world.sql(
        "SELECT status, stop_cause FROM enrollments WHERE id = ?", (enrollment_id,)
    ) == [("stopped", "operator_stop")]
    statuses = world.sql(
        "SELECT status FROM drafts WHERE id IN (?, ?) ORDER BY step_no", (step_1, step_2)
    )
    assert statuses == [("cancelled",), ("cancelled",)]
    [event] = world.events("enrollments", enrollment_id)[-1:]
    assert event[:3] == ("active", "stopped", "operator_stop") and event[4] == "operator"
    assert sequence_panel(dashboard, cookie, lead_id) is None  # nothing live: no panel

    again = post_stop(dashboard, cookie, lead_id, form(confirm="yes"))
    assert again.status == 200
    notice = Tree(again.text).find("p", role="alert")
    assert notice.text == "This Lead has no live Sequence."


def test_stop_sequence_leaves_an_approved_draft_to_the_gate(world: World) -> None:
    _, [step_1, _] = drafted(world)
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (step_1,))
    [(lead_id,)] = world.sql(
        "SELECT e.lead_id FROM drafts d JOIN enrollments e ON e.id = d.enrollment_id "
        "WHERE d.id = ?",
        (step_1,),
    )
    dashboard, cookie = dashboard_of(world)
    assert post_stop(dashboard, cookie, lead_id, form(confirm="yes")).status == 200
    # the approved Draft stays: enrollment_active now blocks it at the gate (5.2/6.10 cancel OMs)
    assert world.sql("SELECT status FROM drafts WHERE id = ?", (step_1,)) == [("approved",)]


def test_a_first_click_after_the_enrollment_ended_says_so(world: World) -> None:
    _, [step_1, _] = drafted(world)
    [(enrollment_id, lead_id)] = world.sql(
        "SELECT e.id, e.lead_id FROM drafts d JOIN enrollments e ON e.id = d.enrollment_id "
        "WHERE d.id = ?",
        (step_1,),
    )
    dashboard, cookie = dashboard_of(world)
    assert sequence_panel(dashboard, cookie, lead_id) is not None
    world.stop(enrollment_id)  # ended elsewhere while the page was open
    before = world.counts()
    reply = post_stop(dashboard, cookie, lead_id)
    assert reply.status == 200
    panel = Tree(reply.text).find("section", id="lead-sequence")
    assert panel.find("p", role="alert").text == "This Lead has no live Sequence."
    assert panel.find_all("form") == []
    assert world.counts() == before


def test_a_lead_without_a_live_enrollment_shows_no_stop_sequence(world: World) -> None:
    lead_id, _ = world.contact()
    dashboard, cookie = dashboard_of(world)
    assert sequence_panel(dashboard, cookie, lead_id) is None


@pytest.mark.parametrize(
    "body", [form(confirm="no"), form(extra="x"), form(confirm="yes", extra="x"), b"confirm=yes&confirm=yes"]
)
def test_a_malformed_stop_is_refused(world: World, body: bytes) -> None:
    lead_id, _ = world.contact()
    dashboard, cookie = dashboard_of(world)
    assert post_stop(dashboard, cookie, lead_id, body).status == 400


def test_an_unknown_lead_is_404_without_echoing_the_id(world: World) -> None:
    dashboard, cookie = dashboard_of(world)
    unknown = new_id()
    for lead_id in (unknown, "not-a-ulid"):
        reply = post_stop(dashboard, cookie, lead_id, form(confirm="yes"))
        assert reply.status == 404
        assert reply.text == "That Lead doesn't exist."
        assert lead_id not in reply.text
