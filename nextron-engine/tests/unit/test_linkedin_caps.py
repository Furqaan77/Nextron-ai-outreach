"""``nextron.domain.linkedin_caps`` (Story 10.2): the account's field checks, the cap rules and
``remaining``, with a frozen ``now`` (the spec's matrix rows 5 and 6, the local-day boundary,
negatives clamped to 0 and a naive ``now`` refused)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nextron.domain.linkedin_caps import (
    BAD_TEXT,
    BLANK_TEXT,
    DAILY_OVER_WEEKLY,
    DEFAULT_CAPS,
    LONG_TEXT,
    NEGATIVE_CAP,
    NOT_WHOLE_NUMBER,
    ONE_ACCOUNT,
    TASKS_OFF,
    WEEKLY_CAP_TOO_HIGH,
    AccountCaps,
    AccountRefused,
    CapTask,
    cap_problems,
    check_fields,
    check_text,
    parse_cap,
    remaining,
)

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)  # a Wednesday, 15:00 UTC
LONDON = ZoneInfo("Europe/London")
YESTERDAY = NOW - timedelta(days=1)


def tasks(count: int, at: datetime, status: str = "sent") -> list[CapTask]:
    return [CapTask(status, at) for _ in range(count)]


# Messages and defaults ---------------------------------------------------------------------------


def test_the_messages_are_the_acs() -> None:
    assert WEEKLY_CAP_TOO_HIGH == "At most 100 invitations per account in a rolling week"
    assert ONE_ACCOUNT == (
        "v1 supports one LinkedIn account; its Tasks go to linkedin_owner_chat (PRD OQ-5)"
    )
    assert TASKS_OFF == "LinkedIn Tasks off: set up the LinkedIn account and linkedin_owner_chat"
    assert DEFAULT_CAPS == AccountCaps(10, 10, 100)


# Cap rules ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caps", "problems"),
    [
        (AccountCaps(10, 10, 100), {}),
        (AccountCaps(0, 0, 0), {}),
        (AccountCaps(10, 10, 101), {"weekly_invite_cap": WEEKLY_CAP_TOO_HIGH}),
        (AccountCaps(10, 20, 15), {"daily_invite_cap": DAILY_OVER_WEEKLY}),
        (AccountCaps(-1, 10, 100), {"tasks_per_weekday": NEGATIVE_CAP}),
        (AccountCaps(10, -1, 100), {"daily_invite_cap": NEGATIVE_CAP}),
        (AccountCaps(10, 10, -1), {"weekly_invite_cap": NEGATIVE_CAP}),
        (AccountCaps(10, 101, 101), {"weekly_invite_cap": WEEKLY_CAP_TOO_HIGH}),
    ],
)
def test_cap_problems(caps: AccountCaps, problems: dict[str, str]) -> None:
    assert cap_problems(caps) == problems


@pytest.mark.parametrize(
    ("text", "value"), [("", 100), ("  ", 100), ("7", 7), (" 42 ", 42), ("-1", -1)]
)
def test_parse_cap_reads_blank_as_the_default(text: str, value: int) -> None:
    assert parse_cap("weekly_invite_cap", text) == value


@pytest.mark.parametrize("text", ["ten", "1.5", "1e3", "+3", "1234567890", "٣", None])
def test_parse_cap_refuses_anything_but_a_whole_number(text: object) -> None:
    with pytest.raises(ValueError, match=NOT_WHOLE_NUMBER):
        parse_cap("daily_invite_cap", text)


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("Furqaan's LinkedIn", None),
        ("", BLANK_TEXT),
        ("   ", BLANK_TEXT),
        (None, BLANK_TEXT),
        ("x" * 100, None),
        ("x" * 101, LONG_TEXT),
        ("two\nlines", BAD_TEXT),
        ("Main\n", None),  # the control characters are checked after stripping
        ("\tMain", None),
        ("\r\nMain \t", None),
        ("zero​width", BAD_TEXT),
    ],
)
def test_check_text(value: object, problem: str | None) -> None:
    assert check_text(value) == problem


def test_check_fields_strips_and_defaults() -> None:
    fields = check_fields(" Main ", " Furqaan Ahmed ", " Founder ", {"daily_invite_cap": "5"})

    assert (fields.label, fields.owner_name, fields.owner_role) == (
        "Main",
        "Furqaan Ahmed",
        "Founder",
    )
    assert fields.caps == AccountCaps(10, 5, 100)


def test_check_fields_names_every_problem() -> None:
    with pytest.raises(AccountRefused) as refused:
        check_fields("", "x" * 101, "Founder", {"tasks_per_weekday": "many"})

    assert refused.value.problems == {
        "label": BLANK_TEXT,
        "owner_name": LONG_TEXT,
        "tasks_per_weekday": NOT_WHOLE_NUMBER,
    }
    assert "x" * 10 not in str(refused.value)


# remaining ---------------------------------------------------------------------------------------


def test_matrix_row_5_defaults_with_96_scheduled_in_the_week_and_none_today() -> None:
    week = tasks(96, YESTERDAY)

    assert remaining(DEFAULT_CAPS, week, NOW, LONDON) == 4


def test_matrix_row_6_a_task_just_outside_the_window_doesnt_count() -> None:
    week = tasks(86, YESTERDAY) + tasks(10, NOW - timedelta(days=7, minutes=1))

    assert remaining(DEFAULT_CAPS, week, NOW, LONDON) == 10


def test_a_task_exactly_seven_days_old_is_outside_and_a_future_one_counts() -> None:
    assert remaining(DEFAULT_CAPS, tasks(95, NOW - timedelta(days=7)), NOW, LONDON) == 10
    future = tasks(95, YESTERDAY) + tasks(1, NOW + timedelta(hours=1))
    assert remaining(DEFAULT_CAPS, future, NOW, LONDON) == 4


@pytest.mark.parametrize("status", ["skipped", "cancelled"])
def test_unscheduled_tasks_count_only_toward_tasks_per_weekday(status: str) -> None:
    assert remaining(DEFAULT_CAPS, tasks(96, YESTERDAY, status), NOW, LONDON) == 10
    assert remaining(DEFAULT_CAPS, tasks(7, NOW, status), NOW, LONDON) == 3


@pytest.mark.parametrize("status", ["pending", "sent", "accepted"])
def test_scheduled_today_counts_toward_the_daily_cap(status: str) -> None:
    caps = AccountCaps(tasks_per_weekday=50, daily_invite_cap=10, weekly_invite_cap=100)

    assert remaining(caps, tasks(6, NOW, status), NOW, LONDON) == 4


def test_today_is_the_local_date_in_tz() -> None:
    zone = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 11, 18, 7, 30, tzinfo=UTC)  # 23:30 on the 17th in Los Angeles
    caps = AccountCaps(tasks_per_weekday=50, daily_invite_cap=10, weekly_invite_cap=100)
    local_yesterday = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)  # 01:00 on the 17th there
    utc_yesterday = datetime(2026, 11, 17, 6, 0, tzinfo=UTC)  # 22:00 on the 16th there

    assert remaining(caps, tasks(3, local_yesterday), now, zone) == 7
    assert remaining(caps, tasks(3, utc_yesterday), now, zone) == 10
    assert remaining(caps, tasks(3, local_yesterday), now, UTC) == 10


def test_remaining_is_never_negative() -> None:
    caps = AccountCaps(tasks_per_weekday=2, daily_invite_cap=2, weekly_invite_cap=2)

    assert remaining(caps, tasks(5, NOW), NOW, LONDON) == 0
    assert remaining(AccountCaps(0, 0, 0), [], NOW, LONDON) == 0


def test_remaining_refuses_a_naive_now_or_prepared_at_and_a_bad_tz() -> None:
    with pytest.raises(ValueError, match="now must be an aware datetime"):
        remaining(DEFAULT_CAPS, [], NOW.replace(tzinfo=None), LONDON)
    with pytest.raises(ValueError, match="prepared_at must be an aware datetime"):
        remaining(DEFAULT_CAPS, tasks(1, NOW.replace(tzinfo=None)), NOW, LONDON)
    with pytest.raises(TypeError):
        remaining(DEFAULT_CAPS, [], NOW, "Europe/London")  # type: ignore[arg-type]
