"""The role adapter registry in ``nextron.main`` (AD-14): ``read_sink_allowlist`` on a real
database at head, and ``build_role_adapter`` end to end for probe registrations: delivered to
an allowlisted recipient, recorded under ``<data dir>\\sink`` for anyone else, unwrapped in
``prod``."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.sink import AlertSink, MailSink
from nextron.adapters.telegram import TelegramAlert
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory, current
from nextron.domain.settings_schema import InvalidSettingValue, Key
from nextron.main import (
    AdapterContext,
    RoleAdapter,
    SinkRole,
    build_role_adapter,
    read_sink_allowlist,
)
from nextron.ports.alert import AlertDelivery, AlertMessage
from nextron.ports.mail import MailDelivery, MailMessage
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.mail import InMemoryMail
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import allow_live_constructor

T = datetime(2026, 11, 17, 23, 59, 59, tzinfo=UTC)


def store_allowlist(units: UnitOfWorkFactory, *entries: str) -> None:
    with units.open():
        Setup(FrozenClock()).set(Key.SINK_ALLOWLIST, entries)


def insert_row(engine: Engine, key: str, value: str) -> None:
    """A stored text written behind ``Setup``'s back, as an older version might have."""
    assert engine.url.database
    writer = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        writer.execute(
            "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            ("01JD3QZ8X5V9K2M7N4P6R8T0WY", key, value, "2026-11-01T00:00:00.000000Z"),
        )
    finally:
        writer.close()


# read_sink_allowlist ----------------------------------------------------------------------------


def test_the_allowlist_is_empty_until_set_then_the_stored_list(units: UnitOfWorkFactory) -> None:
    assert read_sink_allowlist(units) == ()

    store_allowlist(units, "Mac@Example.com", "-100123")

    assert read_sink_allowlist(units) == ("Mac@Example.com", "-100123")


def test_the_allowlist_is_read_while_the_callers_unit_of_work_is_open(
    units: UnitOfWorkFactory,
) -> None:
    store_allowlist(units, "mac@example.com")

    with units.open() as caller:  # a job between its prepare and apply transactions
        assert read_sink_allowlist(units) == ("mac@example.com",)
        assert current() is caller
    with units.open(read_only=True) as caller:
        assert read_sink_allowlist(units) == ("mac@example.com",)
        assert current() is caller
        assert setup_queries.get(Key.SINK_ALLOWLIST) == ("mac@example.com",)


def test_a_stored_allowlist_that_no_longer_passes_raises(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    insert_row(db_engine, "sink_allowlist", "@chan")

    with pytest.raises(InvalidSettingValue) as raised:
        read_sink_allowlist(units)

    assert raised.value.rule == "sink_recipient"


# build_role_adapter -----------------------------------------------------------------------------


class ProbeAlert(InMemoryAlert):
    """A probe "live" alert adapter: what Story 1.12's Telegram adapter will look like."""

    def __init__(self, secrets: Any) -> None:
        super().__init__()
        self.secrets = secrets


class ProbeMail(InMemoryMail):
    """A probe "live" mail adapter."""


@pytest.fixture
def built() -> list[object]:
    """Every probe adapter the registry builds."""
    return []


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch, built: list[object]) -> None:
    """Register one probe alert and one probe mail adapter."""

    def build_alert(context: AdapterContext) -> ProbeAlert:
        built.append(ProbeAlert(context.secrets))
        return built[-1]  # type: ignore[return-value]

    def build_mail(context: AdapterContext) -> ProbeMail:
        built.append(ProbeMail())
        return built[-1]  # type: ignore[return-value]

    monkeypatch.setattr(
        main,
        "ROLE_ADAPTERS",
        (
            RoleAdapter("probe_telegram", SinkRole.ALERT, ProbeAlert, build_alert),
            RoleAdapter("probe_gmail", SinkRole.MAIL, ProbeMail, build_mail),
        ),
    )


def context_for(
    env: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FrozenClock | None = None
) -> AdapterContext:
    monkeypatch.setenv("NEXTRON_ENV", env)
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data" / env))
    clock = clock or FrozenClock(T)
    return AdapterContext(
        load_bootstrap(), InMemorySecrets(env), clock, InMemoryUsageMeter(clock)
    )


@pytest.mark.usefixtures("probes")
def test_on_dev_an_allowlisted_alert_is_delivered_and_another_lands_in_the_sink(
    units: UnitOfWorkFactory,
    built: list[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_allowlist(units, "-100123")
    context = context_for("dev", tmp_path, monkeypatch)

    alert = build_role_adapter("probe_telegram", context, units=units)

    [probe] = built
    assert isinstance(alert, AlertSink) and alert.wrapped is probe
    assert isinstance(probe, ProbeAlert) and probe.secrets is context.secrets
    delivered = alert.send(AlertMessage("-100123", "2 replies waiting"))
    sunk = alert.send(AlertMessage("-100999", "3 replies waiting"))

    assert delivered == AlertDelivery("alert-1")
    assert sunk == AlertDelivery(None, sink=True)
    assert probe.sent == [AlertMessage("-100123", "2 replies waiting")]
    sink_file = tmp_path / "data" / "dev" / "sink" / "2026-11-17.jsonl"
    [line] = sink_file.read_text(encoding="ascii").splitlines()
    assert json.loads(line) == {
        "time": "2026-11-17T23:59:59.000000Z",
        "port": "alert",
        "recipient": "-100999",
        "text": "3 replies waiting",
    }
    assert [path.name for path in (tmp_path / "data").iterdir()] == ["dev"]


@pytest.mark.usefixtures("probes")
def test_on_test_an_allowlisted_email_is_delivered_and_another_lands_in_the_sink(
    units: UnitOfWorkFactory, built: list[object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_allowlist(units, "Mac@Example.com")
    context = context_for("test", tmp_path, monkeypatch)

    mail = build_role_adapter("probe_gmail", context, units=units)

    [probe] = built
    assert isinstance(mail, MailSink) and mail.wrapped is probe
    assert mail.send(MailMessage("mac@example.com", "Test", "Hi")) == MailDelivery(
        "mail-1", "thread-1"
    )
    assert mail.send(MailMessage("ann@prospect.example", "Hello", "Hi Ann")) == MailDelivery(
        None, None, sink=True
    )
    assert len(probe.sent) == 1  # type: ignore[attr-defined]
    sink_file = tmp_path / "data" / "test" / "sink" / "2026-11-17.jsonl"
    assert json.loads(sink_file.read_text(encoding="ascii"))["recipient"] == "ann@prospect.example"


@pytest.mark.usefixtures("probes")
def test_a_change_to_the_allowlist_applies_to_the_next_send(
    units: UnitOfWorkFactory, built: list[object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert = build_role_adapter(
        "probe_telegram", context_for("dev", tmp_path, monkeypatch), units=units
    )

    assert alert.send(AlertMessage("-100123", "one")).sink is True  # type: ignore[arg-type]
    store_allowlist(units, "-100123")  # Mac saves the Settings page
    assert alert.send(AlertMessage("-100123", "two")).sink is False  # type: ignore[arg-type]


@pytest.mark.usefixtures("probes")
def test_an_unreadable_allowlist_sinks_the_message(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    built: list[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insert_row(db_engine, "sink_allowlist", "@chan")  # no longer passes its rule
    alert = build_role_adapter(
        "probe_telegram", context_for("dev", tmp_path, monkeypatch), units=units
    )

    delivery = alert.send(AlertMessage("-100123", "one"))  # type: ignore[arg-type]

    assert delivery == AlertDelivery(None, sink=True)
    assert built[0].sent == []  # type: ignore[attr-defined]


@pytest.mark.usefixtures("probes")
@pytest.mark.parametrize("name", ["probe_telegram", "probe_gmail"])
def test_in_prod_the_live_adapter_is_returned_unwrapped(
    name: str,
    units: UnitOfWorkFactory,
    built: list[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = build_role_adapter(name, context_for("prod", tmp_path, monkeypatch), units=units)

    assert [adapter] == built
    assert type(adapter) in (ProbeAlert, ProbeMail)


@pytest.mark.usefixtures("probes")
@pytest.mark.parametrize("name", ["telegram", "", "PROBE_TELEGRAM"])
def test_an_unknown_name_raises_lookup_error(
    name: str,
    units: UnitOfWorkFactory,
    built: list[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(LookupError):
        build_role_adapter(name, context_for("dev", tmp_path, monkeypatch), units=units)

    assert built == []


def test_a_name_registered_twice_raises_lookup_error(
    units: UnitOfWorkFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = RoleAdapter("twice", SinkRole.ALERT, InMemoryAlert, lambda context: InMemoryAlert())
    monkeypatch.setattr(main, "ROLE_ADAPTERS", (entry, entry))

    with pytest.raises(LookupError, match="2 adapters are registered as 'twice'"):
        build_role_adapter("twice", context_for("dev", tmp_path, monkeypatch), units=units)


@pytest.mark.parametrize(
    ("adapter_type", "returned", "message"),
    [
        (ProbeAlert, InMemoryAlert(), "built a InMemoryAlert, not a ProbeAlert"),
        (object, object(), "does not implement AlertPort"),
    ],
)
def test_a_wrong_adapter_type_raises_type_error(
    adapter_type: type,
    returned: object,
    message: str,
    units: UnitOfWorkFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = RoleAdapter("probe", SinkRole.ALERT, adapter_type, lambda context: returned)
    monkeypatch.setattr(main, "ROLE_ADAPTERS", (entry,))

    for env in ("dev", "prod"):
        with pytest.raises(TypeError, match=message):
            build_role_adapter("probe", context_for(env, tmp_path, monkeypatch), units=units)


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "", "role": SinkRole.ALERT, "adapter_type": InMemoryAlert},
        {"name": "x", "role": "alert", "adapter_type": InMemoryAlert},
        {"name": "x", "role": SinkRole.MAIL, "adapter_type": InMemoryMail()},
    ],
)
def test_a_malformed_registration_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(TypeError):
        RoleAdapter(build=lambda context: None, **fields)


def test_a_registration_needs_a_callable_build() -> None:
    with pytest.raises(TypeError):
        RoleAdapter("x", SinkRole.ALERT, InMemoryAlert, "not callable")  # type: ignore[arg-type]


def test_the_registry_holds_telegram_and_the_roles_are_mail_and_alert() -> None:
    [telegram] = main.ROLE_ADAPTERS
    assert (telegram.name, telegram.role, telegram.adapter_type) == (
        "telegram",
        SinkRole.ALERT,
        TelegramAlert,
    )
    assert [role.value for role in SinkRole] == ["mail", "alert"]
    assert main.SINK_DIR_NAME == "sink"


def test_the_telegram_registration_builds_its_adapter_on_the_contexts_secrets_and_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow_live_constructor(monkeypatch, "nextron.main", "telegram")
    context = context_for("prod", tmp_path, monkeypatch)
    [telegram] = main.ROLE_ADAPTERS

    adapter = telegram.build(context)

    assert type(adapter) is TelegramAlert
    assert adapter._secrets is context.secrets  # type: ignore[attr-defined]
    assert adapter._meter is context.meter  # type: ignore[attr-defined]
    assert adapter._http is None  # type: ignore[attr-defined]  # no client until its first send
