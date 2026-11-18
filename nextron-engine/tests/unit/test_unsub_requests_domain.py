"""``nextron.domain.unsub_requests`` (Story 3.11): the pending format, the poll periods, the
Worker host rule, the outcomes, and the ``unsub_worker_host`` setting built on them."""

from datetime import timedelta

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
