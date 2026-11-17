"""``nextron.domain.settings_schema``: the text forms (the Types row), the declaration checks,
access through ``setting``/``declared``, and the ``sender_timezone`` and ``sink_allowlist``
declarations (Story 1.8's Setting row). No message ever holds the value it refused."""

import ast
from collections.abc import Mapping
from datetime import UTC, time, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from nextron.domain import settings_schema
from nextron.domain.errors import DomainError
from nextron.domain.settings_schema import (
    REQUIRED,
    InvalidSettings,
    InvalidSettingValue,
    Key,
    Rule,
    Section,
    Setting,
    SettingNotSet,
    SettingType,
    Slot,
    UnknownSetting,
    check_schema,
    declared,
    default_text,
    encode,
    max_length,
    maximum,
    minimum,
    parse,
    schema_problems,
    setting,
)

T = SettingType


def probe(kind: SettingType, *, default: Any = REQUIRED, **extra: Any) -> Setting:
    """A setting of ``kind`` named ``probe``."""
    return Setting(
        key="probe",
        type=kind,
        section=Section.ENGINE,
        label="Probe",
        unit="a unit",
        default=default,
        **extra,
    )


def refusal(kind: SettingType, text: str, **extra: Any) -> InvalidSettingValue:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(probe(kind, **extra), text)
    return raised.value


# Row: Types (parse, then encode) ----------------------------------------------------------------

ACCEPTED = [
    (T.STRING, "  Nextron AI  ", "Nextron AI", "Nextron AI"),
    (T.STRING, "x" * 1000, "x" * 1000, "x" * 1000),
    (T.STRING, "café – ü", "café – ü", "café – ü"),
    (T.INTEGER, "42", 42, "42"),
    (T.INTEGER, "-3", -3, "-3"),
    (T.INTEGER, " 7 ", 7, "7"),
    (T.INTEGER, "-0", 0, "0"),
    (T.INTEGER, "9" * 18, int("9" * 18), "9" * 18),
    (T.BOOLEAN, "true", True, "true"),
    (T.BOOLEAN, "false", False, "false"),
    (T.TIME, "07:00", time(7, 0), "07:00"),
    (T.TIME, "00:00", time(0, 0), "00:00"),
    (T.TIME, "23:59", time(23, 59), "23:59"),
    (T.TIMEZONE, "Europe/London", "Europe/London", "Europe/London"),
    (T.TIMEZONE, "America/Los_Angeles", "America/Los_Angeles", "America/Los_Angeles"),
    (T.TIMEZONE, "UTC", "UTC", "UTC"),
    (T.TIMEZONE, "US/Pacific", "US/Pacific", "US/Pacific"),  # a region link
    (T.DURATION, "P1W", timedelta(days=7), "P7D"),
    (T.DURATION, "P1DT2H", timedelta(days=1, hours=2), "P1DT2H"),
    (T.DURATION, "PT30M", timedelta(minutes=30), "PT30M"),
    (T.DURATION, "PT90M", timedelta(minutes=90), "PT1H30M"),
    (T.DURATION, "PT45S", timedelta(seconds=45), "PT45S"),
    (T.DURATION, "P2DT0H5M", timedelta(days=2, minutes=5), "P2DT5M"),
    (T.DURATION, "PT0S", timedelta(0), "PT0S"),
    (T.DURATION, "P0D", timedelta(0), "PT0S"),
    (T.DURATION, "P999999D", timedelta(days=999_999), "P999999D"),
    (T.MONEY, "20", 20_000_000, "20.00"),
    (T.MONEY, "0.000001", 1, "0.000001"),
    (T.MONEY, "1.5", 1_500_000, "1.50"),
    (T.MONEY, "0", 0, "0.00"),
    (T.MONEY, "12.3456", 12_345_600, "12.3456"),
    (T.MONEY, "999999999.999999", 999_999_999_999_999, "999999999.999999"),
    (T.LIST, "a\r\n\r\nb", ("a", "b"), "a\nb"),
    (T.LIST, "  first \n\n second  \r third", ("first", "second", "third"), "first\nsecond\nthird"),
    (T.LIST, "b\na", ("b", "a"), "b\na"),
    (
        T.AVAILABILITY,
        "mon 09:00-12:00",
        (Slot(0, time(9), time(12)),),
        "Mon 09:00-12:00",
    ),
    (
        T.AVAILABILITY,
        "SUN 10:00 - 11:00\r\nTue 09:00-10:00\n\nTUE 10:00-17:30",
        (
            Slot(1, time(9), time(10)),
            Slot(1, time(10), time(17, 30)),
            Slot(6, time(10), time(11)),
        ),
        "Tue 09:00-10:00\nTue 10:00-17:30\nSun 10:00-11:00",
    ),
]


@pytest.mark.parametrize(("kind", "text", "value", "canonical"), ACCEPTED)
def test_parse_reads_a_text_form_and_encode_returns_the_canonical_text(
    kind: SettingType, text: str, value: object, canonical: str
) -> None:
    found = probe(kind)

    parsed = parse(found, text)

    assert parsed == value and type(parsed) is type(value)
    assert encode(found, parsed) == canonical
    assert parse(found, canonical) == value  # the stored text reads back


REFUSED = [
    (T.STRING, "one\ntwo"),
    (T.STRING, "zero​width"),  # Cf
    (T.STRING, "bidi‮override"),  # Cf
    (T.STRING, "line separator"),  # Zl
    (T.STRING, "para separator"),  # Zp
    (T.STRING, "next\u0085line"),  # Cc
    (T.STRING, "x" * 1001),
    (T.INTEGER, "4.2"),
    (T.INTEGER, "1_000"),
    (T.INTEGER, "+1"),
    (T.INTEGER, "٣"),  # ARABIC-INDIC DIGIT THREE
    (T.INTEGER, "1" * 19),
    (T.INTEGER, "0x10"),
    (T.BOOLEAN, "True"),
    (T.BOOLEAN, "1"),
    (T.BOOLEAN, "yes"),
    (T.TIME, "7:00"),
    (T.TIME, "24:00"),
    (T.TIME, "07:00:00"),
    (T.TIME, "07:60"),
    (T.TIME, "07h00"),
    (T.TIMEZONE, "europe/london"),
    (T.TIMEZONE, "Factory"),
    (T.TIMEZONE, "Mars/Base"),
    (T.TIMEZONE, "Europe/London/"),
    # fixed-offset and legacy names: they don't follow daylight saving
    (T.TIMEZONE, "EST"),
    (T.TIMEZONE, "MST"),
    (T.TIMEZONE, "EST5EDT"),
    (T.TIMEZONE, "GB"),
    (T.TIMEZONE, "Zulu"),
    (T.TIMEZONE, "GMT"),
    (T.TIMEZONE, "Etc/GMT+5"),
    (T.TIMEZONE, "Etc/UTC"),
    (T.DURATION, "P1M"),
    (T.DURATION, "P1Y"),
    (T.DURATION, "PT"),
    (T.DURATION, "P"),
    (T.DURATION, "-PT1M"),
    (T.DURATION, "pt2m"),
    (T.DURATION, "PT1.5S"),
    (T.DURATION, "P1DT"),
    (T.DURATION, "PT5"),
    (T.DURATION, "P1W2D"),
    (T.DURATION, "PT1S2M"),
    (T.DURATION, "P1234567D"),
    (T.DURATION, "P999999W"),  # more than 999,999 days
    (T.DURATION, "P999999DT24H"),
    (T.MONEY, "-1"),
    (T.MONEY, "1,000"),
    (T.MONEY, "$20"),
    (T.MONEY, "1.1234567"),
    (T.MONEY, "20."),
    (T.MONEY, ".5"),
    (T.MONEY, "1000000000"),
    (T.MONEY, "1e3"),
    (T.LIST, "a\nb\na"),
    (T.LIST, "ok\nbad​item"),
    (T.LIST, "\n".join(f"item {n}" for n in range(201))),
    (T.LIST, "x" * 1001),
    (T.AVAILABILITY, "Mon 12:00-09:00"),
    (T.AVAILABILITY, "Mon 09:00-09:00"),
    (T.AVAILABILITY, "Mon 09:00-12:00\nmon 11:00-13:00"),
    (T.AVAILABILITY, "Mon 09:00-12:00\nMon 09:00-12:00"),
    (T.AVAILABILITY, "Monday 09:00-12:00"),
    (T.AVAILABILITY, "Mon 9:00-12:00"),
    (T.AVAILABILITY, "Mon 09:00-24:00"),
    (T.AVAILABILITY, "Xyz 09:00-10:00"),
    (T.AVAILABILITY, "Mon09:00-10:00"),
    (T.AVAILABILITY, "\n".join(f"Mon {h:02d}:00-{h:02d}:01" for h in range(24)) * 9),
]


@pytest.mark.parametrize(("kind", "text"), REFUSED)
def test_parse_refuses_a_malformed_text_with_the_types_rule(kind: SettingType, text: str) -> None:
    refused = refusal(kind, text)

    assert refused.key == "probe"
    assert refused.rule == kind.value
    assert str(refused) == f"probe: {refused.reason} (rule {kind.value})"
    assert isinstance(refused, DomainError) and refused.code == "invalid_setting"


def test_a_slot_that_ends_before_it_starts_names_its_line() -> None:
    refused = refusal(T.AVAILABILITY, "Mon 09:00-10:00\n\nTue 12:00-09:00")

    assert refused.reason == "line 3 must start before it ends"


def test_overlapping_slots_name_their_day() -> None:
    assert refusal(T.AVAILABILITY, "Wed 09:00-12:00\nwed 11:59-13:00").reason == (
        "slots on Wed must not overlap"
    )


def test_a_repeated_list_item_names_its_line() -> None:
    assert refusal(T.LIST, "a\n\nb\na").reason == "line 4 repeats an earlier item"


@pytest.mark.parametrize("kind", list(SettingType))
@pytest.mark.parametrize("text", ["", "   ", "\r\n\t"])
def test_blank_text_breaks_required(kind: SettingType, text: str) -> None:
    refused = refusal(kind, text)

    assert (refused.rule, refused.reason) == ("required", "must not be blank")


@pytest.mark.parametrize("kind", list(SettingType))
@pytest.mark.parametrize("text", [None, 5, b"text"])
def test_parse_refuses_what_isnt_text(kind: SettingType, text: object) -> None:
    refused = refusal(kind, text)  # type: ignore[arg-type]

    assert refused.rule == kind.value


# encode checks the value's type ------------------------------------------------------------------

ENCODE_REFUSED = [
    (T.STRING, 5),
    (T.STRING, None),
    (T.STRING, "two\nlines"),
    (T.INTEGER, True),
    (T.INTEGER, 4.2),
    (T.INTEGER, "42"),
    (T.INTEGER, 10**18),
    (T.BOOLEAN, 1),
    (T.BOOLEAN, "true"),
    (T.BOOLEAN, None),
    (T.TIME, time(7, 0, 1)),
    (T.TIME, time(7, 0, 0, 1)),
    (T.TIME, time(7, 0, tzinfo=UTC)),
    (T.TIME, "07:00"),
    (T.TIMEZONE, 5),
    (T.TIMEZONE, None),
    (T.TIMEZONE, "europe/london"),
    (T.DURATION, timedelta(seconds=-1)),
    (T.DURATION, timedelta(seconds=1, microseconds=1)),
    (T.DURATION, timedelta(days=1_000_000)),
    (T.DURATION, 60),
    (T.DURATION, "PT1M"),
    (T.MONEY, -1),
    (T.MONEY, True),
    (T.MONEY, 1.5),
    (T.MONEY, 10**15),
    (T.LIST, "a"),
    (T.LIST, ("a", 5)),
    (T.LIST, ("a", "")),
    (T.LIST, ("a", "a")),
    (T.LIST, ("a", "b\nc")),
    (T.LIST, tuple(f"item {n}" for n in range(201))),
    (T.AVAILABILITY, (("Mon", "09:00", "10:00"),)),
    (T.AVAILABILITY, (Slot(7, time(9), time(10)),)),
    (T.AVAILABILITY, (Slot(True, time(9), time(10)),)),  # type: ignore[arg-type]
    (T.AVAILABILITY, (Slot(0, time(10), time(9)),)),
    (T.AVAILABILITY, (Slot(0, time(9, 0, 30), time(10)),)),
    (T.AVAILABILITY, (Slot(0, time(9), time(11)), Slot(0, time(10), time(12)))),
    (T.AVAILABILITY, Slot(0, time(9), time(10))),  # a slot, not a tuple of slots
]


@pytest.mark.parametrize(("kind", "value"), ENCODE_REFUSED)
def test_encode_refuses_a_value_of_the_wrong_type_or_shape(
    kind: SettingType, value: object
) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        encode(probe(kind), value)

    assert raised.value.rule == kind.value


ENCODE_NORMALISED = [
    (T.STRING, "  padded  ", "padded"),
    (T.TIMEZONE, " Europe/London ", "Europe/London"),
    (T.LIST, [" a ", "b"], "a\nb"),
    (
        T.AVAILABILITY,
        (Slot(4, time(9), time(10)), Slot(0, time(13), time(14)), Slot(0, time(9), time(12))),
        "Mon 09:00-12:00\nMon 13:00-14:00\nFri 09:00-10:00",
    ),
    (T.DURATION, timedelta(weeks=2), "P14D"),
    (T.MONEY, 20_500_000, "20.50"),
]


@pytest.mark.parametrize(("kind", "value", "canonical"), ENCODE_NORMALISED)
def test_encode_normalises_what_has_one_meaning(
    kind: SettingType, value: object, canonical: str
) -> None:
    assert encode(probe(kind), value) == canonical


@pytest.mark.parametrize(("kind", "value"), [(T.STRING, ""), (T.STRING, "  "), (T.LIST, ())])
def test_encode_refuses_a_value_whose_text_is_blank(kind: SettingType, value: object) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        encode(probe(kind), value)

    assert (raised.value.rule, raised.value.reason) == ("required", "must not be blank")


# Rules ------------------------------------------------------------------------------------------


def test_rules_run_after_the_type_on_the_typed_value() -> None:
    found = probe(T.MONEY, rules=(minimum(1_000_000, "$1.00"), maximum(50_000_000, "$50.00")))

    assert parse(found, "1") == 1_000_000
    assert encode(found, 50_000_000) == "50.00"
    low = refusal(T.MONEY, "0.99", rules=found.rules)
    high = refusal(T.MONEY, "50.000001", rules=found.rules)
    malformed = refusal(T.MONEY, "-5", rules=found.rules)  # the type first

    assert (low.rule, low.reason) == ("minimum", "must be at least $1.00")
    assert (high.rule, high.reason) == ("maximum", "must be at most $50.00")
    assert malformed.rule == "money"
    with pytest.raises(InvalidSettingValue, match=r"\(rule maximum\)"):
        encode(found, 50_000_001)


def test_max_length_counts_characters_or_items() -> None:
    assert refusal(T.STRING, "abcd", rules=(max_length(3),)).rule == "max_length"
    assert parse(probe(T.STRING, rules=(max_length(3),)), "abc") == "abc"
    assert refusal(T.LIST, "a\nb\nc", rules=(max_length(2),)).reason == (
        "must have a length of at most 2"
    )
    assert parse(probe(T.LIST, rules=(max_length(2),)), "a\n\nb") == ("a", "b")


def test_durations_and_times_compare_with_their_own_bounds() -> None:
    rules = (minimum(timedelta(minutes=5), "5 minutes"), maximum(timedelta(days=1), "1 day"))

    assert refusal(T.DURATION, "PT4M59S", rules=rules).reason == "must be at least 5 minutes"
    assert refusal(T.DURATION, "P1DT1S", rules=rules).reason == "must be at most 1 day"
    assert parse(probe(T.DURATION, rules=rules), "P1D") == timedelta(days=1)


def test_a_custom_rule_names_itself() -> None:
    even = Rule("even", "must be even", lambda value: value % 2 == 0)

    assert refusal(T.INTEGER, "3", rules=(even,)).rule == "even"
    assert parse(probe(T.INTEGER, rules=(even,)), "4") == 4


# No message holds the value ---------------------------------------------------------------------

MARK = "QZXVALUE"
LEAKY_TEXTS = [
    (T.STRING, f"{MARK}​"),
    (T.STRING, MARK * 200),
    (T.INTEGER, f"42{MARK}"),
    (T.BOOLEAN, MARK),
    (T.TIME, f"07:{MARK}"),
    (T.TIMEZONE, f"Mars/{MARK}"),
    (T.DURATION, f"P{MARK}"),
    (T.MONEY, f"{MARK}20"),
    (T.LIST, f"ok\n{MARK}\u0000"),
    (T.LIST, f"{MARK}\n{MARK}"),
    (T.AVAILABILITY, f"{MARK} 09:00-10:00"),
]


@pytest.mark.parametrize(("kind", "text"), LEAKY_TEXTS)
def test_no_refusal_message_holds_the_text(kind: SettingType, text: str) -> None:
    refused = refusal(kind, text, rules=())

    assert MARK not in str(refused) and MARK not in refused.reason
    assert MARK not in repr(refused.args)


@pytest.mark.parametrize(
    ("kind", "value", "rules"),
    [
        (T.STRING, MARK, (max_length(3),)),
        (T.STRING, f"{MARK}\n{MARK}", ()),
        (T.INTEGER, MARK, ()),
        (T.TIMEZONE, MARK, ()),
        (T.LIST, (MARK, MARK), ()),
        (T.MONEY, MARK, ()),
    ],
)
def test_no_encode_refusal_holds_the_value(
    kind: SettingType, value: object, rules: tuple[Rule, ...]
) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        encode(probe(kind, rules=rules), value)

    assert MARK not in str(raised.value)


def test_the_errors_carry_codes_and_name_keys_not_values() -> None:
    invalid = InvalidSettingValue("sender_timezone", "timezone", "must be a zone")
    together = InvalidSettings({"a_key": invalid, "b_key": InvalidSettingValue("b_key", "x", "y")})
    unset = SettingNotSet("sender_timezone")

    assert str(invalid) == "sender_timezone: must be a zone (rule timezone)"
    assert (invalid.key, invalid.rule, invalid.reason) == (
        "sender_timezone",
        "timezone",
        "must be a zone",
    )
    assert str(together) == "settings not saved; refused: a_key (rule timezone), b_key (rule x)"
    assert dict(together.problems) == {"a_key": invalid, "b_key": together.problems["b_key"]}
    assert str(unset) == "sender_timezone is not set; set it on the Settings page"
    assert unset.key == "sender_timezone"
    codes = [type(error).code for error in (UnknownSetting("x"), invalid, together, unset)]
    assert codes == ["unknown_setting", "invalid_setting", "invalid_settings", "setting_not_set"]


# Access: setting and declared ------------------------------------------------------------------


@pytest.mark.parametrize("key", ["nope", "Sender_Timezone", "", None, 5])
def test_setting_raises_unknown_setting_for_an_undeclared_key(key: object) -> None:
    with pytest.raises(UnknownSetting) as raised:
        setting(key)  # type: ignore[arg-type]

    assert raised.value.code == "unknown_setting"


def test_unknown_setting_names_only_a_well_formed_key() -> None:
    assert str(UnknownSetting("nope")) == "unknown setting: nope"
    assert UnknownSetting("nope").key == "nope"
    for junk in (f"{MARK} <b>", "x" * 65, "Upper"):
        error = UnknownSetting(junk)
        assert junk not in str(error) and error.key is None
        assert str(error) == "unknown setting: (not a setting name)"


def test_setting_and_declared_read_the_schema_when_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one, two = probe(T.STRING, default=""), probe(T.INTEGER, default=1)
    two = Setting(**{**_fields(two), "key": "probe_two"})
    schema = MappingProxyType({"probe": one, "probe_two": two})
    monkeypatch.setattr(settings_schema, "SCHEMA", schema)

    assert setting("probe") is one
    assert declared() == (one, two)
    with pytest.raises(UnknownSetting):
        setting(Key.SENDER_TIMEZONE)


def _fields(found: Setting) -> dict[str, Any]:
    return {name: getattr(found, name) for name in Setting.__dataclass_fields__}


def test_default_text_is_the_canonical_text_of_the_default_or_none() -> None:
    assert default_text(probe(T.TIMEZONE)) is None
    assert default_text(probe(T.MONEY, default=20_000_000)) == "20.00"
    assert default_text(probe(T.DURATION, default=timedelta(days=7))) == "P7D"
    assert default_text(probe(T.LIST, default=())) == ""
    assert default_text(probe(T.STRING, default="")) == ""


# The declared schema ----------------------------------------------------------------------------


def test_sender_timezone_is_declared_required_in_the_sender_section() -> None:
    found = setting(Key.SENDER_TIMEZONE)

    assert found == Setting(
        key="sender_timezone",
        type=SettingType.TIMEZONE,
        section=Section.SENDER,
        label="Sender's timezone",
        unit="IANA timezone name, such as America/Los_Angeles",
        default=REQUIRED,
    )
    assert found.required and found.rules == () and found.timezone_key is None
    assert setting("sender_timezone") is found


def test_the_declared_keys_in_order() -> None:
    assert [key.value for key in Key] == [
        "sender_timezone",
        "sink_allowlist",
        "bulk_threads",
        "backup_time",
        "backup_rotation_days",
        "backup_offpc_dir",
        "alert_chat_id",
        "quota_hunter_credits",
        "quota_prospeo_credits",
        "quota_places_enterprise",
        "quota_places_pro",
        "quota_job_aggregator",
        "ai_budget_monthly",
        "ai_budget_alert_pct",
    ]
    assert declared() == (
        setting(Key.SENDER_TIMEZONE),
        setting(Key.SINK_ALLOWLIST),
        setting(Key.BULK_THREADS),
        setting(Key.BACKUP_TIME),
        setting(Key.BACKUP_ROTATION_DAYS),
        setting(Key.BACKUP_OFFPC_DIR),
        setting(Key.ALERT_CHAT_ID),
        setting(Key.QUOTA_HUNTER_CREDITS),
        setting(Key.QUOTA_PROSPEO_CREDITS),
        setting(Key.QUOTA_PLACES_ENTERPRISE),
        setting(Key.QUOTA_PLACES_PRO),
        setting(Key.QUOTA_JOB_AGGREGATOR),
        setting(Key.AI_BUDGET_MONTHLY),
        setting(Key.AI_BUDGET_ALERT_PCT),
    )


# Row: Setting (alert_chat_id) -------------------------------------------------------------------

ALERT_CHAT_UNIT = (
    "The numeric id of the Telegram chat that receives ops alerts, such as -1001234567890; user "
    "names (@name) aren't accepted."
)


def test_alert_chat_id_is_a_required_numeric_chat_id_in_the_alerts_section() -> None:
    found = setting(Key.ALERT_CHAT_ID)

    assert (found.key, found.type, found.section) == (
        "alert_chat_id",
        SettingType.STRING,
        Section.ALERTS_AND_RECIPIENTS,
    )
    assert (found.label, found.unit) == ("Telegram alert chat id", ALERT_CHAT_UNIT)
    assert found.required and found.timezone_key is None
    assert [(rule.name, rule.message) for rule in found.rules] == [
        ("telegram_chat_id", "must be a numeric Telegram chat id, such as -1001234567890")
    ]
    assert parse(found, " -1001234567890 ") == "-1001234567890"
    assert encode(found, "42") == "42"


@pytest.mark.parametrize("text", ["@ops_channel", "0", "01", "-", "12 34", "1" * 21, "chat"])
def test_alert_chat_id_refuses_anything_but_a_numeric_id(text: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(setting(Key.ALERT_CHAT_ID), text)

    assert raised.value.rule == "telegram_chat_id"
    assert raised.value.reason == "must be a numeric Telegram chat id, such as -1001234567890"


# Row: Setting (sink_allowlist) ------------------------------------------------------------------

SINK_UNIT = (
    "One email address or numeric Telegram chat id per line. Outside prod, email and alerts "
    "reach only these; everything else is written to the sink folder."
)
SINK_REASON = "each line must be an email address or a numeric Telegram chat id"


def test_sink_allowlist_is_an_empty_list_in_the_engine_section() -> None:
    found = setting(Key.SINK_ALLOWLIST)

    assert (found.key, found.type, found.section) == (
        "sink_allowlist",
        SettingType.LIST,
        Section.ENGINE,
    )
    assert (found.label, found.unit) == ("Dev and test allowlist", SINK_UNIT)
    assert found.default == () and not found.required and found.timezone_key is None
    [rule] = found.rules
    assert (rule.name, rule.message) == ("sink_recipient", SINK_REASON)
    assert default_text(found) == ""


def test_sink_allowlist_stores_email_addresses_and_chat_ids() -> None:
    found = setting(Key.SINK_ALLOWLIST)

    assert parse(found, "a@b.co\n123") == ("a@b.co", "123")
    assert parse(found, " Mac@Example.com \r\n\r\n-100123 ") == ("Mac@Example.com", "-100123")
    assert encode(found, ("a@b.co", "123")) == "a@b.co\n123"


@pytest.mark.parametrize(
    "text",
    [
        "@chan",  # a Telegram user name, which can be handed to someone else
        "a@b",  # one label after the @
        "12a",
        "a@b.co\n@chan",  # one bad line refuses the list
        "0",
        "-0",
        "1" * 21,
        "straße@x.com",  # not ASCII
        "a@x.com, b@y.com",  # two addresses on one line
    ],
)
def test_sink_allowlist_refuses_anything_but_addresses_and_chat_ids(text: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(setting(Key.SINK_ALLOWLIST), text)

    assert (raised.value.key, raised.value.rule, raised.value.reason) == (
        "sink_allowlist",
        "sink_recipient",
        SINK_REASON,
    )
    assert text not in str(raised.value)


def test_sink_allowlist_encode_refuses_an_invalid_entry() -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        encode(setting(Key.SINK_ALLOWLIST), ("a@b.co", "@chan"))

    assert raised.value.rule == "sink_recipient"


# Row: Setting (bulk_threads) --------------------------------------------------------------------

BULK_UNIT = (
    "1 or 2. Threads for crawls, enrichment and other bulk work; a change takes effect when the "
    "engine restarts."
)


def test_bulk_threads_is_an_integer_from_1_to_2_in_the_engine_section() -> None:
    found = setting(Key.BULK_THREADS)

    assert (found.key, found.type, found.section) == (
        "bulk_threads",
        SettingType.INTEGER,
        Section.ENGINE,
    )
    assert (found.label, found.unit) == ("Bulk worker threads", BULK_UNIT)
    assert found.default == 1 and not found.required and found.timezone_key is None
    assert [(rule.name, rule.message) for rule in found.rules] == [
        ("minimum", "must be at least 1"),
        ("maximum", "must be at most 2"),
    ]
    assert default_text(found) == "1"
    assert parse(found, "2") == 2 and encode(found, 1) == "1"


@pytest.mark.parametrize(("text", "rule"), [("0", "minimum"), ("3", "maximum"), ("x", "integer")])
def test_bulk_threads_refuses_anything_but_1_or_2(text: str, rule: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(setting(Key.BULK_THREADS), text)

    assert raised.value.rule == rule


# Row: Settings (backup_time, backup_rotation_days; Story 1.16) ---------------------------------

BACKUP_TIME_UNIT = (
    "HH:MM in the sender's timezone (UTC until that is set). A time missed while the engine was "
    "off is made up once when the engine next starts; a change takes effect when the engine "
    "restarts."
)
ROTATION_UNIT = (
    "7 to 35. Local copies older than this are deleted after each successful backup; the newest "
    "verified copy is always kept. 35 days is the most, because erased data must age out of "
    "backups."
)


def test_backup_time_is_a_time_in_the_senders_timezone_in_the_engine_section() -> None:
    found = setting(Key.BACKUP_TIME)

    assert (found.key, found.type, found.section) == (
        "backup_time",
        SettingType.TIME,
        Section.ENGINE,
    )
    assert (found.label, found.unit) == ("Daily backup time", BACKUP_TIME_UNIT)
    assert found.default == time(10, 0) and not found.required and found.rules == ()
    assert found.timezone_key == "sender_timezone"
    assert default_text(found) == "10:00"
    assert parse(found, "02:30") == time(2, 30) and encode(found, time(23, 59)) == "23:59"


def test_backup_rotation_days_is_an_integer_from_7_to_35_in_the_engine_section() -> None:
    found = setting(Key.BACKUP_ROTATION_DAYS)

    assert (found.key, found.type, found.section) == (
        "backup_rotation_days",
        SettingType.INTEGER,
        Section.ENGINE,
    )
    assert (found.label, found.unit) == ("Keep local backups for (days)", ROTATION_UNIT)
    assert found.default == 35 and not found.required and found.timezone_key is None
    assert [(rule.name, rule.message) for rule in found.rules] == [
        ("minimum", "must be at least 7"),
        ("maximum", "must be at most 35"),
    ]
    assert default_text(found) == "35"
    assert parse(found, "7") == 7 and encode(found, 35) == "35"


@pytest.mark.parametrize(("text", "rule"), [("6", "minimum"), ("36", "maximum"), ("x", "integer")])
def test_backup_rotation_days_refuses_anything_outside_7_to_35(text: str, rule: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(setting(Key.BACKUP_ROTATION_DAYS), text)

    assert raised.value.rule == rule


# Row: Settings (backup_offpc_dir; Story 1.17) --------------------------------------------------

OFFPC_UNIT = (
    r"A folder on another drive or a network share, such as E:\NextronBackups or "
    r"\\nas\backups\nextron. Each daily and manual backup is encrypted with the Backup "
    "passphrase (Credentials page) and copied there; copies older than 35 days are deleted. "
    "Blank keeps backups on this PC only."
)


def test_backup_offpc_dir_is_a_blank_by_default_folder_in_the_engine_section() -> None:
    found = setting(Key.BACKUP_OFFPC_DIR)

    assert (found.key, found.type, found.section) == (
        "backup_offpc_dir",
        SettingType.STRING,
        Section.ENGINE,
    )
    assert (found.label, found.unit) == ("Off-PC backup folder", OFFPC_UNIT)
    assert found.default == "" and not found.required and found.timezone_key is None
    assert [(rule.name, rule.message) for rule in found.rules] == [
        (
            "offpc_folder",
            "must be blank, or a full path to a folder on a drive or a network share, such as "
            r"E:\NextronBackups or \\nas\backups\nextron",
        )
    ]
    assert default_text(found) == ""
    assert parse(found, r" E:\NextronBackups ") == r"E:\NextronBackups"
    assert parse(found, r"\\nas\backups\nextron") == r"\\nas\backups\nextron"


@pytest.mark.parametrize(
    "text",
    [
        r"backups\nextron",
        r"E:backups",
        r"\\?\E:\backups",
        r"E:\a\..\b",
        r"\\nas",
        r"E:\backups:ads",
        r"E:\back*ups",
        r"E:\NUL",
        r"E:\backups.",
    ],
)
def test_backup_offpc_dir_refuses_anything_but_a_full_folder_path(text: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(setting(Key.BACKUP_OFFPC_DIR), text)

    assert raised.value.rule == "offpc_folder"


# Budget and limits (Story 1.13) ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("key", "default", "maximum_message"),
    [
        (Key.QUOTA_HUNTER_CREDITS, 50, "must be at most 100,000"),
        (Key.QUOTA_PROSPEO_CREDITS, 100, "must be at most 100,000"),
        (Key.QUOTA_PLACES_ENTERPRISE, 1_000, "must be at most 1,000"),
        (Key.QUOTA_PLACES_PRO, 0, "must be at most 5,000"),
        (Key.QUOTA_JOB_AGGREGATOR, 0, "must be at most 100,000"),
    ],
)
def test_each_quota_is_an_integer_from_0_in_the_budget_section(
    key: Key, default: int, maximum_message: str
) -> None:
    found = setting(key)

    assert (found.type, found.section, found.default) == (
        SettingType.INTEGER,
        Section.BUDGET_AND_LIMITS,
        default,
    )
    assert not found.required and found.timezone_key is None
    assert [(rule.name, rule.message) for rule in found.rules] == [
        ("minimum", "must be at least 0"),
        ("maximum", maximum_message),
    ]
    assert "UTC calendar month" in found.unit
    assert parse(found, "0") == 0


def test_the_ai_budget_is_money_from_0_to_100_dollars_in_the_budget_section() -> None:
    found = setting(Key.AI_BUDGET_MONTHLY)

    assert (found.type, found.section, found.default) == (
        SettingType.MONEY,
        Section.BUDGET_AND_LIMITS,
        20_000_000,
    )
    assert [(rule.name, rule.message) for rule in found.rules] == [
        ("minimum", "must be at least $0"),
        ("maximum", "must be at most $100"),
    ]
    assert default_text(found) == "20.00"
    assert parse(found, "100") == 100_000_000 and parse(found, "0") == 0
    with pytest.raises(InvalidSettingValue) as raised:
        parse(found, "100.000001")
    assert raised.value.rule == "maximum"


def test_the_ai_alert_percentage_is_an_integer_from_1_to_100_in_the_budget_section() -> None:
    found = setting(Key.AI_BUDGET_ALERT_PCT)

    assert (found.type, found.section, found.default) == (
        SettingType.INTEGER,
        Section.BUDGET_AND_LIMITS,
        80,
    )
    assert [(rule.name, rule.message) for rule in found.rules] == [
        ("minimum", "must be at least 1"),
        ("maximum", "must be at most 100"),
    ]
    assert parse(found, "1") == 1 and parse(found, "100") == 100


@pytest.mark.parametrize(
    ("key", "text", "rule"),
    [
        (Key.QUOTA_HUNTER_CREDITS, "-1", "minimum"),
        (Key.QUOTA_PROSPEO_CREDITS, "-1", "minimum"),
        (Key.QUOTA_PLACES_ENTERPRISE, "-1", "minimum"),
        (Key.QUOTA_PLACES_PRO, "-1", "minimum"),
        (Key.QUOTA_JOB_AGGREGATOR, "-1", "minimum"),
        (Key.AI_BUDGET_ALERT_PCT, "101", "maximum"),
        (Key.AI_BUDGET_ALERT_PCT, "0", "minimum"),
    ],
)
def test_a_budget_setting_out_of_range_is_refused(key: Key, text: str, rule: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(setting(key), text)

    assert raised.value.rule == rule


def test_the_schema_is_read_only_and_in_declaration_order() -> None:
    assert isinstance(settings_schema.SCHEMA, MappingProxyType)
    assert list(settings_schema.SCHEMA) == [key.value for key in Key]
    with pytest.raises(TypeError):
        settings_schema.SCHEMA["x"] = probe(T.STRING)  # type: ignore[index]


def test_the_enums_hold_the_types_and_the_page_sections_in_order() -> None:
    assert [kind.value for kind in SettingType] == [
        "string",
        "integer",
        "boolean",
        "time",
        "timezone",
        "duration",
        "money",
        "list",
        "availability",
    ]
    assert [section.value for section in Section] == [
        "Footer and booking",
        "Sender",
        "Alerts and recipients",
        "Sending",
        "Budget and limits",
        "Engine",
    ]
    assert repr(REQUIRED) == "REQUIRED"


# Declaration checks (schema_problems) ------------------------------------------------------------


def declare(*settings: Setting) -> Mapping[str, Setting]:
    return MappingProxyType({found.key: found for found in settings})


def named(key: str, kind: SettingType, **fields: Any) -> Setting:
    values: dict[str, Any] = {
        "key": key,
        "type": kind,
        "section": Section.ENGINE,
        "label": key.replace("_", " "),
        "unit": "a unit",
        "default": REQUIRED,
    }
    return Setting(**{**values, **fields})


def test_the_declared_schema_has_no_problems() -> None:
    assert schema_problems(settings_schema.SCHEMA) == []
    check_schema(settings_schema.SCHEMA)


def test_a_schema_of_every_type_with_defaults_has_no_problems() -> None:
    schema = declare(
        named("zone", T.TIMEZONE, default="Europe/London"),
        named("name", T.STRING, default=""),
        named("count", T.INTEGER, default=30, rules=(minimum(1, "1"),)),
        named("flag", T.BOOLEAN, default=False),
        named("start", T.TIME, default=time(7, 0), timezone_key="zone"),
        named("wait", T.DURATION, default=timedelta(days=2)),
        named("budget", T.MONEY, default=20_000_000),
        named("people", T.LIST, default=("a", "b")),
        named("empty", T.LIST, default=()),
        named("hours", T.AVAILABILITY, default=(Slot(0, time(9), time(17)),), timezone_key="zone"),
        named("required_time", T.TIME, timezone_key="zone"),
    )

    assert schema_problems(schema, keys=None) == []


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ([named("Bad-Key", T.STRING)], "Bad-Key: the key must match"),
        ([named("x" * 65, T.STRING)], "the key must match"),
        ([named("x" * 32, T.STRING)], "(at most 31 characters)"),  # redaction masks 32 or more
        ([named("9lives", T.STRING)], "the key must match"),
        ([named("k", T.STRING, label="")], "k: the label is empty"),
        ([named("k", T.STRING, unit="  ")], "k: the unit is empty"),
        ([named("k", T.STRING, section="Sender")], "k: the section is not a Section"),
        ([named("k", "string")], "k: the type is not a SettingType"),
        ([named("k", T.STRING, rules=(lambda value: True,))], "k: a rule is not a Rule"),
        ([named("k", T.INTEGER, default="30")], "k: the default breaks rule integer"),
        ([named("k", T.INTEGER, default=True)], "k: the default breaks rule integer"),
        (
            [named("k", T.INTEGER, default=0, rules=(minimum(1, "1"),))],
            "k: the default breaks rule minimum",
        ),
        (
            [named("k", T.DURATION, default=timedelta(1), rules=(minimum(5, "5"),))],
            "k: checking the default raised TypeError",
        ),
        ([named("k", T.STRING, default=" padded ")], "k: the default is not in canonical form"),
        ([named("k", T.LIST, default=["a"])], "k: the default is not in canonical form"),
        ([named("k", T.LIST, default=[])], "k: the default is not in canonical form"),
        (
            [
                named("zone", T.TIMEZONE),
                named(
                    "k",
                    T.AVAILABILITY,
                    timezone_key="zone",
                    default=(Slot(1, time(9), time(10)), Slot(0, time(9), time(10))),
                ),
            ],
            "k: the default is not in canonical form",
        ),
        ([named("k", T.TIME)], "k: a time setting needs a timezone_key naming a timezone"),
        ([named("k", T.AVAILABILITY, timezone_key="nope")], "k: a availability setting needs"),
        (
            [named("name", T.STRING), named("k", T.TIME, timezone_key="name")],
            "k: a time setting needs a timezone_key naming a timezone setting",
        ),
        (
            [named("zone", T.TIMEZONE), named("k", T.STRING, timezone_key="zone")],
            "k: only a time or availability setting names a timezone_key",
        ),
    ],
)
def test_schema_problems_catches_a_bad_declaration(settings: list[Setting], problem: str) -> None:
    problems = schema_problems(declare(*settings), keys=None)

    assert any(problem in found for found in problems), problems
    with pytest.raises(TypeError, match="^settings schema: "):
        check_schema(declare(*settings), keys=None)


def test_a_31_character_key_is_allowed() -> None:
    assert schema_problems(declare(named("x" * 31, T.STRING)), keys=None) == []


def test_every_accepted_timezone_is_a_region_name_or_utc() -> None:
    names = settings_schema.timezone_names()

    assert {"UTC", "America/Los_Angeles", "Europe/London", "US/Pacific"} <= names
    assert all("/" in name or name == "UTC" for name in names)
    assert not any(name.startswith("Etc/") for name in names)
    assert "Factory" not in names


def test_schema_problems_catches_a_mapping_key_that_isnt_the_settings_key() -> None:
    problems = schema_problems({"other": named("k", T.STRING)}, keys=None)

    assert problems == ["other: the setting's key is not its mapping key"]


def test_schema_problems_catches_what_isnt_a_setting() -> None:
    not_a_setting: Any = {"k": "a string"}

    assert schema_problems(not_a_setting, keys=None) == ["k: not a Setting"]


def test_schema_problems_holds_the_real_schema_to_key() -> None:
    schema = declare(named("extra_one", T.STRING))

    assert schema_problems(schema) == [
        (
            "Key members not declared: ai_budget_alert_pct, ai_budget_monthly, alert_chat_id, "
            "backup_offpc_dir, backup_rotation_days, backup_time, bulk_threads, "
            "quota_hunter_credits, quota_job_aggregator, quota_places_enterprise, "
            "quota_places_pro, quota_prospeo_credits, sender_timezone, sink_allowlist"
        ),
        "declared but not in Key: extra_one",
    ]
    assert schema_problems(schema, keys=["extra_one"]) == []


def test_no_declaration_problem_holds_the_default() -> None:
    schema = declare(
        named("k", T.STRING, default=f" {MARK} "), named("j", T.INTEGER, default=MARK)
    )

    assert not any(MARK in problem for problem in schema_problems(schema, keys=None))


def test_a_key_declared_twice_is_refused() -> None:
    with pytest.raises(TypeError, match="setting k is declared twice"):
        settings_schema._declare(named("k", T.STRING), named("k", T.INTEGER))


def test_the_module_checks_its_schema_at_import() -> None:
    tree = ast.parse(Path(settings_schema.__file__).read_text(encoding="utf-8"))
    calls = [
        ast.unparse(node.value)
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]

    assert calls == ["check_schema(SCHEMA)"]
    assert tree.body[-1].lineno > max(
        node.lineno for node in tree.body if isinstance(node, ast.Assign | ast.AnnAssign)
    )
