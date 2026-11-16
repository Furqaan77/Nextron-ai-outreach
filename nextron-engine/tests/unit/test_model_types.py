"""The column types ``Ulid`` and ``UtcDateTime``, ``new_id`` and the model base."""

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import StatementError
from ulid import ULID

from nextron.db.engine import create_engine
from nextron.db.models import Base, EventRecord, Ulid, UtcDateTime, new_id
from nextron.db.models.base import NAMING_CONVENTION
from nextron.domain.ids import is_ulid

CANONICAL = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
DIALECT = sqlite.dialect()


def bind_ulid(value: object) -> object:
    return Ulid().process_bind_param(value, DIALECT)


def bind_datetime(value: object) -> object:
    return UtcDateTime().process_bind_param(value, DIALECT)


# Ulid -------------------------------------------------------------------------------------------


def test_ulid_stores_canonical_text() -> None:
    assert bind_ulid(ULID.from_str(CANONICAL)) == CANONICAL
    assert bind_ulid(CANONICAL) == CANONICAL
    assert bind_ulid(CANONICAL.lower()) == CANONICAL  # case is normalised
    assert bind_ulid(None) is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        CANONICAL[:-1],
        CANONICAL + "0",
        " " + CANONICAL[1:],
        "81JD3QZ8X5V9K2M7N4P6R8T0WY",  # overflows 128 bits
        "01JD3QZ8X5V9K2M7N4P6R8T0WI",
        "01JD3QZ8X5V9K2M7N4P6R8T0WL",
        "01JD3QZ8X5V9K2M7N4P6R8T0WO",
        "01JD3QZ8X5V9K2M7N4P6R8T0WU",
        "ann@example.com",
    ],
)
def test_ulid_rejects_a_string_that_is_not_a_ulid(value: str) -> None:
    with pytest.raises(ValueError, match="not a ULID") as raised:
        bind_ulid(value)

    if value:
        assert value not in str(raised.value)


@pytest.mark.parametrize("value", [7, b"01JD3QZ8X5V9K2M7N4P6R8T0WY", uuid4(), 1.5, object()])
def test_ulid_rejects_anything_else(value: object) -> None:
    with pytest.raises(TypeError, match="ULID column"):
        bind_ulid(value)


def test_new_id_is_a_fresh_canonical_ulid() -> None:
    first, second = new_id(), new_id()

    assert is_ulid(first) and is_ulid(second)
    assert first != second


# UtcDateTime ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (datetime(2026, 11, 17, 9, 30, tzinfo=UTC), "2026-11-17T09:30:00.000000Z"),
        (datetime(2026, 11, 17, 9, 30, 1, 7, tzinfo=UTC), "2026-11-17T09:30:01.000007Z"),
        (
            datetime(2026, 7, 1, 10, 0, tzinfo=ZoneInfo("Europe/London")),  # BST, UTC+1
            "2026-07-01T09:00:00.000000Z",
        ),
        (
            datetime(2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            "2025-12-31T19:00:00.000000Z",
        ),
        (datetime(999, 1, 2, 3, 4, 5, tzinfo=UTC), "0999-01-02T03:04:05.000000Z"),
    ],
)
def test_utc_datetime_stores_fixed_width_utc_text(value: datetime, stored: str) -> None:
    text_value = bind_datetime(value)

    assert text_value == stored
    assert len(stored) == 27
    read = UtcDateTime().process_result_value(stored, DIALECT)
    assert read == value
    assert read is not None and read.tzinfo is UTC


def test_utc_datetime_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        bind_datetime(datetime(2026, 11, 17, 9, 30))


@pytest.mark.parametrize("value", [date(2026, 11, 17), "2026-11-17T09:30:00Z", 1_763_371_800])
def test_utc_datetime_rejects_anything_but_a_datetime(value: object) -> None:
    with pytest.raises(TypeError, match="takes a datetime"):
        bind_datetime(value)


@pytest.mark.parametrize(
    "stored", ["2026-11-17T09:30:00Z", "2026-11-17 09:30:00.000000Z", "2026-11-17T09:30:00.000000"]
)
def test_utc_datetime_refuses_to_read_other_text(stored: str) -> None:
    with pytest.raises(ValueError):
        UtcDateTime().process_result_value(stored, DIALECT)


def test_both_types_round_trip_through_sqlite(tmp_path: Path) -> None:
    metadata = MetaData()
    table = Table(
        "typed",
        metadata,
        Column("n", Integer, primary_key=True),
        Column("ref", Ulid),
        Column("at", UtcDateTime),
    )
    engine = create_engine(tmp_path / "types.db")
    moment = datetime(2026, 11, 17, 9, 30, 15, 250000, tzinfo=ZoneInfo("Europe/London"))
    try:
        with engine.connect() as connection:
            metadata.create_all(connection)
            connection.execute(insert(table).values(n=1, ref=CANONICAL.lower(), at=moment))
            raw = connection.execute(text("SELECT ref, at FROM typed")).one()
            typed = connection.execute(select(table.c.ref, table.c.at)).one()
            with pytest.raises(StatementError) as raised:
                connection.execute(insert(table).values(n=2, at=datetime(2026, 11, 17)))
            connection.commit()
    finally:
        engine.dispose()

    assert tuple(raw) == (CANONICAL, "2026-11-17T09:30:15.250000Z")
    assert tuple(typed) == (CANONICAL, moment)
    assert isinstance(raised.value.orig, ValueError)


# The model base ---------------------------------------------------------------------------------


def test_the_base_names_constraints_and_indexes() -> None:
    table = EventRecord.__table__

    assert dict(Base.metadata.naming_convention) == NAMING_CONVENTION
    assert table.primary_key.name == "pk_events"
    assert {index.name for index in table.indexes} == {"ix_events_entity_entity_id", "ix_events_at"}


def test_the_events_model_has_the_ad4_columns() -> None:
    columns = {column.name: column for column in EventRecord.__table__.columns}

    assert list(columns) == [
        "id",
        "entity",
        "entity_id",
        "from_status",
        "to_status",
        "cause",
        "cause_ref",
        "actor",
        "at",
    ]
    assert {name for name, column in columns.items() if column.nullable} == {
        "from_status",
        "cause_ref",
    }
    assert isinstance(columns["id"].type, Ulid) and columns["id"].primary_key
    assert isinstance(columns["entity_id"].type, Ulid)
    assert isinstance(columns["cause_ref"].type, Ulid)
    assert isinstance(columns["at"].type, UtcDateTime)
