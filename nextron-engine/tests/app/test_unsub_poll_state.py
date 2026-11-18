"""The ``unsub_poll_state`` table and its supports (FR-70, FR-58, AD-15, AD-20, Story 3.12), on a
temporary database at head: the one row (only ``id`` = the ULID of value 1), the closed failure
codes and the failure columns set together, every column mapped and classified; the
repository's ``get`` and ``save``; ``UsageMeter.day_calls`` over ``api_usage`` (one UTC day, one
provider and operation); and ``Notify.clear``, which dismisses one active notification and
leaves the alert dedup as it was."""

import importlib.util
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, inspect

from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.db import owners, pii_registry
from nextron.db.metering import UsageMeter
from nextron.db.models.compliance import UNSUB_POLL_STATE_ID
from nextron.db.repos.compliance import unsub_poll_state as poll_state_repo
from nextron.db.repos.compliance.unsub_poll_state import PollState
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.budget import ApiCall
from nextron.domain.job import JobTypes
from nextron.domain.unsub_requests import PollFailure
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
COLUMNS = ["id", "last_success_at", "failing_since", "last_failure_at", "last_failure_code"]
TIME = "2026-11-18T09:00:00.000000Z"
MIGRATIONS = Path(__file__).resolve().parents[2] / "nextron" / "db" / "alembic" / "versions"


def sql(engine: Engine, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    assert engine.url.database
    connection = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        return [tuple(row) for row in connection.execute(statement, params).fetchall()]
    finally:
        connection.close()


def transact[R](units: UnitOfWorkFactory, fn: Any) -> R:
    @use_case
    def caller() -> R:
        with current().transaction():
            return fn()  # type: ignore[no-any-return]

    with units.open():
        return caller()


# The schema -------------------------------------------------------------------------------------


def test_the_table_has_its_columns_all_nullable_but_the_id(db_engine: Engine) -> None:
    columns = inspect(db_engine).get_columns("unsub_poll_state")

    assert [column["name"] for column in columns] == COLUMNS
    assert [column["nullable"] for column in columns] == [False, True, True, True, True]


def test_every_column_is_mapped_to_compliance_and_classified_none() -> None:
    assert owners.TABLES["unsub_poll_state"] == {"compliance": tuple(COLUMNS)}
    assert set(pii_registry.PII["unsub_poll_state"]) == set(COLUMNS)
    assert {str(found) for found in pii_registry.PII["unsub_poll_state"].values()} == {"none"}


@pytest.mark.parametrize(
    "row",
    [
        ("00000000000000000000000002", None, None, None, None),  # a second row
        ("01JD3QZ8X5V9K2M7N4P6R8T0AA", None, None, None, None),
        (UNSUB_POLL_STATE_ID, None, TIME, TIME, "unsub_timeout"),  # not a closed code
        (UNSUB_POLL_STATE_ID, None, TIME, None, "internal_error"),  # the failure half set
        (UNSUB_POLL_STATE_ID, None, None, TIME, "internal_error"),
        (UNSUB_POLL_STATE_ID, None, TIME, TIME, None),
    ],
)
def test_the_table_refuses_another_id_another_code_or_a_half_set_failure(
    db_engine: Engine, row: tuple[object, ...]
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        sql(db_engine, "INSERT INTO unsub_poll_state VALUES (?, ?, ?, ?, ?)", row)


@pytest.mark.parametrize("code", [str(code) for code in PollFailure])
def test_the_table_admits_the_one_row_with_each_closed_code(db_engine: Engine, code: str) -> None:
    sql(
        db_engine,
        "INSERT INTO unsub_poll_state VALUES (?, NULL, ?, ?, ?)",
        (UNSUB_POLL_STATE_ID, TIME, TIME, code),
    )

    assert sql(db_engine, "SELECT id, last_failure_code FROM unsub_poll_state") == [
        (UNSUB_POLL_STATE_ID, code)
    ]


def test_the_migration_s_code_list_is_the_closed_codes() -> None:
    path = MIGRATIONS / "0046_3_12_unsub_poll_state.py"
    spec = importlib.util.spec_from_file_location("migration_0038", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert set(module.FAILURE_CODES) == {code.value for code in PollFailure}
    assert len(module.FAILURE_CODES) == len(PollFailure)


def test_the_closed_codes_are_the_five_port_codes_unreachable_and_internal_error() -> None:
    assert [str(code) for code in PollFailure] == [
        "unsub_refused",
        "unsub_bearer_invalid",
        "unsub_unauthorized",
        "unsub_unavailable",
        "unsub_bad_response",
        "unsub_unreachable",
        "internal_error",
    ]


# The repository ---------------------------------------------------------------------------------


def test_get_is_none_until_a_save_and_save_keeps_one_row(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    with units.open(read_only=True):
        assert poll_state_repo.get() is None
    failing = PollState(None, T0, T0 + timedelta(minutes=5), "unsub_unavailable")
    ok = PollState(T0 + timedelta(minutes=10), None, None, None)

    transact(units, lambda: poll_state_repo.save(failing))
    with units.open(read_only=True):
        assert poll_state_repo.get() == failing
    transact(units, lambda: poll_state_repo.save(ok))

    with units.open(read_only=True):
        assert poll_state_repo.get() == ok
    assert sql(db_engine, "SELECT count(*), min(id) FROM unsub_poll_state") == [
        (1, UNSUB_POLL_STATE_ID)
    ]


# UsageMeter.day_calls ---------------------------------------------------------------------------


def test_day_calls_counts_one_providers_operation_on_now_s_utc_day(
    units: UnitOfWorkFactory,
) -> None:
    clock = FrozenClock(T0)
    meter = UsageMeter(units, clock)

    def record(at: datetime, call: ApiCall) -> None:
        clock.set(at)
        meter.record(call)

    kv = ApiCall("unsub_worker", "kv_list", ok=True)
    record(datetime(2026, 11, 17, 23, 59, 59, tzinfo=UTC), kv)  # the day before
    record(datetime(2026, 11, 18, 0, 0, tzinfo=UTC), kv)
    record(T0, ApiCall("unsub_worker", "kv_list", ok=False, error_code="unsub_unavailable"))
    record(datetime(2026, 11, 18, 23, 59, 59, tzinfo=UTC), kv)
    record(datetime(2026, 11, 19, 0, 0, tzinfo=UTC), kv)  # the day after
    record(T0, ApiCall("unsub_worker", "other", ok=True))
    record(T0, ApiCall("hunter", "kv_list", ok=True))

    with units.open(read_only=True):
        assert meter.day_calls(T0, "unsub_worker", "kv_list") == 3
        assert meter.day_calls(T0, "nobody", "kv_list") == 0


# Notify.clear -----------------------------------------------------------------------------------


def test_clear_dismisses_the_active_notification_once_and_keeps_the_dedup(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    clock = FrozenClock(T0)
    types = JobTypes()
    types.freeze()
    notify = Notify(clock, JobQueue(clock, types), alert_ports={})
    channels = (Channel.DASHBOARD,)

    kind, subject = "unsub_poll_failing", "unsub_poll_failing:1"

    def clear() -> bool:
        return transact(units, lambda: notify.clear(kind, subject))

    def alert(alert_kind: str = kind) -> Any:
        return transact(units, lambda: notify.alert(alert_kind, subject, "x", channels))

    assert clear() is False  # nothing to clear yet
    alert()
    alert("other_kind")
    clock.advance(timedelta(minutes=1))

    assert clear() is True
    assert clear() is False  # already dismissed
    again = alert()

    assert again.created == ()  # a cleared episode never alerts twice
    assert sql(db_engine, "SELECT kind, dismissed_at FROM notifications ORDER BY rowid") == [
        ("unsub_poll_failing", "2026-11-18T09:01:00.000000Z"),
        ("other_kind", None),
    ]
