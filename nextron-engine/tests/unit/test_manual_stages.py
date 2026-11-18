"""Story 9.1's pure rules: ``pipeline_stage.operator_moves`` across all 14 stages, the automatic
stages, and ``nextron.domain.manual_stages`` (deal value and notes parsing, the deal check, the
timezone check and the call time, a daylight-saving gap included)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from nextron.domain.manual_stages import (
    MAX_DEAL_VALUE,
    MICROS_PER_DOLLAR,
    StageRefusal,
    StageRefused,
    call_time,
    check_deal,
    check_timezone,
    clean_notes,
    parse_deal_value,
)
from nextron.domain.pipeline_stage import (
    AUTOMATIC_STAGES,
    OPERATOR_EVENTS,
    REOPEN,
    TRANSITIONS,
    PipelineStage,
    StageCause,
    operator_moves,
)

S = PipelineStage
CLOSES = (S.LOST, S.DISQUALIFIED)
EXPECTED_MOVES = {
    S.NEW: CLOSES,
    S.QUEUED: CLOSES,
    S.CONTACTED: CLOSES,
    S.REPLIED: (S.CALL_BOOKED, *CLOSES),
    S.INTERESTED: (S.CALL_BOOKED, *CLOSES),
    S.CALL_BOOKED: CLOSES,
    S.CALL_HELD: (S.AUDIT_PROPOSED, S.AUDIT_WON, S.BUILD_PROPOSED, S.WON, *CLOSES),
    S.AUDIT_PROPOSED: (S.AUDIT_WON, S.BUILD_PROPOSED, S.WON, *CLOSES),
    S.AUDIT_WON: (S.BUILD_PROPOSED, S.WON, *CLOSES),
    S.BUILD_PROPOSED: (S.WON, *CLOSES),
    S.WON: CLOSES,
    S.NOT_NOW: (S.CALL_BOOKED, *CLOSES),
    S.LOST: (REOPEN,),
    S.DISQUALIFIED: (REOPEN,),
}


@pytest.mark.parametrize("stage", list(PipelineStage))
def test_operator_moves_for_every_stage(stage: PipelineStage) -> None:
    moves = operator_moves(stage)

    assert moves == EXPECTED_MOVES[stage]
    assert not set(moves) & set(AUTOMATIC_STAGES)  # never an automatic stage


def test_every_operator_move_is_a_table_row() -> None:
    for stage in PipelineStage:
        for move in operator_moves(stage):
            if move != REOPEN:
                assert (stage, OPERATOR_EVENTS[PipelineStage(move)]) in TRANSITIONS


def test_the_automatic_stages_and_the_operator_events() -> None:
    assert set(AUTOMATIC_STAGES) == {
        S.NEW,
        S.QUEUED,
        S.CONTACTED,
        S.REPLIED,
        S.INTERESTED,
        S.NOT_NOW,
        S.CALL_HELD,
    }
    assert not set(AUTOMATIC_STAGES) & set(OPERATOR_EVENTS)
    assert set(AUTOMATIC_STAGES) | set(OPERATOR_EVENTS) == set(PipelineStage)
    assert StageCause.DEAL_DETAILS_SAVED == "deal_details_saved"
    assert StageCause.REBOOKED == "rebooked"


@pytest.mark.parametrize("value", ["retired", None, 3, ["new"]])
def test_a_value_that_is_no_stage_has_no_moves(value: object) -> None:
    assert operator_moves(value) == ()  # type: ignore[arg-type]


# Deal value and notes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "micros"),
    [
        ("12000", 12_000_000_000),
        ("  12000 ", 12_000_000_000),
        ("0", 0),
        ("007", 7_000_000),
        ("1000000000", MAX_DEAL_VALUE),
        ("", None),
        ("   ", None),
    ],
)
def test_parse_deal_value(text: str, micros: int | None) -> None:
    assert parse_deal_value(text) == micros


@pytest.mark.parametrize(
    "text", ["-5", "abc", "1.5", "1,000", "$100", "1000000001", "12 000", "١٢", "+5", "1e3"]
)
def test_a_bad_deal_value_is_refused(text: str) -> None:
    with pytest.raises(StageRefused) as caught:
        parse_deal_value(text)

    assert caught.value.reason is StageRefusal.BAD_DEAL_VALUE


def test_clean_notes() -> None:
    assert clean_notes("a\r\nb\rc\nd") == "a\nb\nc\nd"
    assert clean_notes("  keep spaces  ") == "  keep spaces  "
    assert clean_notes("") is None
    assert clean_notes(" \r\n\t") is None
    assert clean_notes("x" * 5_000) == "x" * 5_000
    assert clean_notes("x\r\n" * 2_500) == "x\n" * 2_500  # 5,000 once normalized
    with pytest.raises(StageRefused) as caught:
        clean_notes("x" * 5_001)
    assert caught.value.reason is StageRefusal.NOTES_TOO_LONG


def test_check_deal() -> None:
    check_deal(None, None)
    check_deal(0, "note")
    check_deal(MAX_DEAL_VALUE, "x" * 5_000)
    for bad in (-MICROS_PER_DOLLAR, MAX_DEAL_VALUE + MICROS_PER_DOLLAR, 1):
        with pytest.raises(StageRefused):
            check_deal(bad, None)
    with pytest.raises(StageRefused):
        check_deal(None, "x" * 5_001)
    for wrong in (True, 1.0, "5"):
        with pytest.raises(TypeError):
            check_deal(wrong, None)
    with pytest.raises(TypeError):
        check_deal(None, 5)
    for unnormalized in ("a\r\nb", "  "):
        with pytest.raises(ValueError):
            check_deal(None, unnormalized)


# Timezones and call times ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Europe/London", "America/New_York", "UTC", "Etc/GMT+5"])
def test_a_valid_timezone(name: str) -> None:
    assert check_timezone(name) == ZoneInfo(name)


@pytest.mark.parametrize(
    "name", ["", "Mars/Olympus", "../etc/passwd", "Europe/London ", "/Europe", "x" * 65, None, 5]
)
def test_a_bad_timezone_is_refused(name: object) -> None:
    with pytest.raises(StageRefused) as caught:
        check_timezone(name)

    assert caught.value.reason is StageRefusal.BAD_TIMEZONE


def test_call_time_converts_to_utc() -> None:
    london = ZoneInfo("Europe/London")

    assert call_time("2026-11-20", "14:30", london) == datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
    assert call_time("2026-07-20", "14:30:15", london) == datetime(
        2026, 7, 20, 13, 30, 15, tzinfo=UTC
    )
    # an ambiguous local time (clocks going back) is its first occurrence
    assert call_time("2026-10-25", "01:30", london) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "at"),
    [
        ("2026-03-29", "01:30"),  # skipped: London's clocks go forward at 01:00
        ("2026-02-30", "10:00"),
        ("2026-11-20", "25:00"),
        ("20-11-2026", "10:00"),
        ("2026-11-20", "10am"),
        ("", "10:00"),
        ("2026-11-20", ""),
    ],
)
def test_a_bad_or_skipped_call_time_is_refused(day: str, at: str) -> None:
    with pytest.raises(StageRefused) as caught:
        call_time(day, at, ZoneInfo("Europe/London"))

    assert caught.value.reason is StageRefusal.BAD_DATE_TIME


@pytest.mark.parametrize(
    ("day", "at", "zone"),
    [
        ("0001-01-01", "00:00", "Asia/Tokyo"),  # before year 1 in UTC
        ("9999-12-31", "23:00", "America/New_York"),  # after year 9999 in UTC
    ],
)
def test_a_call_time_outside_the_range_is_refused(day: str, at: str, zone: str) -> None:
    with pytest.raises(StageRefused) as caught:
        call_time(day, at, ZoneInfo(zone))

    assert caught.value.reason is StageRefusal.BAD_DATE_TIME
