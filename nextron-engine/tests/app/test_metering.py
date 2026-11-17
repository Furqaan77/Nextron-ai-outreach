"""``nextron.db.metering.UsageMeter`` on a temporary database at head: an ``ApiCall`` and an
``AiCall`` each become one row stamped with the clock, every record commits on its own (it
survives the caller's rolled-back apply), a record inside the caller's transaction writes
nothing and returns at once, the month's snapshot groups by provider, operation and SKU within
the UTC month's bounds (a read-only unit included), the ``after_record`` hook runs after the
row is committed, and neither a failing hook nor a failing write ever raises."""

import contextvars
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from nextron.db.metering import UsageMeter
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current, in_transaction, use_case
from nextron.domain.budget import AiCall, ApiCall, ApiUse, Usage
from nextron.ports.usage_meter import UsageMeterPort
from tests.fakes.clock import FrozenClock

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
METER_LOGGER = "nextron.db.metering"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T)


def query(engine: Engine, statement: str) -> list[tuple[Any, ...]]:
    """Rows read outside the engine, through a plain ``sqlite3`` connection."""
    assert engine.url.database
    connection = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        return connection.execute(statement).fetchall()
    finally:
        connection.close()


def meter_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == METER_LOGGER]


def test_the_meter_implements_the_port(units: UnitOfWorkFactory, clock: FrozenClock) -> None:
    assert isinstance(UsageMeter(units, clock), UsageMeterPort)


# Record API and Record AI -----------------------------------------------------------------------


def test_an_api_call_becomes_one_row_with_its_units_and_the_clocks_time(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    meter = UsageMeter(units, clock)

    meter.record(ApiCall("hunter", "find", ok=True))
    clock.advance(timedelta(seconds=1))
    meter.record(ApiCall("hunter", "verify", ok=True))
    meter.record(ApiCall("hunter", "domain_search", ok=False, error_code="hunter_unavailable"))
    meter.record(ApiCall("places", "text_search", ok=True, sku="pro"))
    meter.record(ApiCall("places", "text_search", ok=True, sku="enterprise", count=3))

    assert query(
        db_engine,
        "SELECT provider, operation, sku, units, ok, error_code, at FROM api_usage ORDER BY id",
    ) == [
        ("hunter", "find", None, 2, 1, None, "2026-11-17T09:00:00.000000Z"),
        ("hunter", "verify", None, 1, 1, None, "2026-11-17T09:00:01.000000Z"),
        (
            "hunter",
            "domain_search",
            None,
            2,
            0,
            "hunter_unavailable",
            "2026-11-17T09:00:01.000000Z",
        ),
        ("places", "text_search", "pro", 1, 1, None, "2026-11-17T09:00:01.000000Z"),
        ("places", "text_search", "enterprise", 3, 1, None, "2026-11-17T09:00:01.000000Z"),
    ]
    assert query(db_engine, "SELECT COUNT(*) FROM events") == [(0,)]  # no events row
    assert query(db_engine, "SELECT COUNT(*) FROM ai_usage") == [(0,)]


def test_an_ai_call_becomes_one_row_with_every_field(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    meter = UsageMeter(units, clock)

    meter.record(
        AiCall(
            "classify_reply",
            "classify_reply:v2:claude-opus-5-5",
            "claude-opus-5-5",
            input_tokens=1_500,
            output_tokens=40,
            cache_read_tokens=1_000,
            cache_write_tokens=200,
            iterations=2,
            batch=True,
            cost_micros=1_234,
            refused=True,
            error_code="ai_refused",
        )
    )

    assert query(
        db_engine,
        "SELECT route, route_tuple, served_model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_write_tokens, iterations, batch, cost_micros, refused, "
        "error_code, at FROM ai_usage",
    ) == [
        (
            "classify_reply",
            "classify_reply:v2:claude-opus-5-5",
            "claude-opus-5-5",
            1_500,
            40,
            1_000,
            200,
            2,
            1,
            1_234,
            1,
            "ai_refused",
            "2026-11-17T09:00:00.000000Z",
        )
    ]
    assert query(db_engine, "SELECT COUNT(*) FROM api_usage") == [(0,)]


def test_the_ids_are_ulids(units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock) -> None:
    UsageMeter(units, clock).record(ApiCall("telegram", "send_message", ok=True))

    [(row_id,)] = query(db_engine, "SELECT id FROM api_usage")
    assert isinstance(row_id, str) and len(row_id) == 26


# Own commit -------------------------------------------------------------------------------------


class ApplyFailed(Exception):
    pass


def test_a_record_commits_on_its_own_and_outlives_the_callers_rolled_back_apply(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    meter = UsageMeter(units, clock)

    @use_case
    def prepare_call_apply() -> None:
        with current().transaction():  # prepare
            current().session.execute(*_setting_insert("quota_hunter_credits", "40"))
        meter.record(ApiCall("hunter", "find", ok=True))  # the port call, between the two
        with current().transaction():  # apply, which fails
            current().session.execute(*_setting_insert("quota_prospeo_credits", "7"))
            raise ApplyFailed

    with pytest.raises(ApplyFailed), units.open():
        prepare_call_apply()

    assert query(db_engine, "SELECT provider, operation, units FROM api_usage") == [
        ("hunter", "find", 2)
    ]
    assert query(db_engine, "SELECT key FROM settings") == [("quota_hunter_credits",)]


def _setting_insert(key: str, value: str) -> tuple[Any, dict[str, object]]:
    return (
        text("INSERT INTO settings (id, key, value, updated_at) VALUES (:id, :key, :value, :at)"),
        {
            "id": f"01JD3QZ8X5V9K2M7N4P6R8T{len(key):03d}",
            "key": key,
            "value": value,
            "at": "2026-11-17T09:00:00.000000Z",
        },
    )


# Inside a transaction ---------------------------------------------------------------------------


def test_a_record_inside_the_callers_transaction_writes_nothing_and_returns_at_once(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    hooked: list[bool] = []
    meter = UsageMeter(units, clock, after_record=lambda: hooked.append(True))
    elapsed: list[float] = []

    @use_case
    def record_inside() -> None:
        with current().transaction():  # holds the write lock: a write would wait 5 s
            assert in_transaction()
            started = time.monotonic()
            meter.record(ApiCall("hunter", "find", ok=True))
            elapsed.append(time.monotonic() - started)

    with units.open():
        record_inside()

    assert elapsed[0] < 0.5  # it never waited for the lock
    assert query(db_engine, "SELECT COUNT(*) FROM api_usage") == [(0,)]
    assert hooked == []
    [failed] = meter_records(caplog)
    assert (failed.getMessage(), failed.levelno) == ("usage_record_failed", logging.ERROR)
    assert (failed.provider, failed.operation, failed.code) == (  # type: ignore[attr-defined]
        "hunter",
        "find",
        "inside_transaction",
    )


def test_a_record_inside_a_read_only_units_read_writes_nothing(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    meter = UsageMeter(units, clock)

    with units.open(read_only=True):
        assert not in_transaction()  # nothing read yet: no transaction
        meter.snapshot(T)  # the first read begins the read transaction
        assert in_transaction()
        meter.record(ApiCall("places", "text_search", ok=True, sku="pro"))

    assert query(db_engine, "SELECT COUNT(*) FROM api_usage") == [(0,)]
    [failed] = meter_records(caplog)
    assert failed.code == "inside_transaction"  # type: ignore[attr-defined]


def test_a_record_between_the_callers_transactions_is_written(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    meter = UsageMeter(units, clock)

    with units.open():
        assert not in_transaction()
        meter.record(ApiCall("google_oauth", "token_refresh", ok=True))
        assert current().read_only is False  # the caller's unit is still open and its own

    assert query(db_engine, "SELECT provider, operation FROM api_usage") == [
        ("google_oauth", "token_refresh")
    ]


def test_in_transaction_is_false_outside_any_unit_and_on_another_thread(
    units: UnitOfWorkFactory,
) -> None:
    assert in_transaction() is False

    seen: list[bool] = []

    @use_case
    def look() -> None:
        with current().transaction():
            seen.append(in_transaction())
            seen.append(contextvars.Context().run(in_transaction))  # a fresh context
            # The same context (a copy holds the open unit), on another thread.
            copied = contextvars.copy_context()
            other = threading.Thread(target=lambda: seen.append(copied.run(in_transaction)))
            other.start()
            other.join(timeout=5)
            assert not other.is_alive()
            seen.append(copied.run(in_transaction))  # the copy on this thread: still true

    with units.open():
        look()

    assert seen == [True, False, False, True]


# Failures never raise ---------------------------------------------------------------------------


def test_a_write_that_fails_is_logged_and_never_raised(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    query(db_engine, "DROP TABLE ai_usage")
    hooked: list[bool] = []
    meter = UsageMeter(units, clock, after_record=lambda: hooked.append(True))

    meter.record(
        AiCall(
            "draft", "draft:v1", "claude-opus-5-5", input_tokens=1, output_tokens=1, cost_micros=1
        )
    )

    [failed] = meter_records(caplog)
    assert failed.getMessage() == "usage_record_failed"
    shown = (failed.route, failed.code)  # type: ignore[attr-defined]
    assert shown == ("draft", "OperationalError")
    assert not hasattr(failed, "operation")
    assert hooked == []  # nothing was recorded, so nothing to check


def test_anything_but_a_call_is_logged_and_never_raised(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    UsageMeter(units, clock).record("hunter find")  # type: ignore[arg-type]

    [failed] = meter_records(caplog)
    assert failed.code == "not_a_call"  # type: ignore[attr-defined]
    assert query(db_engine, "SELECT COUNT(*) FROM api_usage") == [(0,)]


# The hook ---------------------------------------------------------------------------------------


def test_the_hook_runs_after_the_row_is_committed_in_a_fresh_context(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    seen: list[object] = []

    def hook() -> None:
        seen.append(query(db_engine, "SELECT provider FROM api_usage"))  # committed already
        seen.append(in_transaction())
        with units.open(read_only=True):  # no unit of work is open in its context
            seen.append(UsageMeter(units, clock).snapshot(T).units("hunter"))

    meter = UsageMeter(units, clock, after_record=hook)

    with units.open():  # the caller's unit stays open around the record
        meter.record(ApiCall("hunter", "verify", ok=True))

    assert seen == [[("hunter",)], False, 1]


def test_a_hook_that_raises_is_logged_and_the_row_stays(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def hook() -> None:
        raise RuntimeError("the threshold check failed")

    meter = UsageMeter(units, clock, after_record=hook)

    meter.record(ApiCall("prospeo", "find", ok=True))

    assert query(db_engine, "SELECT provider FROM api_usage") == [("prospeo",)]
    [failed] = meter_records(caplog)
    assert (failed.getMessage(), failed.levelno) == ("usage_alerts_failed", logging.ERROR)
    assert failed.exc_info is not None  # with the traceback


# snapshot ---------------------------------------------------------------------------------------


def record_at(meter: UsageMeter, clock: FrozenClock, at: datetime, call: ApiCall | AiCall) -> None:
    clock.set(at)
    meter.record(call)


def test_the_snapshot_groups_this_utc_months_rows_by_provider_operation_and_sku(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    meter = UsageMeter(units, clock)
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = datetime(2026, 12, 1, tzinfo=UTC)
    tiny = timedelta(microseconds=1)
    calls: list[tuple[datetime, ApiCall | AiCall]] = [
        (start - tiny, ApiCall("hunter", "find", ok=True)),  # October: left out
        (start, ApiCall("hunter", "find", ok=True)),
        (T, ApiCall("hunter", "find", ok=False, error_code="hunter_unavailable")),
        (T, ApiCall("hunter", "verify", ok=True)),
        (T, ApiCall("places", "text_search", ok=True, sku="pro")),
        (T, ApiCall("places", "text_search", ok=True, sku="enterprise")),
        (T, ApiCall("places", "text_search", ok=False, error_code="places_failed", sku="pro")),
        (T, AiCall("draft", "d:1", "m-1", input_tokens=1, output_tokens=1, cost_micros=700)),
        (end - tiny, AiCall("draft", "d:1", "m-1", input_tokens=1, output_tokens=1, cost_micros=5)),
        (end, AiCall("draft", "d:1", "m-1", input_tokens=1, output_tokens=1, cost_micros=900)),
        (end, ApiCall("hunter", "find", ok=True)),  # December: left out
    ]
    for at, call in calls:
        record_at(meter, clock, at, call)

    with units.open(read_only=True):
        found = meter.snapshot(T)

    assert found == Usage(
        month="2026-11",
        api=(
            ApiUse("hunter", "find", None, calls=2, failed=1, units=4),
            ApiUse("hunter", "verify", None, calls=1, failed=0, units=1),
            ApiUse("places", "text_search", "enterprise", calls=1, failed=0, units=1),
            ApiUse("places", "text_search", "pro", calls=2, failed=1, units=2),
        ),
        ai_calls=2,
        ai_cost_micros=705,
    )
    with units.open(read_only=True):
        december = meter.snapshot(end)
    assert december.api == (ApiUse("hunter", "find", None, 1, 0, 2),)
    assert (december.ai_calls, december.ai_cost_micros) == (1, 900)


def test_an_empty_month_has_no_lines(units: UnitOfWorkFactory, clock: FrozenClock) -> None:
    with units.open(read_only=True):
        found = UsageMeter(units, clock).snapshot(T)

    assert found == Usage("2026-11", (), 0, 0)


def test_the_snapshot_reads_inside_the_callers_transaction_too(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    meter = UsageMeter(units, clock)
    meter.record(ApiCall("prospeo", "find", ok=True))
    seen: list[Usage] = []

    @use_case
    def read_inside() -> None:
        with current().transaction():
            seen.append(meter.snapshot(T))

    with units.open():
        read_inside()

    assert seen[0].units("prospeo") == 1


def test_the_snapshot_needs_a_unit_of_work(units: UnitOfWorkFactory, clock: FrozenClock) -> None:
    with pytest.raises(UnitOfWorkError):
        UsageMeter(units, clock).snapshot(T)
