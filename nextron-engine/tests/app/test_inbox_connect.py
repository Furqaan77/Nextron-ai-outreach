"""Connecting a Sending Inbox through Gmail OAuth (``nextron.app.sending.connect``,
``Sending.connect_inbox`` and ``Sending.mark_auth_failed``; Story 6.2, FR-32, AD-12, AD-14,
AD-4) on a temporary database at head, with ``FakeGoogleOAuth``, a fake listener, the test
keyring (``InMemorySecrets``), ``FakeGmail`` and the real subscriber registry.

Every I/O matrix row: a new address, a registered inbox, a reconnect, a different account,
brand and role addresses, the allowlist before and after consent, a state mismatch and a
timeout, the OQ-A2 gate, a listed domain, ``invalid_grant``, a keyring or database failure;
each refusal revokes the new grant and stores nothing (no keyring entry, no row, no event);
the queued ``check_domains`` job; the access token cache; a superseded flow; doctor's
``check_grant``; the health issues; and a log-redaction check over every path. Also (review
patches): an unexpected crash after the exchange revokes; a failed revoke; no address in a
``repr``; a crowded domain; a pause restored on reconnect; the newest pause cause; a failing
``mark_auth_failed``; and ``status_changed_at`` after each later transition."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.queries import sending as sending_queries
from nextron.app.sending.api import Sending
from nextron.app.sending import connect as connect_module
from nextron.app.sending.checks import DomainChecks
from nextron.app.sending.connect import (
    GMAIL_AUTH_FAILED,
    GMAIL_NOT_CONNECTED,
    ConnectOutcome,
    GrantProblem,
    InboxConnections,
    Revocation,
)
from nextron.app.setup.api import Setup
from nextron.db import eventlog
from nextron.db.models.base import new_id
from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.google_oauth import (
    CLIENT_SECRET_NAME,
    ClientConfig,
    encode_client_config,
)
from nextron.domain.reputation import SPAMHAUS_DBL, ZoneOutcome, encode_dnsbl
from nextron.domain.sending_inbox import (
    GMAIL_SCOPE,
    ConnectFailure,
    ConnectRefused,
    InboxStatus,
    PauseCause,
)
from nextron.ports.errors import AdapterError
from nextron.ports.gmail import GMAIL_UNAVAILABLE
from nextron.web import inboxes_page
from nextron.ports.google_oauth import GOOGLE_INVALID_GRANT, GOOGLE_UNAVAILABLE, TokenSet
from tests.fakes.clock import FrozenClock
from tests.fakes.dns import FakeDns
from tests.fakes.gmail import FakeGmail
from tests.fakes.google_oauth import CLIENT_ID, FakeConsentListener, FakeGoogleOAuth
from tests.fakes.postmaster import FakePostmaster
from tests.fakes.secrets import InMemorySecrets

T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
STAMP = "2026-11-19T09:00:00.000000Z"
ADDRESS = "furqaan@nextron-mail.com"
DOMAIN = "nextron-mail.com"
SENDER = "Furqaan Ahmed"
ACCESS = "ya29.new-access-Qz7"
REFRESH = "1//new-refresh-Kx9"
OLD_REFRESH = "1//old-refresh-Pw3"
CODE = "4/0auth-code-Vb2"
SECRET_NAME = f"gmail/{ADDRESS}"
CLIENT = ClientConfig(CLIENT_ID, "GOCSPX-fake-secret")
LOGGERS = ("nextron.app.sending.connect", "nextron.app.sending.api")


def no_jobs() -> Any:
    raise AssertionError("no job runs in these tests")


def tokens(
    *,
    access: str = ACCESS,
    refresh: str | None = REFRESH,
    scope: str = GMAIL_SCOPE,
    expires_in: int = 3599,
) -> TokenSet:
    return TokenSet(access_token=access, expires_in=expires_in, scope=scope, refresh_token=refresh)


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    env: str
    secrets: InMemorySecrets = field(default_factory=InMemorySecrets)
    oauth: FakeGoogleOAuth = field(default_factory=FakeGoogleOAuth)
    gmail: FakeGmail = field(default_factory=FakeGmail)
    listeners: list[FakeConsentListener] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    jobs: list[Any] = field(default_factory=list)
    queue: JobQueue = field(init=False)
    notify: Notify = field(init=False)
    checks: DomainChecks = field(init=False)
    sending: Sending = field(init=False)
    connections: InboxConnections = field(init=False)

    def __post_init__(self) -> None:
        job_types = main.build_job_types(
            alerts=lambda: self.notify,
            backups=no_jobs,
            restore_tests=no_jobs,
            recovery=no_jobs,
            contact_markets=no_jobs,
            crawls=no_jobs,
            discovery=no_jobs,
            research=no_jobs,
            extractions=no_jobs,
            primary=no_jobs,
            test_runs=no_jobs,
            job_boards=no_jobs,
            legal_forms=no_jobs,
            scores=no_jobs,
            domain_checks=lambda: self.checks,
        )
        self.queue = JobQueue(self.clock, job_types)
        self.notify = Notify(self.clock, self.queue, alert_ports={})
        self.checks = DomainChecks(
            self.clock, FakeDns(), self.queue, postmaster=FakePostmaster(), notify=self.notify
        )
        self.sending = Sending(
            self.clock, env=self.env, domain_checks=self.checks, notify=self.notify
        )
        self.connections = InboxConnections(
            self.secrets,
            self.clock,
            oauth=self.oauth,
            listen=self.listen,
            browser=self.browser,
            gmail=self.gmail,
            env=self.env,
            sending=self.sending,
            open_unit=self.units.open,
            start=self.jobs.append,
        )
        self.secrets.write(CLIENT_SECRET_NAME, encode_client_config(CLIENT))
        self.gmail.profiles[ACCESS] = ADDRESS
        self.oauth.exchanges.append(tokens())

    def listen(self) -> FakeConsentListener:
        self.listeners.append(FakeConsentListener())
        return self.listeners[-1]

    def browser(self, url: str) -> bool:
        self.urls.append(url)
        return True

    def save(self, texts: dict[str, str]) -> None:
        with self.units.open():
            Setup(self.clock).save(texts)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def answer(self, *, state: str | None = None, code: str = CODE) -> None:
        """Google's answer to the newest flow: its own ``state`` unless another is given."""
        query = parse_qs(urlsplit(self.urls[-1]).query)
        self.listeners[-1].answer = {
            "state": (query["state"][0] if state is None else state,),
            "code": (code,),
        }

    def run(self) -> None:
        jobs, self.jobs[:] = list(self.jobs), []
        for job in jobs:
            job()

    def connect(self, address: str = ADDRESS, label: str = "Main") -> ConnectOutcome | None:
        """Start a connect, let Google answer and run the flow; the outcome."""
        self.connections.start_connect(address, label)
        self.answer()
        self.run()
        return self.connections.status().outcome

    def inboxes(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT id, address, status, from_name, status_changed_at, connected_at "
            "FROM sending_inboxes ORDER BY rowid"
        )

    def events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity_id, from_status, to_status, cause, cause_ref, actor FROM events "
            "WHERE entity = 'sending_inboxes' ORDER BY rowid"
        )

    def jobs_queued(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT type, idempotency_key, payload FROM jobs ORDER BY rowid")

    def alerts(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT kind, subject, text FROM alerts ORDER BY rowid")

    def dump(self) -> tuple[list[Any], ...]:
        return (
            self.sql("SELECT * FROM sending_inboxes ORDER BY id"),
            self.sql("SELECT * FROM sending_domains ORDER BY id"),
            self.sql("SELECT * FROM events ORDER BY rowid"),
            self.sql("SELECT * FROM jobs ORDER BY rowid"),
        )

    def stored_token(self) -> str | None:
        return self.secrets.read(SECRET_NAME)

    def read(self, fn: Any) -> Any:
        with self.units.open(read_only=True):
            return fn()

    def register(self, address: str = ADDRESS) -> str:
        with self.units.open():
            return self.sending.register_inbox(address, "Main").inbox_id


def world_on(db_engine: Engine, env: str) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    return World(units, db_engine, clock, env)


@pytest.fixture
def world(db_engine: Engine) -> World:
    """``dev``, the gate on, the Sender's name set and the address allowlisted."""
    found = world_on(db_engine, "dev")
    found.save(
        {
            "sender_name": SENDER,
            "sink_allowlist": ADDRESS,
            "gmail_internal_app_confirmed": "true",
        }
    )
    return found


def refused_before_the_flow(world: World, failure: ConnectFailure, **kwargs: str) -> None:
    before = world.dump()
    with pytest.raises(ConnectRefused) as caught:
        world.connections.start_connect(
            kwargs.get("address", ADDRESS), kwargs.get("label", "Main")
        )
    assert caught.value.failure is failure
    assert world.listeners == [] and world.urls == [] and world.jobs == []  # nothing started
    assert world.dump() == before and world.stored_token() is None
    assert world.oauth.calls == []


def refused_after_consent(
    world: World, outcome: ConnectOutcome | None, failure: ConnectFailure
) -> None:
    assert outcome is not None and outcome.failure is failure
    assert outcome.revocation is Revocation.REVOKED
    assert REFRESH in world.oauth.revoked  # the new grant is let go
    assert world.stored_token() is None
    assert world.inboxes() == [] and world.events() == [] and world.jobs_queued() == []


# The happy paths ----------------------------------------------------------------------------


def test_a_new_address_is_connected_with_its_domain_events_and_check(world: World) -> None:
    outcome = world.connect()

    assert outcome is not None and outcome.failure is None and outcome.paused is None
    assert world.stored_token() == REFRESH  # only the refresh token goes to keyring
    [(inbox_id, address, status, from_name, changed, connected)] = world.inboxes()
    assert outcome.inbox_id == inbox_id
    assert (address, status, from_name, changed, connected) == (
        ADDRESS,
        "active",
        SENDER,
        STAMP,
        STAMP,
    )
    assert world.sql("SELECT domain FROM sending_domains") == [(DOMAIN,)]
    assert world.events() == [
        (inbox_id, None, "connecting", "registered", None, "operator"),
        (inbox_id, "connecting", "active", "connected", None, "operator"),
    ]
    [(job_type, key, payload)] = world.jobs_queued()
    [(domain_id,)] = world.sql("SELECT id FROM sending_domains")
    assert (job_type, key) == ("check_domains", f"check_domains:{domain_id}:tick")
    assert domain_id in payload
    assert world.oauth.revoked == []  # nothing to let go
    assert world.connections.status().waiting_until is None


def test_the_consent_url_asks_for_gmail_modify_only_with_pkce(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Main")

    query = parse_qs(urlsplit(world.urls[-1]).query)
    assert query["scope"] == [GMAIL_SCOPE]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["http://127.0.0.1:53682/"]
    status = world.connections.status()
    assert status.waiting_for == ADDRESS
    assert status.waiting_until == T0 + timedelta(minutes=5)
    world.answer()
    world.run()
    assert world.listeners[-1].waited == [300.0] and world.listeners[-1].closed
    verifier = world.oauth.verifiers[-1]
    assert verifier not in world.urls[-1]  # the verifier never leaves the engine


def test_a_registered_connecting_inbox_moves_to_active_with_a_fresh_from_name(
    world: World,
) -> None:
    inbox_id = world.register()
    world.save({"sender_name": "Furqaan A. Ahmed"})
    world.clock.advance(timedelta(minutes=30))

    outcome = world.connect(label="Ignored label")

    assert outcome is not None and outcome.failure is None and outcome.inbox_id == inbox_id
    [row] = world.inboxes()
    assert row[2:4] == ("active", "Furqaan A. Ahmed")
    later = "2026-11-19T09:30:00.000000Z"
    assert row[4:6] == (later, later)  # status_changed_at and connected_at: the connect's time
    assert world.sql("SELECT label FROM sending_inboxes") == [("Main",)]
    assert [event[1:4] for event in world.events()] == [
        (None, "connecting", "registered"),
        ("connecting", "active", "connected"),
    ]


def test_the_row_connect_starts_a_flow_for_a_connecting_inbox(world: World) -> None:
    inbox_id = world.register()

    world.connections.start_reconnect(inbox_id)
    world.answer()
    world.run()

    assert world.connections.status().outcome == ConnectOutcome(ADDRESS, None, inbox_id)
    assert world.inboxes()[0][2] == "active"


def test_a_reconnect_moves_auth_failed_to_active_and_overwrites_the_old_token(
    world: World,
) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    world.secrets.write(SECRET_NAME, OLD_REFRESH)
    world.sql("UPDATE sending_inboxes SET status = 'auth_failed'")
    world.oauth.exchanges.append(tokens())
    world.clock.advance(timedelta(hours=1))

    world.connections.start_reconnect(inbox_id)
    world.answer()
    world.run()

    outcome = world.connections.status().outcome
    assert outcome == ConnectOutcome(ADDRESS, None, inbox_id)
    assert world.stored_token() == REFRESH
    # Not revoked: the same account and client, so Google's revoke could end the new grant.
    assert world.oauth.revoked == []
    assert world.events()[-1] == (inbox_id, "auth_failed", "active", "reconsented", None, "operator")
    later = "2026-11-19T10:00:00.000000Z"
    assert world.inboxes()[0][4:6] == (later, later)  # status_changed_at, connected_at renewed


# Refusals before the flow -------------------------------------------------------------------


def test_the_gate_off_refuses_with_no_listener_and_no_browser(world: World) -> None:
    world.save({"gmail_internal_app_confirmed": "false"})
    refused_before_the_flow(world, ConnectFailure.GATE_OFF)


def test_the_gate_unset_refuses(db_engine: Engine) -> None:
    world = world_on(db_engine, "prod")
    world.save({"sender_name": SENDER})
    refused_before_the_flow(world, ConnectFailure.GATE_OFF)


@pytest.mark.parametrize(
    ("address", "failure"),
    [
        ("furqaan@nextronai.com", ConnectFailure.BRAND_DOMAIN),
        ("furqaan@mail.nextronai.com", ConnectFailure.BRAND_DOMAIN),
        ("sales@nextron-mail.com", ConnectFailure.ROLE_ADDRESS),
        ("info+cold@nextron-mail.com", ConnectFailure.ROLE_ADDRESS),
        ("not an address", ConnectFailure.INVALID_ADDRESS),
        ("o'brien@nextron-mail.com", ConnectFailure.INVALID_ADDRESS),  # no keyring name
        ("other@nextron-mail.com", ConnectFailure.NOT_ALLOWLISTED),
    ],
)
def test_an_address_that_cant_be_an_inbox_is_refused_before_the_flow(
    world: World, address: str, failure: ConnectFailure
) -> None:
    world.save({"sink_allowlist": f"{ADDRESS}\nsales@nextron-mail.com\no'brien@nextron-mail.com"})
    refused_before_the_flow(world, failure, address=address)


def test_a_bad_label_and_an_unset_sender_name_are_refused_before_the_flow(
    world: World,
) -> None:
    refused_before_the_flow(world, ConnectFailure.INVALID_LABEL, label=" ")
    world.sql("DELETE FROM settings WHERE key = 'sender_name'")
    refused_before_the_flow(world, ConnectFailure.SENDER_NAME_UNSET)


def test_no_imported_client_is_refused_before_the_flow(world: World) -> None:
    world.secrets.delete(CLIENT_SECRET_NAME)
    refused_before_the_flow(world, ConnectFailure.CLIENT_NOT_IMPORTED)


def test_an_active_or_removed_inbox_is_refused_before_the_flow(world: World) -> None:
    inbox_id = world.register()
    world.sql("UPDATE sending_inboxes SET status = 'active'")
    refused_before_the_flow(world, ConnectFailure.ALREADY_CONNECTED)
    with pytest.raises(ConnectRefused) as caught:
        world.connections.start_reconnect(inbox_id)
    assert caught.value.failure is ConnectFailure.ALREADY_CONNECTED
    world.sql("UPDATE sending_inboxes SET status = 'removed'")
    refused_before_the_flow(world, ConnectFailure.INBOX_REMOVED)
    with pytest.raises(ConnectRefused) as caught:
        world.connections.start_reconnect(inbox_id)
    assert caught.value.failure is ConnectFailure.UNKNOWN_INBOX  # removed: not listed
    with pytest.raises(ConnectRefused):
        world.connections.start_reconnect(new_id())
    assert world.listeners == []


# Refusals after consent ---------------------------------------------------------------------


def test_a_different_account_is_revoked_and_nothing_is_stored(world: World) -> None:
    world.gmail.profiles[ACCESS] = "other@nextron-mail.com"
    refused_after_consent(world, world.connect(), ConnectFailure.DIFFERENT_ACCOUNT)


def test_the_profile_address_is_compared_ignoring_ascii_case(world: World) -> None:
    world.gmail.profiles[ACCESS] = "Furqaan@Nextron-Mail.COM"
    outcome = world.connect()
    assert outcome is not None and outcome.failure is None


def test_an_address_taken_off_the_allowlist_during_consent_is_revoked(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Main")
    world.save({"sink_allowlist": "someone@else.example"})
    world.answer()
    world.run()
    refused_after_consent(
        world, world.connections.status().outcome, ConnectFailure.NOT_ALLOWLISTED
    )


def test_prod_needs_no_allowlist(db_engine: Engine) -> None:
    world = world_on(db_engine, "prod")
    world.save({"sender_name": SENDER, "gmail_internal_app_confirmed": "true"})
    outcome = world.connect()
    assert outcome is not None and outcome.failure is None


@pytest.mark.parametrize(
    ("answer", "failure"),
    [
        (tokens(scope="https://www.googleapis.com/auth/gmail.readonly"), "scope_not_granted"),
        (tokens(refresh=None), "no_refresh_token"),
        (tokens(refresh="1//" + "x" * 1278), "token_too_long"),
    ],
)
def test_a_grant_without_gmail_modify_or_a_usable_refresh_token_is_revoked(
    world: World, answer: TokenSet, failure: str
) -> None:
    world.oauth.exchanges[:] = [answer]
    outcome = world.connect()
    assert outcome is not None and outcome.failure == failure
    assert world.oauth.revoked == [answer.refresh_token or answer.access_token]
    assert world.stored_token() is None and world.inboxes() == []


def test_a_failed_profile_read_is_revoked(world: World) -> None:
    world.gmail.errors[ACCESS] = AdapterError(True, GMAIL_UNAVAILABLE, "down")
    outcome = world.connect()
    assert outcome is not None and outcome.code == GMAIL_UNAVAILABLE
    refused_after_consent(world, outcome, ConnectFailure.PROFILE_FAILED)


def test_an_unexpected_crash_after_the_exchange_still_revokes_and_stores_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def crash(access_token: str) -> str:
        raise RuntimeError(f"unexpected {access_token}")

    monkeypatch.setattr(world.gmail, "get_profile", crash)
    caplog.set_level(logging.DEBUG)

    outcome = world.connect()

    refused_after_consent(world, outcome, ConnectFailure.CRASHED)
    assert "inbox_connect_crashed" in [r.getMessage() for r in caplog.records]
    for record in caplog.records:
        assert ACCESS not in record.getMessage() + repr(vars(record))


def test_a_failed_revoke_is_reported_as_failed(world: World) -> None:
    world.gmail.profiles[ACCESS] = "other@nextron-mail.com"
    world.oauth.revoke_error = AdapterError(True, GOOGLE_UNAVAILABLE, "down")

    outcome = world.connect()

    assert outcome is not None and outcome.failure is ConnectFailure.DIFFERENT_ACCOUNT
    assert outcome.revocation is Revocation.FAILED
    assert world.oauth.revoked == [REFRESH]  # it was tried
    assert world.stored_token() is None and world.inboxes() == []


def test_no_repr_shows_the_address_or_the_label(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Qzx-label")
    waiting = world.connections.status()
    assert ADDRESS not in repr(waiting)
    with world.connections._state_lock:  # noqa: SLF001 - the private flow's repr
        flow = world.connections._flow  # noqa: SLF001
    assert flow is not None
    assert ADDRESS not in repr(flow) and "Qzx-label" not in repr(flow)
    world.answer()
    world.run()
    outcome = world.connections.status().outcome
    assert outcome is not None and outcome.failure is None
    assert ADDRESS not in repr(outcome) and ADDRESS not in repr(world.connections.status())
    plan = connect_module._Plan(ADDRESS, "Qzx-label", CLIENT)  # noqa: SLF001
    assert ADDRESS not in repr(plan) and "Qzx-label" not in repr(plan)


def test_a_state_mismatch_stores_nothing_and_exchanges_nothing(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Main")
    world.answer(state="forged-state")
    world.run()

    outcome = world.connections.status().outcome
    assert outcome == ConnectOutcome(ADDRESS, ConnectFailure.STATE_MISMATCH)
    assert world.oauth.calls == [] and world.stored_token() is None and world.inboxes() == []


def test_no_answer_in_five_minutes_is_a_timeout(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Main")
    world.run()  # the listener's answer stays None: the wait timed out

    assert world.connections.status().outcome == ConnectOutcome(ADDRESS, ConnectFailure.TIMED_OUT)
    assert world.oauth.calls == [] and world.inboxes() == []


def test_a_declined_consent_and_a_failed_exchange(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Main")
    world.listeners[-1].answer = {
        "state": (parse_qs(urlsplit(world.urls[-1]).query)["state"][0],),
        "error": ("access_denied",),
    }
    world.run()
    assert world.connections.status().outcome == ConnectOutcome(ADDRESS, ConnectFailure.DECLINED)

    world.oauth.exchanges[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "bad code")]
    outcome = world.connect()
    assert outcome == ConnectOutcome(
        ADDRESS, ConnectFailure.EXCHANGE_FAILED, code=GOOGLE_INVALID_GRANT
    )
    assert world.inboxes() == []


# Store failures -----------------------------------------------------------------------------


def test_a_keyring_write_failure_revokes_and_stores_nothing(world: World) -> None:
    world.secrets.failing.add("write")
    outcome = world.connect()
    assert outcome is not None and outcome.code == "keyring_failed"
    refused_after_consent(world, outcome, ConnectFailure.STORE_FAILED)


def test_a_refused_database_transaction_deletes_the_entry_and_revokes(world: World) -> None:
    world.connections.start_connect(ADDRESS, "Main")
    world.sql("DELETE FROM settings WHERE key = 'sender_name'")  # cleared while Google answers
    world.answer()
    world.run()

    refused_after_consent(
        world, world.connections.status().outcome, ConnectFailure.SENDER_NAME_UNSET
    )


def test_a_failing_database_transaction_is_store_failed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(world.sending, "connect_inbox", broken)
    outcome = world.connect()
    assert outcome is not None and outcome.code == "runtimeerror"
    refused_after_consent(world, outcome, ConnectFailure.STORE_FAILED)


# A listed domain ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dnsbl", "spam", "cause"),
    [
        (ZoneOutcome.LISTED, "ok", PauseCause.BLOCKLIST),
        (ZoneOutcome.CLEAN, "pause", PauseCause.SPAM_RATE),
        (ZoneOutcome.LISTED, "pause", PauseCause.BLOCKLIST),
    ],
)
def test_a_domain_whose_newest_check_pauses_pauses_the_new_inbox_at_once(
    world: World, dnsbl: ZoneOutcome, spam: str, cause: PauseCause
) -> None:
    domain_id, check_id = new_id(), new_id()
    world.sql(
        "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, ?, ?)",
        (domain_id, DOMAIN, STAMP),
    )
    world.sql(
        "INSERT INTO domain_checks (id, sending_domain_id, checked_at, spf_ok, dkim_ok, "
        "dmarc_ok, aligned_ok, passed, details, dnsbl_result, spam_rate, spam_rate_status) "
        "VALUES (?, ?, ?, 1, 1, 1, 1, 1, '{}', ?, ?, ?)",
        (
            check_id,
            domain_id,
            STAMP,
            encode_dnsbl({SPAMHAUS_DBL: dnsbl}),
            4000 if spam == "pause" else 0,
            spam,
        ),
    )

    outcome = world.connect()

    assert outcome is not None and outcome.failure is None and outcome.paused is cause
    [(inbox_id, _, status, *_)] = world.inboxes()
    assert status == "paused"
    assert world.events()[-2:] == [
        (inbox_id, "connecting", "active", "connected", None, "operator"),
        (inbox_id, "active", "paused", str(cause), check_id, "system:sending"),
    ]
    [view] = world.read(sending_queries.inboxes)
    assert view.pause_cause is cause
    issues = world.read(lambda: sending_queries.inbox_health_issues())
    assert [(issue.kind, issue.subject) for issue in issues] == [("inbox_paused", inbox_id)]
    assert str(cause) in issues[0].text and ADDRESS not in issues[0].text


def test_a_fourth_inbox_on_a_domain_connects_and_the_panel_warns(world: World) -> None:
    others = [f"{name}@nextron-mail.com" for name in ("ann", "bob", "cat")]
    world.save({"sink_allowlist": "\n".join([ADDRESS, *others])})
    for other in others:
        world.register(other)

    outcome = world.connect()

    assert outcome is not None and outcome.failure is None
    assert len(world.inboxes()) == 4
    context = inboxes_page.panel_context(world.read(sending_queries.inboxes))
    assert context["warnings"] == [
        f"More than 3 inboxes on {DOMAIN} raises deliverability risk"
    ]


# invalid_grant and the access token ---------------------------------------------------------


def test_the_access_token_is_cached_then_refreshed_with_the_stored_grant(world: World) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()

    assert world.connections.access_token(inbox_id) == ACCESS  # cached by the connect
    assert world.oauth.calls == [("exchange", CODE)]
    world.clock.advance(timedelta(seconds=3599 - 59))  # within the 60 s margin
    world.oauth.refreshes.append(tokens(access="ya29.refreshed"))
    assert world.connections.access_token(inbox_id) == "ya29.refreshed"
    assert world.oauth.calls[-1] == ("refresh", REFRESH)
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes.append(tokens(access="ya29.again"))
    assert world.connections.access_token(inbox_id) == "ya29.again"


def test_invalid_grant_moves_the_inbox_to_auth_failed_with_one_event_and_one_alert(
    world: World,
) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "revoked")] * 2
    world.clock.advance(timedelta(hours=2))

    with pytest.raises(AdapterError) as caught:
        world.connections.access_token(inbox_id)

    assert caught.value.code == GMAIL_AUTH_FAILED and not caught.value.retryable
    assert world.inboxes()[0][2] == "auth_failed"
    assert world.inboxes()[0][4:6] == ("2026-11-19T11:00:00.000000Z", STAMP)  # connected_at kept
    [event] = [row for row in world.events() if row[2] == "auth_failed"]
    assert event[1:] == ("active", "auth_failed", "invalid_grant", None, "system:sending")
    [(kind, subject, text)] = world.alerts()
    assert kind == "inbox_auth_failed" and inbox_id in text and "@" not in text
    assert world.sql("SELECT kind FROM notifications") == [("inbox_auth_failed",)]
    issues = world.read(lambda: sending_queries.health_issues(world.clock.now()))
    assert ("inbox_auth_failed", inbox_id) in [(issue.kind, issue.subject) for issue in issues]

    with pytest.raises(AdapterError):  # still refused: no second event or alert
        world.connections.access_token(inbox_id)
    assert len([row for row in world.events() if row[2] == "auth_failed"]) == 1
    assert len(world.alerts()) == 1


def test_a_paused_inbox_also_moves_to_auth_failed(world: World) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    world.sql("UPDATE sending_inboxes SET status = 'paused'")
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "revoked")]

    with pytest.raises(AdapterError):
        world.connections.access_token(inbox_id)

    assert world.inboxes()[0][2] == "auth_failed"


def pause(world: World, inbox_id: str, cause: PauseCause, at: datetime) -> None:
    """Move an ``active`` inbox to ``paused`` with its ``pause_<cause>`` event, as Story 6.3's
    commands and 6.6's checks will."""

    @use_case
    def write() -> None:
        with current().transaction():
            assert inboxes_repo.set_status(
                inbox_id, from_status="active", to_status="paused", at=at
            )
            eventlog.append(
                entity="sending_inboxes",
                entity_id=inbox_id,
                from_status="active",
                to_status="paused",
                cause=str(cause),
                cause_ref=None,
                actor="operator",
                at=at,
            )

    with world.units.open():
        write()


def test_a_reconnect_restores_the_pause_the_inbox_had_before_its_grant_failed(
    world: World,
) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    pause(world, inbox_id, PauseCause.OPERATOR, T0 + timedelta(minutes=10))
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "revoked")]
    world.clock.advance(timedelta(minutes=20))
    with pytest.raises(AdapterError):
        world.connections.access_token(inbox_id)
    assert world.inboxes()[0][2] == "auth_failed"
    world.oauth.exchanges.append(tokens())
    world.clock.advance(timedelta(minutes=40))  # 10:00

    world.connections.start_reconnect(inbox_id)
    world.answer()
    world.run()

    outcome = world.connections.status().outcome
    assert outcome == ConnectOutcome(ADDRESS, None, inbox_id, PauseCause.OPERATOR)
    [(_, _, status, _, changed, connected)] = world.inboxes()
    later = "2026-11-19T10:00:00.000000Z"
    assert (status, changed, connected) == ("paused", later, later)
    assert world.events()[-2:] == [
        (inbox_id, "auth_failed", "active", "reconsented", None, "operator"),
        (inbox_id, "active", "paused", "operator", None, "system:sending"),
    ]
    [view] = world.read(sending_queries.inboxes)
    assert view.pause_cause is PauseCause.OPERATOR


def test_a_reconnect_of_an_inbox_active_before_its_grant_failed_stays_active(
    world: World,
) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    pause(world, inbox_id, PauseCause.OPERATOR, T0 + timedelta(minutes=10))
    world.sql("UPDATE sending_inboxes SET status = 'active'")  # resumed (Story 6.3)
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "revoked")]
    with pytest.raises(AdapterError):
        world.connections.access_token(inbox_id)
    world.oauth.exchanges.append(tokens())

    world.connections.start_reconnect(inbox_id)
    world.answer()
    world.run()

    outcome = world.connections.status().outcome
    assert outcome == ConnectOutcome(ADDRESS, None, inbox_id)
    assert world.inboxes()[0][2] == "active"


def test_the_newest_pause_event_gives_the_cause(world: World) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    # The newer pause is written first, so only its time can make it the newest.
    pause(world, inbox_id, PauseCause.BOUNCE_RATE, T0 + timedelta(hours=2))
    world.sql("UPDATE sending_inboxes SET status = 'active'")
    pause(world, inbox_id, PauseCause.OPERATOR, T0 + timedelta(hours=1))

    [view] = world.read(sending_queries.inboxes)
    assert view.status is InboxStatus.PAUSED
    assert view.pause_cause is PauseCause.BOUNCE_RATE


def test_a_failing_mark_auth_failed_still_raises_gmail_auth_failed(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "revoked")]

    def broken(inbox_id: str) -> bool:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(world.sending, "mark_auth_failed", broken)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(AdapterError) as caught:
        world.connections.access_token(inbox_id)

    assert caught.value.code == GMAIL_AUTH_FAILED and not caught.value.retryable
    [record] = [r for r in caplog.records if r.getMessage() == "inbox_mark_auth_failed_failed"]
    assert record.levelno == logging.ERROR and getattr(record, "inbox_id", None) == inbox_id
    assert world.inboxes()[0][2] == "active"  # nothing changed


def test_another_refresh_error_changes_no_status(world: World) -> None:
    world.connect()
    [(inbox_id, *_)] = world.inboxes()
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(True, GOOGLE_UNAVAILABLE, "down")]

    with pytest.raises(AdapterError) as caught:
        world.connections.access_token(inbox_id)

    assert caught.value.code == GOOGLE_UNAVAILABLE and caught.value.retryable
    assert world.inboxes()[0][2] == "active" and world.alerts() == []


def test_access_token_refuses_an_unknown_or_unconnected_inbox_and_a_transaction(
    world: World,
) -> None:
    inbox_id = world.register()
    with pytest.raises(AdapterError) as caught:
        world.connections.access_token(inbox_id)
    assert caught.value.code == GMAIL_NOT_CONNECTED
    with pytest.raises(AdapterError):
        world.connections.access_token(new_id())

    with world.units.open(read_only=True):
        sending_queries.inboxes()  # a read transaction is open now
        with pytest.raises(RuntimeError):
            world.connections.access_token(inbox_id)


# One flow at a time -------------------------------------------------------------------------


def test_a_new_connect_replaces_a_waiting_flow_which_then_stores_nothing(world: World) -> None:
    world.save({"sink_allowlist": f"{ADDRESS}\nsecond@nextron-mail.com"})
    world.connections.start_connect(ADDRESS, "Main")
    first_listener, first_url = world.listeners[-1], world.urls[-1]
    world.connections.start_connect("second@nextron-mail.com", "Second")

    assert first_listener.closed
    assert world.connections.status().waiting_for == "second@nextron-mail.com"
    state = parse_qs(urlsplit(first_url).query)["state"][0]
    first_listener.answer = {"state": (state,), "code": (CODE,)}
    world.jobs[0]()  # the replaced flow's job: Google answered it after all

    assert world.oauth.revoked == [REFRESH]  # its grant is let go
    assert world.stored_token() is None and world.inboxes() == []
    assert world.connections.status().waiting_for == "second@nextron-mail.com"


# Doctor -------------------------------------------------------------------------------------


def test_check_grant_reads_the_keyring_then_refreshes_and_reads_the_profile(
    world: World,
) -> None:
    assert world.connections.check_grant(ADDRESS, network=False) is GrantProblem.NOT_STORED
    world.connect()
    calls = list(world.oauth.calls)
    assert world.connections.check_grant(ADDRESS, network=False) is None
    assert world.oauth.calls == calls  # no network
    world.oauth.refreshes.append(tokens(access="ya29.doctor"))
    world.gmail.profiles["ya29.doctor"] = ADDRESS
    assert world.connections.check_grant(ADDRESS, network=True) is None
    world.oauth.refreshes.append(tokens(access="ya29.other"))
    world.gmail.profiles["ya29.other"] = "someone@else.example"
    assert world.connections.check_grant(ADDRESS, network=True) is GrantProblem.DIFFERENT_ACCOUNT
    world.oauth.refreshes.append(AdapterError(False, GOOGLE_INVALID_GRANT, "revoked"))
    assert world.connections.check_grant(ADDRESS, network=True) is GrantProblem.INVALID_GRANT
    world.oauth.refreshes.append(AdapterError(True, GOOGLE_UNAVAILABLE, "down"))
    assert world.connections.check_grant(ADDRESS, network=True) is GrantProblem.UNREACHABLE
    assert world.inboxes()[0][2] == "active"  # doctor changes no status
    world.secrets.failing.add("read")
    assert world.connections.check_grant(ADDRESS, network=False) is GrantProblem.KEYRING_FAILED


# Logs ---------------------------------------------------------------------------------------


def test_no_token_code_verifier_address_or_label_reaches_a_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.connect(label="Qzx-secret-label")
    [(inbox_id, *_)] = world.inboxes()
    world.connections.forget_token(inbox_id)
    world.oauth.refreshes[:] = [AdapterError(False, GOOGLE_INVALID_GRANT, "revoked")]
    with pytest.raises(AdapterError):
        world.connections.access_token(inbox_id)
    world.gmail.profiles[ACCESS] = "other@nextron-mail.com"
    world.sql("UPDATE sending_inboxes SET status = 'auth_failed'")
    world.oauth.exchanges.append(tokens())
    world.connections.start_reconnect(inbox_id)
    world.answer()
    world.run()

    planted = [ACCESS, REFRESH, CODE, ADDRESS, "Qzx-secret-label", *world.oauth.verifiers]
    records = [r for r in caplog.records if r.name in LOGGERS]
    assert records  # the paths were logged
    for record in records:
        text = record.getMessage() + repr(vars(record))
        for secret in planted:
            assert secret not in text, record.getMessage()
