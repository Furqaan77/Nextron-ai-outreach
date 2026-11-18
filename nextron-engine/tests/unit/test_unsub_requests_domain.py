"""``nextron.domain.unsub_requests`` (Story 3.11): the pending format, the poll periods, the
Worker host rule, the outcomes, and the ``unsub_worker_host`` setting built on them."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.domain import unsub_requests
from nextron.domain.settings_schema import (
    SCHEMA,
    InvalidSettingValue,
    Key,
    Section,
    SettingType,
    parse,
)
from nextron.domain.unsub_requests import (
    PENDING_FORMAT,
    POLL_PERIOD_OTHER,
    POLL_PERIOD_PROD,
    POLL_UNSUBSCRIBES_JOB,
    UnsubOutcome,
    is_worker_host,
    poll_period,
)
from nextron.jobs.triggers import Trigger


def test_the_constants_are_the_specified_ones() -> None:
    assert PENDING_FORMAT == "nextron-unsub-pending/1"
    assert (POLL_PERIOD_PROD, POLL_PERIOD_OTHER) == (timedelta(minutes=5), timedelta(minutes=30))
    assert POLL_UNSUBSCRIBES_JOB == Trigger.POLL_UNSUBSCRIBES.value
    assert [str(outcome) for outcome in UnsubOutcome] == ["applied", "duplicate", "invalid"]


@pytest.mark.parametrize(
    ("env", "period"),
    [
        ("prod", timedelta(minutes=5)),
        ("dev", timedelta(minutes=30)),
        ("test", timedelta(minutes=30)),
        ("PROD", timedelta(minutes=30)),
        ("", timedelta(minutes=30)),
    ],
)
def test_the_poll_period_is_5_minutes_only_in_prod(env: str, period: timedelta) -> None:
    assert poll_period(env) == period


@pytest.mark.parametrize(
    "host",
    [
        "",
        "u.example.com",
        "u.nextron-mail.co.uk",
        "u.a.b",
        "u.x1.example-sender.io",
        "u." + "a" * 63 + ".com",
        "u." + ".".join(["abcdefgh"] * 27) + ".com",  # 249 characters
    ],
)
def test_a_valid_or_blank_host_passes(host: str) -> None:
    assert len(host) <= 253
    assert is_worker_host(host)


@pytest.mark.parametrize(
    "host",
    [
        " ",
        "u.example",
        "example.com",
        "w.u.example.com",
        "uu.example.com",
        "U.example.com",
        "u.Example.com",
        "u.example.com.",
        ".u.example.com",
        "u..example.com",
        "https://u.example.com",
        "u.example.com:443",
        "u.example.com/pending",
        "u.example.com?x=1",
        "u.exa_mple.com",
        "u.-example.com",
        "u.example-.com",
        "u." + "a" * 64 + ".com",
        "u." + ".".join(["abcdefgh"] * 28) + ".com",  # 258 characters
        "u.workers.dev",
        "u.nextron.workers.dev",
        "u.exämple.com",
        "u.example.com\n",
        None,
        7,
        b"u.example.com",
    ],
)
def test_anything_else_fails(host: object) -> None:
    assert not is_worker_host(host)


def test_a_host_under_a_lookalike_of_workers_dev_passes() -> None:
    assert is_worker_host("u.myworkers.dev")
    assert is_worker_host("u.workers.dev.example.com")


def test_the_unsub_worker_host_setting_is_declared_as_specified() -> None:
    setting = SCHEMA[Key.UNSUB_WORKER_HOST]

    assert (setting.type, setting.section, setting.label, setting.default) == (
        SettingType.STRING,
        Section.SENDING,
        "Unsub Worker host",
        "",
    )
    assert not setting.required
    assert [rule.name for rule in setting.rules] == ["max_length", "worker_host"]
    assert parse(setting, "  u.example.com ") == "u.example.com"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("https://u.example.com", "worker_host"),
        ("u.workers.dev", "worker_host"),
        ("nextron.example.org", "worker_host"),
        ("u." + "a" * 260, "max_length"),
    ],
)
def test_the_setting_refuses_a_bad_host(text: str, rule: str) -> None:
    with pytest.raises(InvalidSettingValue) as raised:
        parse(SCHEMA[Key.UNSUB_WORKER_HOST], text)

    assert raised.value.rule == rule
    assert text not in str(raised.value)


def test_the_module_imports_only_the_standard_library() -> None:
    source = unsub_requests.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        imports = [
            line.split()[1]
            for line in handle
            if line.startswith(("import ", "from "))
        ]
    assert all(not name.startswith("nextron") for name in imports)


# Monitoring the poll (Story 3.12) ---------------------------------------------------------------

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def test_the_monitoring_constants() -> None:
    assert unsub_requests.FAILING_AFTER == timedelta(hours=24)
    assert (unsub_requests.KV_LIST_DAILY_LIMIT, unsub_requests.KV_LIST_ALERT_PCT) == (1000, 80)
    assert unsub_requests.DOCTOR_DEADLINE_S == 10.0
    assert (unsub_requests.KV_PROVIDER, unsub_requests.KV_LIST_OPERATION) == (
        "unsub_worker",
        "kv_list",
    )


@pytest.mark.parametrize(
    ("last_success", "since", "failing"),
    [
        (None, None, False),
        (NOW - timedelta(days=9), None, False),  # no episode open
        (None, NOW - timedelta(hours=24), False),  # exactly 24 hours is not over it
        (None, NOW - timedelta(hours=24, seconds=1), True),
        (NOW - timedelta(hours=3), NOW - timedelta(hours=2), False),
        (NOW - timedelta(hours=30), NOW - timedelta(hours=2), True),  # 30 h since the success
    ],
)
def test_polling_failing(
    last_success: datetime | None, since: datetime | None, failing: bool
) -> None:
    assert unsub_requests.polling_failing(last_success, since, NOW) is failing


@pytest.mark.parametrize(
    ("code", "failure"),
    [
        ("unsub_refused", "unsub_refused"),
        ("unsub_bearer_invalid", "unsub_bearer_invalid"),
        ("unsub_unauthorized", "unsub_unauthorized"),
        ("unsub_unavailable", "unsub_unavailable"),
        ("unsub_bad_response", "unsub_bad_response"),
        ("unsub_unreachable", "unsub_unreachable"),
        ("credential_not_set", "unsub_bearer_invalid"),  # no bearer stored
        ("keyring_failed", "internal_error"),
        ("internal_error", "internal_error"),
        (None, "internal_error"),
        (7, "internal_error"),
    ],
)
def test_failure_code_maps_an_error_code_to_a_closed_one(code: object, failure: str) -> None:
    assert unsub_requests.failure_code(code) == failure


def test_the_kv_warning_starts_at_800() -> None:
    assert not unsub_requests.kv_list_warning(799)
    assert unsub_requests.kv_list_warning(800)
    assert unsub_requests.kv_list_warning(1200)


def test_the_texts_and_subjects_hold_only_times_codes_and_counts() -> None:
    since = datetime(2026, 11, 18, 14, 30, 59, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    assert unsub_requests.failing_text(since) == (
        "Unsubscribe polling failing since 2026-11-18 09:00 UTC"
    )
    assert unsub_requests.failing_alert_text(since, "unsub_unauthorized") == (
        "Unsubscribe polling failing since 2026-11-18 09:00 UTC (unsub_unauthorized)"
    )
    assert unsub_requests.kv_list_text(800) == (
        "Unsubscribe Worker KV list calls today: 800 of 1,000"
    )
    assert unsub_requests.failing_subject(NOW) == f"unsub_poll_failing:{int(NOW.timestamp())}"
    assert unsub_requests.kv_list_subject(since) == "kv_list:2026-11-18"
