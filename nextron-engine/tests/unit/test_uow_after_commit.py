"""``UnitOfWork.after_commit`` (Story 3.16): callbacks run in order right after the transaction
commits, outside it, while the use case still runs; a rollback drops them; a failing one is
logged and the rest still run."""

import logging
import sqlite3

import pytest
from sqlalchemy import Engine, text

from nextron.db.uow import (
    AFTER_COMMIT_FAILED,
    UnitOfWorkError,
    UnitOfWorkFactory,
    command,
    current,
    in_transaction,
    use_case,
)


@pytest.fixture
def scratch(db_engine: Engine) -> Engine:
    with db_engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE scratch (id INTEGER PRIMARY KEY, note TEXT NOT NULL)"
        )
        connection.commit()
    return db_engine


def committed(engine: Engine) -> list[str]:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        return [note for (note,) in reader.execute("SELECT note FROM scratch ORDER BY id")]
    finally:
        reader.close()


def write(note: str) -> None:
    current().session.execute(text("INSERT INTO scratch (note) VALUES (:note)"), {"note": note})


def test_callbacks_run_in_order_after_the_commit_outside_it(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    seen: list[tuple[str, list[str], bool]] = []

    @command
    def record(note: str) -> None:
        write(note)
        current().after_commit(lambda: seen.append((note, committed(scratch), in_transaction())))

    @use_case
    def save() -> str:
        with current().transaction():
            record("one")
            record("two")
            assert seen == []
        seen.append(("after", committed(scratch), in_transaction()))
        return "done"

    with units.open():
        assert save() == "done"

    assert seen == [
        ("one", ["one", "two"], False),
        ("two", ["one", "two"], False),
        ("after", ["one", "two"], False),
    ]


def test_a_rollback_drops_the_callbacks(units: UnitOfWorkFactory, scratch: Engine) -> None:
    seen: list[str] = []

    @use_case
    def save() -> None:
        with current().transaction():
            write("lost")
            current().after_commit(lambda: seen.append("ran"))
            raise KeyError("boom")

    with units.open(), pytest.raises(KeyError):
        save()

    assert seen == []
    assert committed(scratch) == []


def test_a_doomed_transaction_drops_the_callbacks(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    seen: list[str] = []

    @command
    def broken() -> None:
        current().after_commit(lambda: seen.append("ran"))
        raise ValueError("broken")

    @use_case
    def save() -> None:
        with current().transaction():
            try:
                broken()
            except ValueError:
                pass

    with units.open(), pytest.raises(UnitOfWorkError):
        save()

    assert seen == []


def test_a_failing_callback_is_logged_and_the_rest_still_run(
    units: UnitOfWorkFactory, scratch: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[str] = []

    def fail() -> None:
        raise OSError("disk gone")

    @use_case
    def save() -> None:
        with current().transaction():
            write("kept")
            current().after_commit(fail)
            current().after_commit(lambda: seen.append("second"))

    with caplog.at_level(logging.ERROR, logger="nextron.db.uow"), units.open():
        save()

    assert seen == ["second"]
    assert committed(scratch) == ["kept"]
    failed = [record for record in caplog.records if record.getMessage() == AFTER_COMMIT_FAILED]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert failed[0].code == "OSError"  # type: ignore[attr-defined]
    assert failed[0].callback.endswith("fail")  # type: ignore[attr-defined]
    assert failed[0].exc_info is not None and failed[0].exc_info[0] is OSError


def test_a_callback_may_open_another_transaction_of_the_use_case(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        uow = current()

        def follow_up() -> None:
            with uow.transaction():
                write("follow-up")

        with uow.transaction():
            write("first")
            uow.after_commit(follow_up)

    with units.open():
        save()

    assert committed(scratch) == ["first", "follow-up"]


def test_after_commit_needs_an_open_write_transaction(units: UnitOfWorkFactory) -> None:
    @use_case
    def outside() -> None:
        current().after_commit(lambda: None)

    with units.open(), pytest.raises(UnitOfWorkError, match="open transaction"):
        outside()

    with units.open(read_only=True) as uow:
        uow.session.execute(text("SELECT 1"))  # the read transaction is open
        with pytest.raises(UnitOfWorkError, match="read-only"):
            uow.after_commit(lambda: None)


def test_after_commit_takes_a_callable(units: UnitOfWorkFactory, scratch: Engine) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            current().after_commit("not callable")  # type: ignore[arg-type]

    with units.open(), pytest.raises(TypeError):
        save()
