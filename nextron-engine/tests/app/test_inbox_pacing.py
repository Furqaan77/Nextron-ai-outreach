"""``Sending``'s cold-send pacing (Story 6.11, FR-31, AD-11, NFR-22) on a temporary database at
head, with ``FrozenClock`` and a fixed gap source: ``record_cold_send`` stores ``last_sent_at``
and ``next_send_at`` (one gap later); ``repace_after_start`` re-paces every listed inbox from the
later of its ``last_sent_at`` and process start (with and without a last send), skips a
``removed`` inbox and logs its count; the startup step ``repace_cold_sends``; ``register_inbox``
pacing a new inbox one gap after its registration; and ``dispatch_state`` reading its facts (on
the ``sender_timezone`` date, for the inbox's own id), failing closed on the seams' real
``None``s, a missing, removed or not-``active`` inbox and an unset timezone."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import replies as replies_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.sending.api import Sending
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.sending_policy import DispatchCandidate, DispatchPlan, pick_dispatch
from tests.fakes.clock import FrozenClock

START = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)  # a Wednesday; London is UTC
LOGGER = "nextron.app.sending.api"
WARMUP = ("2026-10-01", "manual", "20", "0", "5", "30")


class Fixed:
    """A gap source that always answers ``minutes``."""

    def __init__(self, minutes: float) -> None:
        self.minutes = minutes

    def uniform(self, a: float, b: float) -> float:
        assert (a, b) == (4.0, 12.0)
        return self.minutes


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock

    def sending(self, minutes: float = 6.0) -> Sending:
        return Sending(self.clock, env="prod", gap_source=Fixed(minutes))

    def register(self, address: str) -> str:
        with self.units.open():
            return self.sending().register_inbox(address, "Main").inbox_id

    def pacing(self, inbox_id: str) -> tuple[Any, ...]:
        with self.engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT next_send_at, last_sent_at FROM sending_inboxes WHERE id = ?", (inbox_id,)
            ).one()
        return tuple(row)

    def sql(self, statement: str) -> None:
        with self.engine.connect() as connection:
            connection.exec_driver_sql(statement)
            connection.commit()

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(START)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    found = World(units, db_engine, clock)
    with units.open():
        Setup(clock).save({"sender_name": "Furqaan Ahmed", "sender_timezone": "Europe/London"})
    return found


def test_a_new_inbox_is_paced_one_gap_after_its_registration(world: World) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    assert world.pacing(inbox) == ("2026-11-18T13:06:00.000000Z", None)
    [view] = world.read(sending_queries.inboxes)
    assert (view.next_send_at, view.last_sent_at) == (START + timedelta(minutes=6), None)


def test_an_inbox_registered_after_start_is_paced_past_process_start(world: World) -> None:
    sending = world.sending(4.0)  # the engine's Sending, built at start
    world.clock.advance(timedelta(hours=2))
    with world.units.open():
        inbox = sending.register_inbox("furqaan@nextron-ai.com", "Main").inbox_id
    [view] = world.read(sending_queries.inboxes)
    assert view.id == inbox
    assert view.next_send_at is not None
    assert view.next_send_at > sending.process_started_at
    assert view.next_send_at == START + timedelta(hours=2, minutes=4)


def test_record_cold_send_stores_the_send_and_the_next_one_a_gap_later(world: World) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    sent_at = START + timedelta(minutes=30)
    assert Apply(world, world.sending(7.5))((inbox, sent_at)) == [sent_at + timedelta(minutes=7.5)]
    assert world.pacing(inbox) == (
        "2026-11-18T13:37:30.000000Z",
        "2026-11-18T13:30:00.000000Z",
    )
    [view] = world.read(sending_queries.inboxes)
    assert view.next_send_at == sent_at + timedelta(minutes=7.5)
    assert view.last_sent_at == sent_at


def test_record_cold_send_for_no_inbox_writes_nothing(world: World) -> None:
    assert Apply(world, world.sending())(("01JD3QZ8X5V9K2M7N4P6R8T0D0", START)) == [None]


def test_record_cold_send_refuses_a_naive_time(world: World) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    with pytest.raises(ValueError):
        Apply(world, world.sending())((inbox, datetime(2026, 11, 18, 13, 0)))  # noqa: DTZ001
    assert world.pacing(inbox) == ("2026-11-18T13:06:00.000000Z", None)


def test_repace_without_a_last_send_paces_from_process_start(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    sending = world.sending(5.0)  # process start is START
    world.clock.advance(timedelta(seconds=40))  # the startup step runs a little later
    caplog.set_level(logging.INFO, logger=LOGGER)
    with world.units.open():
        assert sending.repace_after_start() == 1
    assert world.pacing(inbox) == ("2026-11-18T13:05:00.000000Z", None)
    [record] = [r for r in caplog.records if r.name == LOGGER and r.msg == "cold_sends_repaced"]
    assert record.__dict__["count"] == 1


def test_repace_paces_from_the_later_of_the_last_send_and_process_start(world: World) -> None:
    early = world.register("furqaan@nextron-ai.com")
    late = world.register("furqaan@nextron-mail.com")
    removed = world.register("furqaan@nextron-hq.com")
    Apply(world, world.sending(4.0))(
        (early, START - timedelta(minutes=2)),  # 12:58, before the start
        (late, START + timedelta(minutes=3)),  # a last send after the start
        (removed, START - timedelta(minutes=1)),
    )
    world.sql(f"UPDATE sending_inboxes SET status = 'removed' WHERE id = '{removed}'")
    removed_pace = world.pacing(removed)
    sending = world.sending(9.0)
    with world.units.open():
        assert sending.repace_after_start() == 2
    assert world.pacing(early) == ("2026-11-18T13:09:00.000000Z", "2026-11-18T12:58:00.000000Z")
    assert world.pacing(late) == ("2026-11-18T13:12:00.000000Z", "2026-11-18T13:03:00.000000Z")
    assert world.pacing(removed) == removed_pace  # a removed inbox is left alone


def test_the_startup_step_repaces_in_its_own_unit_of_work(world: World) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    main.repace_cold_sends(world.units, world.sending(12.0))
    assert world.pacing(inbox) == ("2026-11-18T13:12:00.000000Z", None)


def test_process_start_is_construction_time(world: World) -> None:
    sending = world.sending()
    world.clock.advance(timedelta(minutes=3))
    assert sending.process_started_at == START


def test_dispatch_state_fails_closed_on_the_seams_real_nones(world: World) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    world.sql(f"UPDATE sending_inboxes SET status = 'active' WHERE id = '{inbox}'")
    sending = world.sending()
    with world.units.open():
        sending.repace_after_start()
    state = world.read(lambda: sending.dispatch_state(inbox, START + timedelta(hours=1)))
    assert state.inbox_id == inbox
    assert state.recovery_mode is False
    assert state.process_started_at == START
    assert state.next_send_at == START + timedelta(minutes=6)
    assert state.last_sync_completed_at is None  # until Story 6.8
    assert state.cold_sent_today is None  # until Story 6.10
    assert state.cold_cap == 0  # no warm-up recorded
    reply = DispatchCandidate(
        "reply", "reply", None, START, None, "Europe/London", _Market(), None, None
    )
    assert pick_dispatch(state, [reply], START + timedelta(hours=1)) == DispatchPlan(None)


def test_dispatch_state_reads_each_fact(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    world.sql(f"UPDATE sending_inboxes SET status = 'active' WHERE id = '{inbox}'")
    sending = world.sending()
    with world.units.open():
        sending.record_warmup(inbox, *WARMUP)
    with world.units.open():
        sending.repace_after_start()
    synced = START + timedelta(minutes=2)
    days: list[object] = []

    def cold_sent_on(inbox_id: str, local_date: object) -> int:
        days.append((inbox_id, local_date))
        return 4

    monkeypatch.setattr(replies_queries, "last_sync_completed", lambda inbox_id: synced)
    monkeypatch.setattr(sending_queries, "cold_sent_on", cold_sent_on)
    monkeypatch.setattr(ops_queries, "recovery_mode", lambda: True)
    late = datetime(2026, 11, 18, 23, 30, tzinfo=UTC)  # still 18 November in London
    state = world.read(lambda: sending.dispatch_state(inbox, late))
    assert (state.recovery_mode, state.last_sync_completed_at) == (True, synced)
    assert state.cold_sent_today == 4
    assert state.cold_cap == 30  # warm level 20, ramped weekly to 30 after 48 days
    assert [day for _, day in days] == [late.date()]


def test_dispatch_state_reads_the_sender_timezone_date_and_the_inbox_own_id(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = world.register("furqaan@nextron-mail.com")
    inbox = world.register("furqaan@nextron-ai.com")
    world.sql("UPDATE sending_inboxes SET status = 'active'")
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/New_York"})
    sending = world.sending()
    calls: list[tuple[str, object]] = []

    def cold_sent_on(inbox_id: str, local_date: object) -> int:
        calls.append(("cold_sent_on", (inbox_id, local_date)))
        return 0

    def cold_daily_caps(local_date: object) -> dict[str, int]:
        calls.append(("cold_daily_caps", local_date))
        return {inbox: 7, other: 9}

    def last_sync_completed(inbox_id: str) -> None:
        calls.append(("last_sync_completed", inbox_id))

    monkeypatch.setattr(sending_queries, "cold_sent_on", cold_sent_on)
    monkeypatch.setattr(sending_queries, "cold_daily_caps", cold_daily_caps)
    monkeypatch.setattr(replies_queries, "last_sync_completed", last_sync_completed)
    now = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)  # 22:00 on 18 November in New York
    state = world.read(lambda: sending.dispatch_state(inbox, now))
    assert sorted(calls, key=lambda call: call[0]) == [
        ("cold_daily_caps", date(2026, 11, 18)),
        ("cold_sent_on", (inbox, date(2026, 11, 18))),
        ("last_sync_completed", inbox),
    ]
    assert (state.inbox_id, state.cold_cap) == (inbox, 7)


@pytest.mark.parametrize("status", ["connecting", "paused", "auth_failed"])
def test_dispatch_state_for_an_inbox_that_is_not_active_picks_nothing(
    world: World, status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    world.sql(f"UPDATE sending_inboxes SET status = '{status}' WHERE id = '{inbox}'")
    synced = START + timedelta(minutes=2)
    monkeypatch.setattr(replies_queries, "last_sync_completed", lambda inbox_id: synced)
    monkeypatch.setattr(sending_queries, "cold_sent_on", lambda inbox_id, local_date: 0)
    sending = world.sending()
    now = START + timedelta(hours=1)
    state = world.read(lambda: sending.dispatch_state(inbox, now))
    assert state.recovery_mode is True and state.last_sync_completed_at is None
    assert (state.next_send_at, state.cold_sent_today, state.cold_cap) == (None, None, 0)
    reply = DispatchCandidate(
        "reply", "reply", None, START, None, "Europe/London", _Market(), None, None
    )
    assert pick_dispatch(state, [reply], now) == DispatchPlan(None)


def test_dispatch_state_for_no_inbox_or_no_timezone_picks_nothing(world: World) -> None:
    inbox = world.register("furqaan@nextron-ai.com")
    sending = world.sending()
    missing = world.read(lambda: sending.dispatch_state("01JD3QZ8X5V9K2M7N4P6R8T0D0", START))
    assert missing.recovery_mode is True and missing.last_sync_completed_at is None
    assert (missing.next_send_at, missing.cold_sent_today, missing.cold_cap) == (None, None, 0)
    world.sql("DELETE FROM settings WHERE key = 'sender_timezone'")
    unset = world.read(lambda: sending.dispatch_state(inbox, START))
    assert unset.recovery_mode is True and unset.last_sync_completed_at is None
    world.sql(f"UPDATE sending_inboxes SET status = 'removed' WHERE id = '{inbox}'")
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "Europe/London"})
    removed = world.read(lambda: sending.dispatch_state(inbox, START))
    assert removed.recovery_mode is True
    assert removed.next_send_at is None
    assert removed.cold_cap == 0
    with pytest.raises(ValueError):
        world.read(lambda: sending.dispatch_state(inbox, datetime(2026, 11, 18, 13, 0)))  # noqa: DTZ001


class _Market:
    unknown_timezone = "Europe/London"
    unknown_send_window = None


class Apply:
    """A test-local stand-in for Story 6.10's apply unit: one use case, one transaction."""

    def __init__(self, world: World, sending: Sending) -> None:
        self._world = world
        self._sending = sending

    @use_case
    def _run(self, sends: tuple[tuple[str, datetime], ...]) -> list[datetime | None]:
        with current().transaction():
            return [self._sending.record_cold_send(inbox, at) for inbox, at in sends]

    def __call__(self, *sends: tuple[str, datetime]) -> list[datetime | None]:
        with self._world.units.open():
            return self._run(sends)
