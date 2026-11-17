"""``Setup.set``, ``Setup.save`` and ``queries.setup.get`` on real units of work at head (the Set,
Get and Save rows), with a ``FrozenClock`` and, for the save rows, a probe schema swapped in for
``settings_schema.SCHEMA``."""

import logging
import sqlite3
from datetime import UTC, datetime, time
from types import MappingProxyType
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory
from nextron.domain import settings_schema
from nextron.domain.settings_schema import (
    REQUIRED,
    InvalidSettings,
    InvalidSettingValue,
    Key,
    Section,
    Setting,
    SettingNotSet,
    SettingType,
    Slot,
    UnknownSetting,
    maximum,
    minimum,
)
from tests.fakes.clock import FrozenClock

T1 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 17, 10, 30, 15, 250000, tzinfo=UTC)
MARK = "QZXVALUE"

PROBE = MappingProxyType(
    {
        "probe_zone": Setting(
            key="probe_zone",
            type=SettingType.TIMEZONE,
            section=Section.SENDER,
            label="Probe zone",
            unit="IANA timezone name",
            default=REQUIRED,
        ),
        "probe_cap": Setting(
            key="probe_cap",
            type=SettingType.INTEGER,
            section=Section.SENDING,
            label="Probe cap",
            unit="emails a day",
            default=30,
            rules=(minimum(1, "1"), maximum(100, "100")),
        ),
        "probe_start": Setting(
            key="probe_start",
            type=SettingType.TIME,
            section=Section.SENDING,
            label="Probe start",
            unit="HH:MM",
            default=time(7, 0),
            timezone_key="probe_zone",
        ),
        "probe_budget": Setting(
            key="probe_budget",
            type=SettingType.MONEY,
            section=Section.BUDGET_AND_LIMITS,
            label="Probe budget",
            unit="US dollars a month",
            default=20_000_000,
        ),
        "probe_people": Setting(
            key="probe_people",
            type=SettingType.LIST,
            section=Section.ALERTS_AND_RECIPIENTS,
            label="Probe people",
            unit="one per line",
            default=(),
        ),
        "probe_hours": Setting(
            key="probe_hours",
            type=SettingType.AVAILABILITY,
            section=Section.SENDER,
            label="Probe hours",
            unit="one slot per line",
            default=REQUIRED,
            timezone_key="probe_zone",
        ),
    }
)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T1)


@pytest.fixture
def setup(clock: FrozenClock) -> Setup:
    return Setup(clock)


@pytest.fixture
def probe_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_schema.check_schema(PROBE, keys=None)
    monkeypatch.setattr(settings_schema, "SCHEMA", PROBE)


def rows(engine: Engine) -> list[tuple[Any, ...]]:
    """Every ``settings`` row as ``(id, key, value, updated_at)``, read outside the engine."""
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        return reader.execute(
            "SELECT id, key, value, updated_at FROM settings ORDER BY key"
        ).fetchall()
    finally:
        reader.close()


def by_key(engine: Engine) -> dict[str, tuple[Any, ...]]:
    return {row[1]: row for row in rows(engine)}


def insert_row(engine: Engine, key: str, value: str) -> None:
    assert engine.url.database
    writer = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        writer.execute(
            "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            ("01JD3QZ8X5V9K2M7N4P6R8T0WY", key, value, "2026-11-01T00:00:00.000000Z"),
        )
    finally:
        writer.close()


def get(units: UnitOfWorkFactory, key: str) -> Any:
    with units.open(read_only=True):
        return setup_queries.get(key)


def stored(units: UnitOfWorkFactory) -> dict[str, str]:
    with units.open(read_only=True):
        return setup_queries.stored_texts()


def save(units: UnitOfWorkFactory, setup: Setup, texts: dict[str, Any]) -> dict[str, str]:
    with units.open():
        return setup.save(texts)


def set_value(units: UnitOfWorkFactory, setup: Setup, key: str, value: object) -> None:
    with units.open():
        setup.set(key, value)


def logged_changes(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        getattr(record, "keys")
        for record in caplog.records
        if record.getMessage() == "settings_changed"
    ]


# Row: Set ok ------------------------------------------------------------------------------------


def test_set_stores_the_canonical_text_with_updated_at_then_updates_the_same_row(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup, clock: FrozenClock
) -> None:
    set_value(units, setup, Key.SENDER_TIMEZONE, "Europe/London")
    [first] = rows(db_engine)
    clock.set(T2)
    set_value(units, setup, Key.SENDER_TIMEZONE, " America/New_York ")
    [second] = rows(db_engine)

    assert first[1:] == ("sender_timezone", "Europe/London", "2026-11-17T09:00:00.000000Z")
    assert second[1:] == ("sender_timezone", "America/New_York", "2026-11-17T10:30:15.250000Z")
    assert second[0] == first[0]  # the same id
    assert get(units, Key.SENDER_TIMEZONE) == "America/New_York"
    assert get(units, "sender_timezone") == "America/New_York"


def test_set_logs_the_key_never_the_value(
    units: UnitOfWorkFactory, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    set_value(units, setup, Key.SENDER_TIMEZONE, "Europe/London")

    assert logged_changes(caplog) == ["sender_timezone"]
    assert "Europe/London" not in caplog.text
    assert all("Europe/London" not in str(vars(record)) for record in caplog.records)


def test_each_use_case_reads_the_clock_once(units: UnitOfWorkFactory) -> None:
    class CountingClock(FrozenClock):
        reads = 0

        def now(self) -> datetime:
            self.reads += 1
            return super().now()

    clock = CountingClock(T1)
    setup = Setup(clock)

    set_value(units, setup, Key.SENDER_TIMEZONE, "Europe/London")
    assert clock.reads == 1
    save(units, setup, {"sender_timezone": "Europe/Paris"})
    assert clock.reads == 2


# Row: Set refused -------------------------------------------------------------------------------


def test_set_refuses_an_unknown_key_and_writes_nothing(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup
) -> None:
    with pytest.raises(UnknownSetting):
        set_value(units, setup, "nope", "Europe/London")

    assert rows(db_engine) == []


@pytest.mark.parametrize("value", ["europe/london", "Factory", "Mars/Base", 5, None, ""])
def test_set_refuses_an_invalid_value_and_writes_nothing(
    value: object, units: UnitOfWorkFactory, db_engine: Engine, setup: Setup
) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        set_value(units, setup, Key.SENDER_TIMEZONE, value)

    assert raised.value.rule == "timezone"  # the type is checked first, even for ""
    assert str(raised.value).startswith("sender_timezone: ")
    assert str(raised.value).endswith("(rule timezone)")
    if isinstance(value, str) and value:
        assert value not in str(raised.value)
    assert rows(db_engine) == []


def test_a_refused_set_keeps_the_stored_value(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup, clock: FrozenClock
) -> None:
    set_value(units, setup, Key.SENDER_TIMEZONE, "Europe/London")
    before = rows(db_engine)
    clock.set(T2)

    with pytest.raises(InvalidSettingValue):
        set_value(units, setup, Key.SENDER_TIMEZONE, "Mars/Base")

    assert rows(db_engine) == before


# Row: Get ---------------------------------------------------------------------------------------


@pytest.mark.usefixtures("probe_schema")
def test_get_returns_the_default_without_a_row(units: UnitOfWorkFactory) -> None:
    assert get(units, "probe_cap") == 30
    assert get(units, "probe_start") == time(7, 0)
    assert get(units, "probe_budget") == 20_000_000
    assert get(units, "probe_people") == ()


def test_get_fails_closed_on_a_required_setting_that_isnt_set(units: UnitOfWorkFactory) -> None:
    with pytest.raises(SettingNotSet) as raised:
        get(units, Key.SENDER_TIMEZONE)

    assert raised.value.code == "setting_not_set"
    assert str(raised.value) == "sender_timezone is not set; set it on the Settings page"


@pytest.mark.usefixtures("probe_schema")
def test_get_decodes_the_stored_text_to_its_type(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    save(
        units,
        setup,
        {"probe_hours": "fri 09:00-12:00\nMon 09:00-17:00", "probe_budget": "12.5"},
    )

    assert get(units, "probe_hours") == (
        Slot(0, time(9), time(17)),
        Slot(4, time(9), time(12)),
    )
    assert get(units, "probe_budget") == 12_500_000


@pytest.mark.usefixtures("probe_schema")
@pytest.mark.parametrize(
    ("key", "text", "rule"),
    [
        ("probe_cap", "lots", "integer"),
        ("probe_cap", "500", "maximum"),  # valid once, refused by a rule declared since
        ("probe_zone", "Mars/Base", "timezone"),
        ("probe_zone", "", "required"),
    ],
)
def test_get_refuses_a_stored_text_that_no_longer_parses(
    key: str, text: str, rule: str, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    insert_row(db_engine, key, text)

    with pytest.raises(InvalidSettingValue) as raised:
        get(units, key)

    assert raised.value.rule == rule
    assert raised.value.key == key


def test_get_refuses_an_unknown_key(units: UnitOfWorkFactory) -> None:
    with pytest.raises(UnknownSetting):
        get(units, "nope")


def test_stored_texts_lists_every_stored_row(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    assert stored(units) == {}

    set_value(units, setup, Key.SENDER_TIMEZONE, "Asia/Tokyo")

    assert stored(units) == {"sender_timezone": "Asia/Tokyo"}


# Required settings (nextron doctor, Story 1.14) -------------------------------------------------


# the required footer settings (Story 4.11), set
FOOTER_SET = {
    "sender_signature": "Furqaan, Nextron AI",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 Example Street, London EC1A 1AA",
    "privacy_notice_url": "https://nextronai.com/privacy",
}


def required_unset(units: UnitOfWorkFactory) -> tuple[str, ...]:
    with units.open(read_only=True):
        return setup_queries.required_unset()


def test_required_unset_lists_every_required_key_in_declaration_order(
    units: UnitOfWorkFactory,
) -> None:
    required = [str(found.key) for found in settings_schema.declared() if found.required]

    assert (
        required_unset(units)
        == (
            "sender_timezone",
            "alert_chat_id",
            "sender_signature",  # the footer (Story 4.11)
            "legal_name",
            "postal_address",
            "privacy_notice_url",
        )
        == tuple(required)
    )


def test_required_unset_is_empty_once_every_required_setting_is_set(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    save(
        units,
        setup,
        {"sender_timezone": "Europe/London", "alert_chat_id": "-1001234567890", **FOOTER_SET},
    )

    assert required_unset(units) == ()


def test_required_unset_counts_a_stored_text_that_no_longer_parses(
    units: UnitOfWorkFactory, setup: Setup, db_engine: Engine
) -> None:
    save(units, setup, {"alert_chat_id": "-1001234567890", **FOOTER_SET})
    insert_row(db_engine, "sender_timezone", "Mars/Base")

    assert required_unset(units) == ("sender_timezone",)


@pytest.mark.usefixtures("probe_schema")
def test_required_unset_follows_the_schema(units: UnitOfWorkFactory, db_engine: Engine) -> None:
    assert required_unset(units) == ("probe_zone", "probe_hours")

    insert_row(db_engine, "probe_zone", "Europe/London")

    assert required_unset(units) == ("probe_hours",)


# Row: Save ok -----------------------------------------------------------------------------------

FIRST = {
    "probe_zone": "Europe/London",
    "probe_cap": " 40 ",
    "probe_start": "08:30",
    "probe_budget": "25",
    "probe_people": "ops@example.test\r\n\r\nmac@example.test",
    "probe_hours": "tue 09:00-12:00\nMon 09:00-17:00",
}
FIRST_STORED = {
    "probe_zone": "Europe/London",
    "probe_cap": "40",
    "probe_start": "08:30",
    "probe_budget": "25.00",
    "probe_people": "ops@example.test\nmac@example.test",
    "probe_hours": "Mon 09:00-17:00\nTue 09:00-12:00",
}


@pytest.mark.usefixtures("probe_schema")
def test_save_stores_each_canonical_text_and_returns_the_stored_texts(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    result = save(units, setup, FIRST)

    assert result == FIRST_STORED
    assert stored(units) == FIRST_STORED
    assert {row[3] for row in rows(db_engine)} == {"2026-11-17T09:00:00.000000Z"}
    assert logged_changes(caplog) == [",".join(FIRST)]
    for value in ("Europe/London", "ops@example.test", "25.00", "Mon 09:00"):
        assert value not in caplog.text
        assert all(value not in str(vars(record)) for record in caplog.records)


@pytest.mark.usefixtures("probe_schema")
def test_save_writes_only_changed_keys_and_deletes_a_blank_defaulted_key(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    setup: Setup,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    save(units, setup, FIRST)
    before = by_key(db_engine)
    clock.set(T2)
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    result = save(
        units,
        setup,
        {
            **FIRST,
            "probe_cap": "40",  # the same canonical text: unchanged
            "probe_start": "09:15",  # changed
            "probe_budget": "   ",  # blank with a default: back to the default
            "probe_hours": "Mon 09:00-17:00\ntue 09:00-12:00",  # the same once sorted
        },
    )

    after = by_key(db_engine)
    assert result == {
        **{key: text for key, text in FIRST_STORED.items() if key != "probe_budget"},
        "probe_start": "09:15",
    }
    assert "probe_budget" not in after
    assert after["probe_start"][2:] == ("09:15", "2026-11-17T10:30:15.250000Z")
    assert after["probe_start"][0] == before["probe_start"][0]
    for key in ("probe_zone", "probe_cap", "probe_people", "probe_hours"):
        assert after[key] == before[key]  # the same text and updated_at
    assert logged_changes(caplog) == ["probe_start,probe_budget"]
    assert get(units, "probe_budget") == 20_000_000


@pytest.mark.usefixtures("probe_schema")
def test_an_unset_key_sent_back_with_its_default_text_stays_unset(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    defaults = {
        "probe_cap": "30",  # the default text, as the page shows it
        "probe_start": " 07:00 ",
        "probe_budget": "20",  # canonically 20.00, the default
    }

    result = save(units, setup, {**defaults, "probe_zone": "Asia/Tokyo"})

    assert result == {"probe_zone": "Asia/Tokyo"}
    assert set(by_key(db_engine)) == {"probe_zone"}
    assert logged_changes(caplog) == ["probe_zone"]
    assert get(units, "probe_cap") == 30


@pytest.mark.usefixtures("probe_schema")
def test_a_stored_key_set_to_its_default_text_keeps_its_row(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup, clock: FrozenClock
) -> None:
    save(units, setup, {"probe_cap": "45"})
    clock.set(T2)

    assert save(units, setup, {"probe_cap": "30"}) == {"probe_cap": "30"}

    [row] = rows(db_engine)
    assert row[1:] == ("probe_cap", "30", "2026-11-17T10:30:15.250000Z")


@pytest.mark.usefixtures("probe_schema")
def test_saving_nothing_new_writes_nothing_and_logs_nothing(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    setup: Setup,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    save(units, setup, FIRST)
    before = rows(db_engine)
    clock.set(T2)
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    assert save(units, setup, FIRST) == FIRST_STORED

    assert rows(db_engine) == before
    assert logged_changes(caplog) == []


@pytest.mark.usefixtures("probe_schema")
def test_a_blank_required_key_that_was_never_set_stays_unset(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup
) -> None:
    result = save(units, setup, {"probe_zone": "", "probe_hours": " \n ", "probe_cap": "5"})

    assert result == {"probe_cap": "5"}
    assert set(by_key(db_engine)) == {"probe_cap"}
    with pytest.raises(SettingNotSet):
        get(units, "probe_zone")


@pytest.mark.usefixtures("probe_schema")
def test_a_blank_defaulted_key_without_a_row_changes_nothing(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    assert save(units, setup, {"probe_cap": "", "probe_people": ""}) == {}

    assert rows(db_engine) == []
    assert logged_changes(caplog) == []


@pytest.mark.usefixtures("probe_schema")
def test_save_accepts_any_subset_of_the_declared_keys(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    assert save(units, setup, {"probe_zone": "Asia/Tokyo"}) == {"probe_zone": "Asia/Tokyo"}
    assert save(units, setup, {}) == {"probe_zone": "Asia/Tokyo"}


# Row: Save refused ------------------------------------------------------------------------------


@pytest.mark.usefixtures("probe_schema")
def test_one_invalid_field_among_valid_ones_stores_nothing(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    setup: Setup,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    save(units, setup, FIRST)
    before = rows(db_engine)
    clock.set(T2)
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    with pytest.raises(InvalidSettings) as raised:
        save(
            units,
            setup,
            {
                **FIRST,
                "probe_zone": f"Mars/{MARK}",
                "probe_start": "10:00",  # valid, but nothing is stored
                "probe_cap": "0",
                "probe_people": f"{MARK}\n{MARK}",
                "probe_budget": "",  # valid (back to the default), but nothing is stored
            },
        )

    problems = raised.value.problems
    assert {key: problem.rule for key, problem in problems.items()} == {
        "probe_zone": "timezone",
        "probe_cap": "minimum",
        "probe_people": "list",
    }
    assert problems["probe_cap"].reason == "must be at least 1"
    assert raised.value.code == "invalid_settings"
    assert MARK not in str(raised.value)
    assert not any(MARK in str(problem) for problem in problems.values())
    assert rows(db_engine) == before
    assert logged_changes(caplog) == []


@pytest.mark.usefixtures("probe_schema")
def test_a_required_key_that_is_set_cannot_be_cleared(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup
) -> None:
    save(units, setup, FIRST)
    before = rows(db_engine)

    with pytest.raises(InvalidSettings) as raised:
        save(units, setup, {**FIRST, "probe_zone": "  ", "probe_cap": "50"})

    [(key, problem)] = raised.value.problems.items()
    assert (key, problem.rule) == ("probe_zone", "required")
    assert problem.reason == "is required and can't be cleared once set"
    assert rows(db_engine) == before


@pytest.mark.usefixtures("probe_schema")
@pytest.mark.parametrize("unknown", ["nope", "sender_timezone", "Probe_Zone"])
def test_an_unknown_key_refuses_the_whole_save(
    unknown: str, units: UnitOfWorkFactory, db_engine: Engine, setup: Setup
) -> None:
    with pytest.raises(UnknownSetting):
        save(units, setup, {"probe_zone": "Asia/Tokyo", unknown: "x"})

    assert rows(db_engine) == []


@pytest.mark.usefixtures("probe_schema")
def test_a_value_that_isnt_text_is_a_problem(
    units: UnitOfWorkFactory, db_engine: Engine, setup: Setup
) -> None:
    with pytest.raises(InvalidSettings) as raised:
        save(units, setup, {"probe_zone": "Asia/Tokyo", "probe_cap": 40})

    assert raised.value.problems["probe_cap"].rule == "integer"
    assert rows(db_engine) == []


def test_saving_the_real_schema_stores_the_sender_timezone(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    with pytest.raises(InvalidSettings):
        save(units, setup, {"sender_timezone": "Mars/Base"})
    assert stored(units) == {}

    assert save(units, setup, {"sender_timezone": "America/Los_Angeles"}) == {
        "sender_timezone": "America/Los_Angeles"
    }
    assert get(units, Key.SENDER_TIMEZONE) == "America/Los_Angeles"


def test_a_read_only_unit_of_work_refuses_the_use_cases(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    with units.open(read_only=True):
        with pytest.raises(UnitOfWorkError):
            setup.set(Key.SENDER_TIMEZONE, "Europe/London")
        with pytest.raises(UnitOfWorkError):
            setup.save({"sender_timezone": "Europe/London"})

    assert stored(units) == {}
