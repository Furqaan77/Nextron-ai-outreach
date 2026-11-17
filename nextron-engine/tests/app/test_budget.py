"""The ``ops`` service's ``Budget`` through a real ``UsageMeter`` and a real ``Notify`` on a
temporary database, wired as ``nextron.main`` wires them (the meter's ``after_record`` is
``check_usage(units, budget)``): the 80% AI notice once, both AI notices from one jump, a quota
notice once per month, quiet limits of 0, a mid-month budget raise that repeats nothing, and
``status()`` in a read-only unit of work."""

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Budget
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.db.metering import UsageMeter
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory
from nextron.domain.alert import Channel
from nextron.domain.budget import (
    AiCall,
    AiState,
    ApiCall,
    BudgetClass,
    Limits,
    QuotaState,
    Threshold,
    allow,
)
from nextron.domain.settings_schema import Key
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
DECEMBER = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)


def no_backups() -> Any:
    """``build_job_types``'s ``backups`` where no ``backup_db`` job runs."""
    raise AssertionError("no backup_db job runs in these tests")


def no_restore_tests() -> Any:
    """``build_job_types``'s ``restore_tests`` where no ``restore_test`` job runs."""
    raise AssertionError("no restore_test job runs in these tests")


def no_recovery() -> Any:
    """``build_job_types``'s ``recovery`` where no ``check_recovery`` job runs."""
    raise AssertionError("no check_recovery job runs in these tests")


class World:
    """A meter, a budget and notify on one database, wired as ``nextron.main`` does."""

    def __init__(self, units: UnitOfWorkFactory, engine: Engine) -> None:
        self.units = units
        self.engine = engine
        self.clock = FrozenClock(T)
        self.alert = InMemoryAlert()
        job_types = main.build_job_types(
            alerts=lambda: self.notify,
            backups=no_backups,
            restore_tests=no_restore_tests,
            recovery=no_recovery,
        )
        self.notify = Notify(
            self.clock,
            JobQueue(self.clock, job_types),
            alert_ports={Channel.TELEGRAM: lambda: self.alert},
        )
        self.meter = UsageMeter(
            units, self.clock, after_record=lambda: main.check_usage(units, self.budget)
        )
        self.budget = Budget(self.clock, self.meter, self.notify)

    def spend(self, micros: int) -> None:
        self.meter.record(
            AiCall(
                "draft_email",
                "draft_email:v1:claude-opus-5-5",
                "claude-opus-5-5",
                input_tokens=1_000,
                output_tokens=200,
                cost_micros=micros,
            )
        )

    def set(self, key: str, value: object) -> None:
        with self.units.open():
            Setup(self.clock).set(key, value)

    def notices(self) -> list[tuple[str, str, str]]:
        return [
            (str(kind), str(subject), str(text))
            for kind, subject, text in self.query(
                "SELECT kind, subject, text FROM notifications ORDER BY created_at, id"
            )
        ]

    def query(self, statement: str) -> list[tuple[Any, ...]]:
        assert self.engine.url.database
        connection = sqlite3.connect(self.engine.url.database, isolation_level=None)
        try:
            return connection.execute(statement).fetchall()
        finally:
            connection.close()


@pytest.fixture
def world(units: UnitOfWorkFactory, db_engine: Engine) -> World:
    return World(units, db_engine)


WARNING_80 = (
    "ai_budget_warning",
    "ai_budget:2026-11",
    "AI spend at 80% of $20: $16 spent in 2026-11",
)


def test_ai_spend_reaching_80_percent_adds_one_notice_and_a_further_record_adds_none(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    world.spend(15_999_999)
    assert world.notices() == []

    world.spend(1)  # now exactly $16 of $20: 80%
    assert world.notices() == [WARNING_80]

    world.spend(500_000)
    world.meter.record(ApiCall("telegram", "send_message", ok=True))
    assert world.notices() == [WARNING_80]
    raised = [r for r in caplog.records if r.getMessage() == "usage_alert_raised"]
    assert [(r.kind, r.subject) for r in raised] == [  # type: ignore[attr-defined]
        ("ai_budget_warning", "ai_budget:2026-11")
    ]
    assert world.query("SELECT COUNT(*) FROM alerts") == [(0,)]  # dashboard only, no Telegram
    assert world.alert.attempts == []


def test_one_jump_past_80_and_100_percent_adds_both_notices(world: World) -> None:
    world.spend(21_000_000)

    assert world.notices() == [
        (
            "ai_budget_warning",
            "ai_budget:2026-11",
            "AI spend at 80% of $20: $21 spent in 2026-11",
        ),
        (
            "ai_budget_reached",
            "ai_budget:2026-11",
            "AI budget reached: discovery, enrichment and re-checks stopped "
            "($21 of $20 spent in 2026-11)",
        ),
    ]
    with world.units.open(read_only=True):
        status = world.budget.status()
    assert status.ai.state is AiState.REACHED
    assert status.stopped == ("AI budget reached",)


def test_a_quota_reached_adds_its_notice_once_a_month(world: World) -> None:
    hunter = (
        "quota_reached",
        "hunter:2026-11",
        "Hunter quota reached: enrichment stopped (50 of 50 credits used in 2026-11)",
    )

    world.meter.record(ApiCall("hunter", "find", ok=True, count=49))  # 98 units: 49 credits
    assert world.notices() == []
    world.meter.record(ApiCall("hunter", "verify", ok=True, count=2))  # 100 units: 50 credits
    assert world.notices() == [hunter]
    world.meter.record(ApiCall("hunter", "find", ok=True))
    assert world.notices() == [hunter]  # once

    world.clock.set(DECEMBER)
    world.meter.record(ApiCall("hunter", "find", ok=True, count=50))

    assert world.notices() == [
        hunter,
        (
            "quota_reached",
            "hunter:2026-12",
            "Hunter quota reached: enrichment stopped (50 of 50 credits used in 2026-12)",
        ),
    ]


def test_a_quota_of_zero_stays_quiet_and_refuses_blockable_work(world: World) -> None:
    world.meter.record(ApiCall("telegram", "send_message", ok=True))

    assert world.notices() == []
    with world.units.open(read_only=True):
        status = world.budget.status()
        usage = world.meter.snapshot(T)
    places_pro = next(line for line in status.quotas if line.spec.name == "places_pro")
    assert (places_pro.used, places_pro.limit, places_pro.state) == (0, 0, QuotaState.OFF)
    assert status.stopped == ()
    limits = status_limits(world)
    assert allow(BudgetClass.BLOCKABLE, usage, limits, quota="places_pro") is False
    assert allow(BudgetClass.ESSENTIAL, usage, limits, quota="places_pro") is True
    assert allow(BudgetClass.BLOCKABLE, usage, limits, quota="places_enterprise") is True


def status_limits(world: World) -> Limits:
    with world.units.open(read_only=True):
        return setup_queries.budget_limits()


def test_raising_the_budget_mid_month_repeats_no_notice(world: World) -> None:
    world.spend(16_000_000)
    assert world.notices() == [WARNING_80]

    world.set(Key.AI_BUDGET_MONTHLY, 40_000_000)  # $40: $16 is now 40%
    world.spend(17_000_000)  # $33: past 80% of $40 again
    assert world.notices() == [WARNING_80]  # the month's warning came already

    world.spend(7_000_000)  # $40: the raised budget is reached
    assert world.notices() == [
        WARNING_80,
        (
            "ai_budget_reached",
            "ai_budget:2026-11",
            "AI budget reached: discovery, enrichment and re-checks stopped "
            "($40 of $40 spent in 2026-11)",
        ),
    ]


def test_check_returns_only_the_notices_it_added(world: World) -> None:
    world.meter.record(ApiCall("prospeo", "find", ok=True, count=100))
    prospeo = Threshold(
        "quota_reached",
        "prospeo:2026-11",
        "Prospeo quota reached: enrichment stopped (100 of 100 credits used in 2026-11)",
    )
    assert world.notices() == [(prospeo.kind, prospeo.subject, prospeo.text)]

    with world.units.open():
        assert world.budget.check() == ()


def test_the_settings_set_the_limits_the_status_reads(world: World) -> None:
    world.set(Key.QUOTA_HUNTER_CREDITS, 3)
    world.set(Key.QUOTA_PLACES_PRO, 10)
    world.set(Key.AI_BUDGET_ALERT_PCT, 50)
    world.meter.record(ApiCall("hunter", "verify", ok=True, count=5))  # 2.5 credits
    world.spend(10_000_000)  # $10 of $20: 50%

    with world.units.open(read_only=True):
        status = world.budget.status()

    by_name = {line.spec.name: line for line in status.quotas}
    assert (by_name["hunter"].used, by_name["hunter"].limit) == (5, 6)  # stored doubled
    assert by_name["places_pro"].limit == 10
    assert (status.ai.spent, status.ai.budget, status.ai.alert_pct) == (
        10_000_000,
        20_000_000,
        50,
    )
    assert status.ai.state is AiState.WARNING
    assert status.month == "2026-11"
    assert world.notices() == [
        (
            "ai_budget_warning",
            "ai_budget:2026-11",
            "AI spend at 50% of $20: $10 spent in 2026-11",
        )
    ]


def test_status_runs_in_a_read_only_unit_and_check_needs_a_writing_one(world: World) -> None:
    with world.units.open(read_only=True):
        world.budget.status()
        with pytest.raises(UnitOfWorkError):
            world.budget.check()
