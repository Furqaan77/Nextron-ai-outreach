"""``Sending.register_inbox`` and ``queries.sending`` (FR-32, AD-3, AD-4, AD-14, AD-21, Story
4.7) on a temporary database at head, with ``FrozenClock`` and the real subscriber registry:
every I/O matrix row with its rows and ``events``, nothing stored after each refusal, the
``prod`` and ``test`` paths, an invalid stored allowlist, the logs, the binding query over rows
raw-inserted in each status, and the checks, unique indexes and foreign key on raw writes (the
nextronai.com checks included)."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.queries import sending as sending_queries
from nextron.app.queries.sending import SendingInboxView
from nextron.app.sending.api import RegisteredInbox, Sending
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.sending_inbox import InboxRefused, InboxStatus, Refusal
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
STAMP = "2026-11-17T09:00:00.000000Z"
LOGGER = "nextron.app.sending.api"
SENDER = "Furqaan Ahmed"
FIRST = "furqaan@nextron-ai.com"
INBOX_COLUMNS = "id, address, label, sending_domain_id, from_name, status, created_at"


@dataclass
class World:
    """``Sending`` on real units of work with the registry ``main`` builds."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    env: str

    def sending(self) -> Sending:
        return Sending(self.clock, env=self.env)

    def register(self, address: str, label: str = "Main") -> RegisteredInbox:
        with self.units.open():
            return self.sending().register_inbox(address, label)

    def refused(self, address: str, label: str = "Main") -> Refusal:
        before = self.dump()
        with pytest.raises(InboxRefused) as caught:
            self.register(address, label)
        assert self.dump() == before  # nothing stored
        assert str(caught.value) not in (address, label)
        return caught.value.refusal

    def save(self, texts: dict[str, str]) -> None:
        with self.units.open():
            Setup(self.clock).save(texts)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def inboxes(self) -> list[tuple[Any, ...]]:
        return self.sql(f"SELECT {INBOX_COLUMNS} FROM sending_inboxes ORDER BY rowid")

    def domains(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT id, domain, created_at FROM sending_domains ORDER BY rowid")

    def events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events WHERE entity = 'sending_inboxes' ORDER BY rowid"
        )

    def dump(self) -> tuple[list[Any], ...]:
        return (
            self.sql("SELECT * FROM sending_inboxes ORDER BY id"),
            self.sql("SELECT * FROM sending_domains ORDER BY id"),
            self.sql("SELECT * FROM events ORDER BY rowid"),
        )

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()


def world_on(db_engine: Engine, env: str) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    return World(units, db_engine, clock, env)


@pytest.fixture
def world(db_engine: Engine) -> World:
    """``prod``, with the Sender's name set."""
    found = world_on(db_engine, "prod")
    found.save({"sender_name": SENDER})
    return found


def created_event(inbox_id: str, at: str = STAMP) -> tuple[Any, ...]:
    return ("sending_inboxes", inbox_id, None, "connecting", "registered", None, "operator", at)


def records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == event]


# Register -------------------------------------------------------------------------------------


def test_register_stores_the_inbox_at_connecting_with_its_domain_and_event(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    result = world.register("Furqaan@Nextron-AI.com", "  Qzxlabel  ")

    [domain] = world.domains()
    assert domain[1:] == ("nextron-ai.com", STAMP)
    assert result == RegisteredInbox(result.inbox_id, domain[0], "nextron-ai.com", True)
    assert world.inboxes() == [
        (result.inbox_id, FIRST, "Qzxlabel", domain[0], SENDER, "connecting", STAMP)
    ]
    assert world.events() == [created_event(result.inbox_id)]
    [logged] = records(caplog, "sending_inbox_registered")
    fields = vars(logged)
    assert (fields["inbox_id"], fields["sending_domain_id"], fields["domain_created"]) == (
        result.inbox_id,
        domain[0],
        True,
    )
    shown = json.dumps([vars(record) for record in caplog.records], default=str)
    for planted in ("furqaan", "nextron-ai", "qzxlabel"):
        assert planted not in shown.lower()


def test_the_same_domain_again_reuses_its_row(world: World) -> None:
    first = world.register(FIRST)
    world.clock.advance(timedelta(minutes=1))

    second = world.register("mac@nextron-ai.com", "Second")

    assert second.domain_created is False
    assert second.sending_domain_id == first.sending_domain_id
    assert len(world.domains()) == 1
    assert [row[1] for row in world.inboxes()] == [FIRST, "mac@nextron-ai.com"]
    assert world.events() == [
        created_event(first.inbox_id),
        created_event(second.inbox_id, "2026-11-17T09:01:00.000000Z"),
    ]


def test_a_fourth_inbox_on_a_domain_is_stored(world: World) -> None:
    for name in ("a", "b", "c", "d"):
        world.register(f"{name}@nextron-ai.com", name)

    assert len(world.inboxes()) == 4
    assert len(world.domains()) == 1


def test_another_domain_gets_its_own_row(world: World) -> None:
    first = world.register(FIRST)
    second = world.register("mac@other-firm.co.uk")

    assert second.domain_created and second.domain == "other-firm.co.uk"
    assert second.sending_domain_id != first.sending_domain_id


def test_from_name_is_copied_when_the_inbox_is_registered(world: World) -> None:
    world.register(FIRST)
    world.save({"sender_name": "Mac Smith"})

    assert [row[4] for row in world.inboxes()] == [SENDER]


# Refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address", ["x@nextronai.com", "x@mail.NextronAI.com", "team@nextronai.com"]
)
def test_the_brand_domain_is_refused(world: World, address: str) -> None:
    assert world.refused(address) is Refusal.BRAND_DOMAIN


@pytest.mark.parametrize("address", ["info@d.com", "Sales@d.com", "info+cold@d.com"])
def test_a_role_address_is_refused(world: World, address: str) -> None:
    assert world.refused(address) is Refusal.ROLE_ADDRESS


def test_a_duplicate_address_is_refused_in_any_case(world: World) -> None:
    world.register(FIRST)

    assert world.refused("FURQAAN@nextron-ai.com", "Other") is Refusal.ALREADY_REGISTERED


@pytest.mark.parametrize("status", ["active", "paused", "auth_failed", "removed"])
def test_a_duplicate_is_refused_at_every_status(world: World, status: str) -> None:
    world.register(FIRST)
    world.sql("UPDATE sending_inboxes SET status = ?", (status,))

    assert world.refused(FIRST) is Refusal.ALREADY_REGISTERED


@pytest.mark.parametrize(
    ("address", "label", "refusal"),
    [
        ("not-an-email", "Main", Refusal.INVALID_ADDRESS),
        ("a@co.uk", "Main", Refusal.INVALID_ADDRESS),
        ("\u00fc@d.com", "Main", Refusal.INVALID_ADDRESS),
        ('"info"@d.com', "Main", Refusal.INVALID_ADDRESS),  # not a dot-atom local part
        ("a..b@d.com", "Main", Refusal.INVALID_ADDRESS),
        ("mac@d.com", "", Refusal.INVALID_LABEL),
        ("mac@d.com", "   ", Refusal.INVALID_LABEL),
        ("mac@d.com", "x" * 81, Refusal.INVALID_LABEL),
        ("mac@d.com", "Main\ninbox", Refusal.INVALID_LABEL),
    ],
)
def test_a_bad_address_or_label_is_refused(
    world: World, address: str, label: str, refusal: Refusal
) -> None:
    assert world.refused(address, label) is refusal


def test_an_unset_sender_name_is_refused(db_engine: Engine) -> None:
    found = world_on(db_engine, "prod")

    assert found.refused(FIRST) is Refusal.SENDER_NAME_UNSET


def test_a_stored_sender_name_that_no_longer_parses_is_refused(world: World) -> None:
    world.sql("UPDATE settings SET value = 'Mac 2' WHERE key = 'sender_name'")

    assert world.refused(FIRST) is Refusal.SENDER_NAME_UNSET


def test_a_refusal_is_logged_with_its_code_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    world.refused("info+planted@d.com", "Planted label")

    [logged] = records(caplog, "sending_inbox_refused")
    assert logged.refusal == "role_address"  # type: ignore[attr-defined]
    assert records(caplog, "sending_inbox_registered") == []
    shown = json.dumps([vars(record) for record in caplog.records], default=str)
    assert "planted" not in shown.lower()


@pytest.mark.parametrize(
    "refusal",
    [Refusal.ALREADY_REGISTERED, Refusal.NOT_ALLOWLISTED, Refusal.SENDER_NAME_UNSET],
)
def test_a_refusal_inside_the_transaction_is_logged_with_its_code_only(
    db_engine: Engine, caplog: pytest.LogCaptureFixture, refusal: Refusal
) -> None:
    address, label = "plantedperson@nextron-ai.com", "Plantedlabel"
    found = world_on(db_engine, "test" if refusal is Refusal.NOT_ALLOWLISTED else "prod")
    if refusal is not Refusal.SENDER_NAME_UNSET:
        found.save({"sender_name": SENDER})
    if refusal is Refusal.ALREADY_REGISTERED:
        found.register(address, label)
    caplog.clear()
    caplog.set_level(logging.INFO)

    assert found.refused(address, label) is refusal

    [logged] = records(caplog, "sending_inbox_refused")
    assert vars(logged)["refusal"] == str(refusal)
    assert records(caplog, "sending_inbox_registered") == []
    shown = json.dumps([vars(record) for record in caplog.records], default=str)
    assert "plantedperson" not in shown.lower() and "plantedlabel" not in shown.lower()


def test_a_non_str_argument_raises_type_error(world: World) -> None:
    with pytest.raises(TypeError):
        world.register(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        world.register(FIRST, 3)  # type: ignore[arg-type]
    assert world.inboxes() == []


# Outside prod: the allowlist (AD-14) ----------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "test"])
def test_outside_prod_an_address_not_on_the_allowlist_is_refused(
    db_engine: Engine, env: str
) -> None:
    found = world_on(db_engine, env)
    found.save({"sender_name": SENDER, "sink_allowlist": "other@nextron-ai.com\n-1001234567890"})

    assert found.refused(FIRST) is Refusal.NOT_ALLOWLISTED


def test_outside_prod_an_empty_allowlist_refuses_everything(db_engine: Engine) -> None:
    found = world_on(db_engine, "test")
    found.save({"sender_name": SENDER})

    assert found.refused(FIRST) is Refusal.NOT_ALLOWLISTED


def test_in_test_an_allowlisted_address_in_any_ascii_case_is_registered(
    db_engine: Engine,
) -> None:
    found = world_on(db_engine, "test")
    found.save({"sender_name": SENDER, "sink_allowlist": "Furqaan@NEXTRON-AI.com"})

    result = found.register("FURQAAN@nextron-ai.com")

    assert found.inboxes()[0][:2] == (result.inbox_id, FIRST)


def test_an_unreadable_allowlist_counts_as_empty(db_engine: Engine) -> None:
    found = world_on(db_engine, "test")
    found.save({"sender_name": SENDER})
    found.sql(
        "INSERT INTO settings (id, key, value, updated_at) VALUES (?, 'sink_allowlist', ?, ?)",
        (new_id(), f"{FIRST}\n@telegram_name", STAMP),
    )

    assert found.refused(FIRST) is Refusal.NOT_ALLOWLISTED


def test_the_allowlist_is_checked_before_the_duplicate_and_the_sender_name(
    db_engine: Engine,
) -> None:
    found = world_on(db_engine, "test")  # no Sender's name, no allowlist

    assert found.refused(FIRST) is Refusal.NOT_ALLOWLISTED


def test_in_prod_the_allowlist_is_never_read(world: World) -> None:
    world.sql(
        "INSERT INTO settings (id, key, value, updated_at) VALUES (?, 'sink_allowlist', ?, ?)",
        (new_id(), "@unreadable", STAMP),
    )

    world.register(FIRST)

    assert len(world.inboxes()) == 1


def test_the_duplicate_check_comes_before_the_sender_name(world: World) -> None:
    world.register(FIRST)
    world.sql("DELETE FROM settings WHERE key = 'sender_name'")

    assert world.refused(FIRST) is Refusal.ALREADY_REGISTERED


# Reads ----------------------------------------------------------------------------------------


def raw_inbox(
    world: World, address: str, status: str, created_at: str, domain_id: str | None = None
) -> str:
    if domain_id is None:
        domain_id = new_id()
        world.sql(
            "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, ?, ?)",
            (domain_id, address.split("@")[1], STAMP),
        )
    inbox_id = new_id()
    world.sql(
        f"INSERT INTO sending_inboxes ({INBOX_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (inbox_id, address, address.split("@")[0], domain_id, SENDER, status, created_at),
    )
    return inbox_id


def test_the_binding_query_returns_only_connecting_and_active_oldest_first(
    world: World,
) -> None:
    ids = {
        status: raw_inbox(world, f"{status}@d{n}.com", status, f"2026-11-17T09:0{9 - n}:00.000000Z")
        for n, status in enumerate(["connecting", "active", "paused", "auth_failed", "removed"])
    }

    bound = world.read(sending_queries.inboxes_for_binding)
    listed = world.read(sending_queries.inboxes)

    assert [view.id for view in bound] == [ids["active"], ids["connecting"]]  # oldest first
    assert [view.status for view in bound] == [InboxStatus.ACTIVE, InboxStatus.CONNECTING]
    assert [view.id for view in listed] == [
        ids["auth_failed"],
        ids["paused"],
        ids["active"],
        ids["connecting"],
    ]


def test_a_tie_on_created_at_is_ordered_by_id(world: World) -> None:
    domain_id = new_id()
    world.sql(
        "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, 'd.com', ?)",
        (domain_id, STAMP),
    )
    ids = [raw_inbox(world, f"m{n}@d.com", "active", STAMP, domain_id) for n in range(3)]

    assert [view.id for view in world.read(sending_queries.inboxes_for_binding)] == sorted(ids)


def test_the_views_carry_every_field(world: World) -> None:
    result = world.register(FIRST, "Main")

    [view] = world.read(sending_queries.inboxes)

    assert view == SendingInboxView(
        id=result.inbox_id,
        address=FIRST,
        label="Main",
        sending_domain_id=result.sending_domain_id,
        domain="nextron-ai.com",
        from_name=SENDER,
        status=InboxStatus.CONNECTING,
        created_at=T0,
    )
    assert world.read(sending_queries.inboxes_for_binding) == (view,)


def test_with_no_inboxes_both_reads_are_empty(world: World) -> None:
    assert world.read(sending_queries.inboxes) == ()
    assert world.read(sending_queries.inboxes_for_binding) == ()


# Raw writes: checks, unique indexes and the foreign key ---------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "sending"},
        {"status": "CONNECTING"},
        {"address": "mac@nextronai.com"},
        {"address": "mac@mail.nextronai.com"},
        {"address": "mac@NEXTRONAI.COM"},
        {"label": None},
        {"from_name": None},
        {"created_at": None},
        {"sending_domain_id": new_id()},  # no such Sending Domain
    ],
)
def test_a_raw_inbox_write_that_breaks_a_rule_raises_integrity_error(
    world: World, changes: dict[str, object]
) -> None:
    domain_id = new_id()
    world.sql(
        "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, 'd.com', ?)",
        (domain_id, STAMP),
    )
    values: dict[str, object] = {
        "id": new_id(),
        "address": "mac@d.com",
        "label": "Main",
        "sending_domain_id": domain_id,
        "from_name": SENDER,
        "status": "connecting",
        "created_at": STAMP,
        **changes,
    }

    with pytest.raises(IntegrityError):
        world.sql(
            f"INSERT INTO sending_inboxes ({', '.join(values)}) VALUES "
            f"({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
    assert world.inboxes() == []


def test_the_status_defaults_to_connecting(world: World) -> None:
    domain_id = new_id()
    world.sql(
        "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, 'd.com', ?)",
        (domain_id, STAMP),
    )
    world.sql(
        "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, "
        "created_at) VALUES (?, 'mac@d.com', 'Main', ?, ?, ?)",
        (new_id(), domain_id, SENDER, STAMP),
    )

    assert [row[5] for row in world.inboxes()] == ["connecting"]


@pytest.mark.parametrize(
    "changes", [{"domain": "nextronai.com"}, {"domain": None}, {"created_at": None}]
)
def test_a_raw_domain_write_that_breaks_a_rule_raises_integrity_error(
    world: World, changes: dict[str, object]
) -> None:
    values: dict[str, object] = {"id": new_id(), "domain": "d.com", "created_at": STAMP, **changes}

    with pytest.raises(IntegrityError):
        world.sql(
            f"INSERT INTO sending_domains ({', '.join(values)}) VALUES (?, ?, ?)",
            tuple(values.values()),
        )
    assert world.domains() == []


def test_the_address_and_the_domain_are_unique(world: World) -> None:
    world.register(FIRST)
    [(domain_id, _, _)] = world.domains()

    with pytest.raises(IntegrityError):
        raw_inbox(world, FIRST, "active", STAMP, domain_id)
    with pytest.raises(IntegrityError):
        world.sql(
            "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, 'nextron-ai.com', ?)",
            (new_id(), STAMP),
        )


def test_a_sending_domain_with_inboxes_cannot_be_deleted(world: World) -> None:
    world.register(FIRST)

    with pytest.raises(IntegrityError):
        world.sql("DELETE FROM sending_domains")
    assert len(world.domains()) == 1
