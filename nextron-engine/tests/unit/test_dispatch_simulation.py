"""The UJ-5 day (Story 6.11, FR-31, AD-11, NFR-22): cold sends paced inside Send Windows, with a
catch-up and no burst after the PC was off.

A frozen clock ticks each minute over Wednesday 18 and Thursday 19 November 2026 (London is UTC,
New York UTC-5). Three inboxes, each with a cold cap of 30, hold a backlog of Step 1s (UK and US
recipients, the US ones with an unknown zone, so 11:00 to 17:00 New York) and follow-ups, all
due from 08:00 London. The PC is off from 09:00 to 13:00 London on Wednesday; at 13:00 the engine
restarts: a new process start, the re-pace (``repaced_next_send_at``) and a fresh mailbox sync
before anything goes. Every 2 minutes while the PC is on, a sync completes.

A test-local stand-in for Story 6.10 runs each minute: per inbox, ``pick_dispatch`` on the
inbox's state and its queued OMs; a picked OM goes through ``InMemoryMail`` (the ``MailPort``
fake), is removed from the queue, and the inbox's ``next_send_at`` becomes
``next_send_after(sent_at, source)`` with a seeded ``random.Random``.

The acceptance checks: every cold send per inbox is at least 4 minutes after the one before (the
first after the wake included), no inbox exceeds 30 a day, nothing goes outside its recipient's
window or while the PC is off, no OM is sent twice, and the morning's missed sends go out after
13:00 or in Thursday's window."""

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.sending_policy import (
    GAP_MIN,
    DispatchCandidate,
    InboxDispatchState,
    in_send_window,
    next_send_after,
    pick_dispatch,
    repaced_next_send_at,
)
from nextron.ports.mail import MailMessage
from tests.fakes.mail import InMemoryMail

LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
DAY_1, DAY_2 = date(2026, 11, 18), date(2026, 11, 19)
FIRST_START = datetime(2026, 11, 18, 7, 0, tzinfo=UTC)  # the PC is on from 07:00 London
OFF_FROM = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
OFF_UNTIL = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)  # the restart
END = datetime(2026, 11, 19, 23, 59, tzinfo=UTC)
SYNC_EVERY = 2  # minutes
CAP = 30
INBOXES = ("inbox-a", "inbox-b", "inbox-c")
SEED = 611


def shipped_market(code: str) -> MarketDef:
    text = (main.SEED_DIR / main.MARKETS_FILE_NAME).read_text(encoding="utf-8")
    [found] = [market for market in validate_markets(seed_yaml.parse(text)) if market.code == code]
    return found


UK, US = shipped_market("uk"), shipped_market("us")


@dataclass
class Inbox:
    """One inbox as Story 6.10 will hold it: the stored pace, the day's count and the queue."""

    inbox_id: str
    queue: list[DispatchCandidate]
    next_send_at: datetime | None = None
    last_sent_at: datetime | None = None
    sent_on: dict[date, int] = field(default_factory=dict)
    sends: list[tuple[datetime, DispatchCandidate]] = field(default_factory=list)


def backlog(inbox_id: str) -> list[DispatchCandidate]:
    """40 queued cold OMs: 30 Step 1s and 10 follow-ups, half UK, half US, all due 08:00."""
    due = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)
    approved = datetime(2026, 11, 17, 16, 0, tzinfo=UTC)
    found = []
    for number in range(40):
        uk = number % 2 == 0
        step = 2 if number < 10 else 1
        found.append(
            DispatchCandidate(
                om_id=f"{inbox_id}-om-{number:02d}",
                kind="step",
                step=step,
                approved_at=approved,
                due_at=due + timedelta(minutes=number),
                contact_tz="Europe/London" if uk else None,
                market=UK if uk else US,
            )
        )
    return found


def london_day(moment: datetime) -> date:
    return moment.astimezone(LONDON).date()


def simulate() -> tuple[dict[str, Inbox], InMemoryMail, list[datetime]]:
    gaps = random.Random(SEED)
    mail = InMemoryMail()
    inboxes = {inbox_id: Inbox(inbox_id, backlog(inbox_id)) for inbox_id in INBOXES}
    process_started_at = FIRST_START
    for inbox in inboxes.values():  # the first start's re-pace
        inbox.next_send_at = repaced_next_send_at(inbox.last_sent_at, process_started_at, gaps)
    last_sync: datetime | None = None
    sent_times: list[datetime] = []
    now = FIRST_START
    while now <= END:
        pc_on = not (OFF_FROM <= now < OFF_UNTIL)
        if not pc_on:
            now += timedelta(minutes=1)
            continue
        if now == OFF_UNTIL:  # the restart: a new process, re-paced, no sync yet
            process_started_at = now
            last_sync = None
            for inbox in inboxes.values():
                inbox.next_send_at = repaced_next_send_at(
                    inbox.last_sent_at, process_started_at, gaps
                )
        minutes_up = int((now - process_started_at).total_seconds() // 60)
        if minutes_up % SYNC_EVERY == 0 and minutes_up > 0:
            last_sync = now  # a sync completes every 2 minutes after the start
        for inbox in inboxes.values():
            state = InboxDispatchState(
                inbox_id=inbox.inbox_id,
                recovery_mode=False,
                process_started_at=process_started_at,
                last_sync_completed_at=last_sync,
                next_send_at=inbox.next_send_at,
                cold_sent_today=inbox.sent_on.get(london_day(now), 0),
                cold_cap=CAP,
            )
            plan = pick_dispatch(state, inbox.queue, now)
            if plan.om_id is None:
                continue
            [picked] = [om for om in inbox.queue if om.om_id == plan.om_id]
            mail.send(MailMessage(f"{picked.om_id}@example.com", "Hello", "Plain text."))
            inbox.queue.remove(picked)
            inbox.sends.append((now, picked))
            inbox.sent_on[london_day(now)] = inbox.sent_on.get(london_day(now), 0) + 1
            inbox.last_sent_at = now
            inbox.next_send_at = next_send_after(now, gaps)
            sent_times.append(now)
        now += timedelta(minutes=1)
    return inboxes, mail, sent_times


@dataclass(frozen=True)
class Day:
    """The simulation's outcome, run once for the module."""

    inboxes: dict[str, Inbox]
    mail: InMemoryMail
    sent_times: list[datetime]


@pytest.fixture(scope="module")
def day() -> Day:
    return Day(*simulate())


def test_every_om_goes_once_through_the_fake(day: Day) -> None:
    sent = [message.recipient for message in day.mail.sent]
    assert len(sent) == len(set(sent)) == 3 * 40  # every OM, none twice
    assert all(not inbox.queue for inbox in day.inboxes.values())


def test_cold_sends_per_inbox_are_at_least_4_minutes_apart(day: Day) -> None:
    for inbox in day.inboxes.values():
        times = [at for at, _ in inbox.sends]
        assert times == sorted(times)
        assert all(later - earlier >= GAP_MIN for earlier, later in zip(times, times[1:]))


def test_the_first_send_after_the_wake_waits_a_gap_and_a_sync(day: Day) -> None:
    for inbox in day.inboxes.values():
        after = [at for at, _ in inbox.sends if at >= OFF_UNTIL]
        assert after[0] >= OFF_UNTIL + GAP_MIN
        assert after[0] >= OFF_UNTIL + timedelta(minutes=SYNC_EVERY)


def test_no_inbox_exceeds_its_cap_on_any_day(day: Day) -> None:
    for inbox in day.inboxes.values():
        assert all(count <= CAP for count in inbox.sent_on.values())
        assert max(inbox.sent_on.values()) == CAP  # the cap binds on the first day


def test_nothing_goes_outside_a_window_or_while_the_pc_is_off(day: Day) -> None:
    for inbox in day.inboxes.values():
        for at, om in inbox.sends:
            assert not OFF_FROM <= at < OFF_UNTIL
            assert in_send_window(om.contact_tz, om.market, at)
            if om.contact_tz is None:  # a US recipient with an unknown zone: 11:00 to 17:00 ET
                local = at.astimezone(NEW_YORK).time()
                assert time(11, 0) <= local < time(17, 0)


def test_follow_ups_go_before_step_1s(day: Day) -> None:
    for inbox in day.inboxes.values():
        uk = [om.step for _, om in inbox.sends if om.contact_tz is not None]
        assert uk[:5] == [2] * 5  # the UK follow-ups go first once their window opens


def test_the_mornings_missed_sends_catch_up_after_13_00_or_next_day_without_a_burst(
    day: Day,
) -> None:
    for inbox in day.inboxes.values():
        morning = [at for at, _ in inbox.sends if at < OFF_FROM]
        rest = [at for at, _ in inbox.sends if at >= OFF_FROM]
        assert morning  # some went before the PC turned off
        assert all(at >= OFF_UNTIL for at in rest)
        assert any(london_day(at) == DAY_2 for at in rest)  # the cap rolled the rest over
        assert all(london_day(at) in (DAY_1, DAY_2) for at in rest)
    assert len(day.sent_times) == 3 * 40  # every OM went, over the two days
