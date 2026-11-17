"""``nextron.db.eventlog.append`` and the append-only ``events`` table."""

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron.db import eventlog
from nextron.db.owners import OWNERS
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current, use_case
from nextron.domain.ids import is_ulid

LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
REPLY = "01JD3QZ8X5V9K2M7N4P6R8T0WZ"
AT = datetime(2026, 11, 17, 9, 30, 15, 123456, tzinfo=UTC)

VALID: dict[str, Any] = {
    "entity": "events",
    "entity_id": LEAD,
    "from_status": "new",
    "to_status": "queued",
    "cause": "enrolled",
    "cause_ref": REPLY,
    "actor": "system:sequencing",
    "at": AT,
}


def rows(engine: Engine) -> list[tuple[object, ...]]:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        return reader.execute(
            "SELECT id, entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events ORDER BY id"
        ).fetchall()
    finally:
        reader.close()


def append_in_a_unit_of_work(units: UnitOfWorkFactory, **arguments: Any) -> str:
    @use_case
    def record() -> str:
        with current().transaction():
            return eventlog.append(**arguments)

    with units.open():
        return record()


# Row: event row
def test_append_writes_one_row_and_returns_its_ulid(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    event_id = append_in_a_unit_of_work(units, **VALID)

    assert is_ulid(event_id)
    assert rows(db_engine) == [
        (
            event_id,
            "events",
            LEAD,
            "new",
            "queued",
            "enrolled",
            REPLY,
            "system:sequencing",
            "2026-11-17T09:30:15.123456Z",
        )
    ]


def test_a_creation_has_no_from_status_and_a_cause_ref_is_optional(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    event_id = append_in_a_unit_of_work(
        units, **(VALID | {"from_status": None, "cause_ref": None, "actor": "seed"})
    )

    [row] = rows(db_engine)
    assert row[0] == event_id
    assert (row[3], row[6], row[7]) == (None, None, "seed")


def test_at_is_stored_as_utc(units: UnitOfWorkFactory, db_engine: Engine) -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    append_in_a_unit_of_work(units, **(VALID | {"at": datetime(2026, 11, 17, 15, 0, tzinfo=india)}))

    [row] = rows(db_engine)
    assert row[8] == "2026-11-17T09:30:00.000000Z"


def test_append_writes_in_the_current_transaction(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    @use_case
    def record_then_fail() -> None:
        with current().transaction():
            eventlog.append(**VALID)
            raise RuntimeError("the transition failed")

    with units.open(), pytest.raises(RuntimeError):
        record_then_fail()

    assert rows(db_engine) == []


def test_append_needs_an_open_transaction() -> None:
    with pytest.raises(UnitOfWorkError):
        eventlog.append(**VALID)


@pytest.mark.parametrize(
    "actor",
    [
        "operator",
        "sender",
        "seed",
        *(f"system:{owner}" for owner in OWNERS),
        "ai:reply_classifier@2026-11-17",
        "ai:r@1",
        "ai:draft_step_1@v1.2_rc-3",
        "ai:r" + "x" * 63 + "@" + "V" * 32,
    ],
)
def test_every_valid_actor_is_accepted(
    actor: str, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    append_in_a_unit_of_work(units, **(VALID | {"actor": actor}))

    assert [row[7] for row in rows(db_engine)] == [actor]


# Rows: bad actor, and every other argument
BAD_ARGUMENTS = [
    ("actor", "system:cron"),
    ("actor", "Operator"),
    ("actor", "ai:x"),
    ("actor", "ai:r@"),
    ("actor", "a@b.co"),
    ("actor", "system:"),
    ("actor", "system:setup "),
    ("actor", "operator\n"),
    ("actor", "ai:Route@1"),
    ("actor", "ai:1route@1"),
    ("actor", "ai:r@-1"),
    ("actor", "ai:r" + "x" * 64 + "@1"),
    ("actor", "ai:r@" + "V" * 33),
    ("actor", None),
    ("entity", "no_such_table"),
    ("entity", "EVENTS"),
    ("entity", None),
    ("entity_id", LEAD.lower()),
    ("entity_id", "lead-7"),
    ("entity_id", None),
    ("from_status", "New"),
    ("from_status", ""),
    ("to_status", None),
    ("to_status", "x" * 65),
    ("to_status", "in progress"),
    ("cause", "1st"),
    ("cause", 7),
    ("cause_ref", "not-a-ulid"),
    ("cause_ref", 7),
    ("at", datetime(2026, 11, 17, 9, 30)),
    ("at", date(2026, 11, 17)),
    ("at", "2026-11-17T09:30:00Z"),
    ("at", None),
]


@pytest.mark.parametrize(("argument", "value"), BAD_ARGUMENTS)
def test_a_bad_argument_writes_nothing(
    argument: str, value: object, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    @use_case
    def record() -> None:
        with current().transaction():
            with pytest.raises(ValueError, match=argument):
                eventlog.append(**(VALID | {argument: value}))

    with units.open():
        record()

    assert rows(db_engine) == []


@pytest.mark.parametrize(
    ("argument", "value"),
    [("actor", "ann@example.com"), ("entity_id", "ann@example.com"), ("cause", "Ann Smith")],
)
def test_the_error_names_the_argument_never_the_value(argument: str, value: str) -> None:
    with pytest.raises(ValueError) as raised:
        eventlog.append(**(VALID | {argument: value}))

    assert argument in str(raised.value)
    assert value not in str(raised.value)


def test_every_bad_argument_is_named_at_once() -> None:
    with pytest.raises(ValueError) as raised:
        eventlog.append(
            entity="x",
            entity_id="x",
            from_status="X",
            to_status="X",
            cause="X",
            cause_ref="x",
            actor="x",
            at="x",  # type: ignore[arg-type]
        )

    names = ["entity", "entity_id", "from_status", "to_status", "cause", "cause_ref", "actor", "at"]
    assert [part.split(" ", 1)[0] for part in str(raised.value).split("; ")] == names


# Row: append-only
SAME_ID_CHANGED = (
    "SELECT id, entity, entity_id, from_status, 'lost', cause, cause_ref, actor, at FROM events"
)


@pytest.fixture
def one_event(units: UnitOfWorkFactory, db_engine: Engine) -> str:
    return append_in_a_unit_of_work(units, **VALID)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE events SET to_status = 'lost'",
        "UPDATE events SET to_status = to_status",
        "DELETE FROM events",
        f"INSERT OR REPLACE INTO events {SAME_ID_CHANGED}",
        f"REPLACE INTO events {SAME_ID_CHANGED}",
        "INSERT INTO events SELECT * FROM events",
        "INSERT INTO events SELECT * FROM events WHERE true "
        "ON CONFLICT (id) DO UPDATE SET to_status = 'lost'",
        "INSERT OR IGNORE INTO events SELECT * FROM events",
    ],
)
def test_the_triggers_abort_any_change_to_an_event(
    sql: str, one_event: str, db_engine: Engine
) -> None:
    before = rows(db_engine)

    with db_engine.connect() as connection:
        with pytest.raises(IntegrityError, match="events is append-only"):
            connection.exec_driver_sql(sql)
        connection.rollback()

    assert rows(db_engine) == before
    assert before[0][0] == one_event


def test_the_triggers_hold_outside_the_engine_too(one_event: str, db_engine: Engine) -> None:
    assert db_engine.url.database
    other = sqlite3.connect(db_engine.url.database, isolation_level=None)
    try:
        for sql in ("UPDATE events SET actor = 'operator'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                other.execute(sql)
    finally:
        other.close()

    assert len(rows(db_engine)) == 1


def test_a_new_id_inserts_normally(
    one_event: str, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    second = append_in_a_unit_of_work(units, **VALID)

    assert second != one_event
    assert len(rows(db_engine)) == 2
