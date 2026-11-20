"""The reputation guardrails of ``DomainChecks`` (Story 6.6; FR-38, FR-58, AD-9) on a temporary
database at head, with ``FakeDns``, ``FakePostmaster``, ``FrozenClock`` and a real ``Notify``:
every I/O matrix row (listed, refused, the spam-rate boundaries, no data, a missing grant, a
resume while listed, an outage), the pause events, the alert dedup per kind, domain and UTC
day, ``dispatch_state`` closed after a pause, the stored columns and their CHECK, the panel's
view and the health issues."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.queries import sending as sending_queries
from nextron.app.sending.api import Sending
from nextron.app.sending.checks import DomainChecks
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory, in_transaction
from nextron.domain.reputation import SpamStatus, ZoneOutcome
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    GOOGLE_CLIENT_NOT_IMPORTED,
    GOOGLE_NEEDS_RECONSENT,
    GOOGLE_NOT_GRANTED,
)
from nextron.ports.postmaster import POSTMASTER_REJECTED, POSTMASTER_UNAVAILABLE
from nextron.web.inboxes_page import auth_row
from tests.fakes.clock import FrozenClock
from tests.fakes.dns import FakeDns
from tests.fakes.postmaster import FakePostmaster

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP = "2026-11-18T09:00:00.000000Z"
DOMAIN = "nextron-ai.com"
DOMAIN_ID = "01JD3QZ8X5V9K2M7N4P6R8T0D1"
DBL_NAME = f"{DOMAIN}.dbl.spamhaus.org"
ACTOR = "system:sending"
SUBJECT = f"{DOMAIN_ID.lower()}:2026-11-18"
SPF_OK = "v=spf1 include:_spf.google.com ~all"
DKIM_OK = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA"
DMARC_OK = "v=DMARC1; p=none"


def no_jobs() -> Any:
    raise AssertionError("no job runs in these tests")


class OutsideTransaction(FakeDns):
    """``FakeDns`` that fails the test if an A query runs inside a transaction (AD-3)."""

    def a(self, name: str) -> tuple[str, ...]:
        assert not in_transaction(), "a DNSBL query ran inside a transaction"
        return super().a(name)


class OutsideTransactionPostmaster(FakePostmaster):
    def spam_rate(self, domain: str, day: Any) -> Any:
        assert not in_transaction(), "a Postmaster read ran inside a transaction"
        return super().spam_rate(domain, day)


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    dns: OutsideTransaction = field(default_factory=OutsideTransaction)
    postmaster: OutsideTransactionPostmaster = field(default_factory=OutsideTransactionPostmaster)
    queue: JobQueue = field(init=False)
    notify: Notify = field(init=False)
    checks: DomainChecks = field(init=False)
    inboxes: dict[str, str] = field(default_factory=dict)

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
            self.clock, self.dns, self.queue, postmaster=self.postmaster, notify=self.notify
        )
        self.dns.txt_answers.update(
            {DOMAIN: (SPF_OK,), f"google._domainkey.{DOMAIN}": (DKIM_OK,)}
        )
        self.dns.txt_answers[f"_dmarc.{DOMAIN}"] = (DMARC_OK,)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def domain(self, *inboxes: tuple[str, str]) -> None:
        self.sql(
            "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, ?, ?)",
            (DOMAIN_ID, DOMAIN, STAMP),
        )
        for address, status in inboxes:
            inbox_id = new_id()
            self.inboxes[address] = inbox_id
            self.sql(
                "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, "
                "status, created_at, status_changed_at) "
                "VALUES (?, ?, 'Main', ?, 'Furqaan Ahmed', ?, ?, ?)",
                (inbox_id, address, DOMAIN_ID, status, STAMP, STAMP),
            )

    def check(self) -> Any:
        with self.units.open():
            return self.checks.check_domain(DOMAIN_ID)

    def statuses(self) -> dict[str, str]:
        return dict(self.sql("SELECT address, status FROM sending_inboxes ORDER BY address"))

    def stored(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT dnsbl_result, spam_rate, spam_rate_status FROM domain_checks ORDER BY rowid"
        )

    def pause_events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor FROM events "
            "WHERE entity = 'sending_inboxes' ORDER BY rowid"
        )

    def alerts(self) -> list[tuple[Any, ...]]:
        telegram = self.sql("SELECT kind, subject, text FROM alerts ORDER BY rowid")
        dashboard = self.sql("SELECT kind, subject, text FROM notifications ORDER BY rowid")
        assert telegram == dashboard  # every guardrail alert goes to both channels
        return telegram

    def read(self, fn: Any) -> Any:
        with self.units.open(read_only=True):
            return fn()

    def view(self) -> sending_queries.DomainAuthView:
        found = self.read(lambda: sending_queries.domain_auth(DOMAIN_ID, self.clock.now()))
        assert found is not None
        return found

    def issues(self, grant_missing: bool | None = None) -> list[tuple[str, str, str]]:
        missing = None if grant_missing is None else (lambda: grant_missing)
        found = self.read(lambda: sending_queries.health_issues(self.clock.now(), missing))
        return [(issue.kind, issue.subject, issue.text) for issue in found]


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    return World(units, db_engine, clock)


@pytest.fixture
def three(world: World) -> World:
    """Two ``active`` inboxes and one ``connecting``."""
    world.domain(
        (f"a@{DOMAIN}", "active"), (f"b@{DOMAIN}", "active"), (f"c@{DOMAIN}", "connecting")
    )
    return world


# Listed -----------------------------------------------------------------------------------------


def test_a_listing_pauses_the_active_inboxes_with_events_and_one_alert(three: World) -> None:
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)

    result = three.check()

    assert result.dnsbl == {"dbl.spamhaus.org": ZoneOutcome.LISTED}
    assert three.stored() == [('{"dbl.spamhaus.org":"listed"}', None, "no_data")]
    assert three.statuses() == {
        f"a@{DOMAIN}": "paused",
        f"b@{DOMAIN}": "paused",
        f"c@{DOMAIN}": "connecting",  # never pause connecting inboxes
    }
    active = sorted(three.inboxes[address] for address in (f"a@{DOMAIN}", f"b@{DOMAIN}"))
    assert sorted(result.paused) == active
    assert sorted(three.pause_events()) == [
        ("sending_inboxes", inbox, "active", "paused", "blocklist", result.check_id, ACTOR)
        for inbox in active
    ]
    assert three.alerts() == [
        (
            "domain_blocklisted",
            SUBJECT,
            f"Sending Domain {DOMAIN_ID} is blocklisted; 2 inboxes paused",
        )
    ]
    issue = (
        "domain_blocklisted",
        DOMAIN_ID,
        f"{DOMAIN}: blocklisted on dbl.spamhaus.org; each check pauses its active inboxes",
    )
    assert issue in three.issues()
    row = auth_row(three.view())
    assert (row.dnsbl, row.spam_rate, row.dnsbl_paused) == (
        "Listed (dbl.spamhaus.org)", "No data", True
    )


def test_a_paused_inbox_fails_dispatch_closed(three: World) -> None:
    with three.units.open():
        Setup(three.clock).save({"sender_timezone": "America/New_York"})
    sending = Sending(three.clock, env="test")
    inbox = three.inboxes[f"a@{DOMAIN}"]
    before = three.read(lambda: sending.dispatch_state(inbox, T0))
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)

    three.check()

    after = three.read(lambda: sending.dispatch_state(inbox, T0))
    assert before.recovery_mode is False  # active, so not the closed default
    assert (after.recovery_mode, after.next_send_at, after.cold_cap) == (True, None, 0)
    [view] = [v for v in three.read(sending_queries.inboxes) if v.id == inbox]
    assert view.status == "paused"


def test_a_resumed_inbox_still_listed_is_paused_again_without_a_second_alert(three: World) -> None:
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)
    first = three.check()
    inbox = three.inboxes[f"a@{DOMAIN}"]
    three.sql(f"UPDATE sending_inboxes SET status = 'active' WHERE id = '{inbox}'")  # a resume
    three.clock.set(T0 + timedelta(hours=2))

    second = three.check()

    assert second.paused == (inbox,)
    assert three.statuses()[f"a@{DOMAIN}"] == "paused"
    assert len(three.pause_events()) == 3
    assert three.pause_events()[-1][5] == second.check_id != first.check_id
    assert [alert[0] for alert in three.alerts()] == ["domain_blocklisted"]  # deduplicated


def test_a_listing_that_persists_alerts_again_the_next_utc_day(three: World) -> None:
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)
    three.check()
    three.clock.set(T0 + timedelta(days=1))

    later = three.check()

    assert later.paused == ()  # nothing active is left to pause
    assert [alert[1] for alert in three.alerts()] == [SUBJECT, f"{DOMAIN_ID.lower()}:2026-11-19"]


def test_every_configured_zone_is_queried_and_the_worst_wins(three: World) -> None:
    with three.units.open():
        Setup(three.clock).save({"dnsbl_zones": "multi.uribl.com\ndbl.spamhaus.org"})
    three.dns.a_answers[f"{DOMAIN}.multi.uribl.com"] = ("127.0.0.4",)

    result = three.check()

    assert ("a", DBL_NAME) in three.dns.queries
    assert ("a", f"{DOMAIN}.multi.uribl.com") in three.dns.queries
    assert result.dnsbl == {
        "dbl.spamhaus.org": ZoneOutcome.CLEAN,
        "multi.uribl.com": ZoneOutcome.LISTED,
    }
    assert three.stored()[0][0] == '{"dbl.spamhaus.org":"clean","multi.uribl.com":"listed"}'
    assert set(three.statuses().values()) == {"paused", "connecting"}


# Refused and unrecognised -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "outcome", "text"),
    [
        ("127.255.255.254", ZoneOutcome.QUERY_REFUSED, "Query refused"),
        ("127.0.0.1", ZoneOutcome.UNRECOGNISED, "Unrecognised answer"),
    ],
)
def test_a_refused_or_unrecognised_answer_only_warns(
    three: World, answer: str, outcome: ZoneOutcome, text: str
) -> None:
    three.dns.a_answers[DBL_NAME] = (answer,)

    result = three.check()

    assert result.dnsbl == {"dbl.spamhaus.org": outcome}
    assert result.paused == ()
    assert set(three.statuses().values()) == {"active", "connecting"}
    assert three.pause_events() == []
    assert three.alerts() == []
    assert [issue[0] for issue in three.issues()] == ["dnsbl_query_refused"]
    assert auth_row(three.view()).dnsbl == text


# Spam rate --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ppm", "status", "alert", "paused"),
    [
        (900, SpamStatus.OK, None, False),
        (1000, SpamStatus.ALERT, "spam_rate_high", False),
        (2900, SpamStatus.ALERT, "spam_rate_high", False),
        (3000, SpamStatus.PAUSE, "spam_rate_pause", True),
    ],
)
def test_each_spam_rate_boundary(
    three: World, ppm: int, status: SpamStatus, alert: str | None, paused: bool
) -> None:
    three.postmaster.rates[DOMAIN] = ppm

    result = three.check()

    assert (result.spam_rate, result.spam_status) == (ppm, status)
    assert three.stored() == [('{"dbl.spamhaus.org":"clean"}', ppm, str(status))]
    assert three.postmaster.calls == [(DOMAIN, (T0 - timedelta(days=1)).date())]
    assert [found[0] for found in three.alerts()] == ([] if alert is None else [alert])
    assert (three.statuses()[f"a@{DOMAIN}"] == "paused") is paused
    issue_kinds = [issue[0] for issue in three.issues()]
    paused_issues = ["inbox_paused"] * 2 if paused else []  # Story 6.2: each paused inbox
    assert issue_kinds == paused_issues + ([] if alert is None else [alert])
    if paused:
        assert {event[4] for event in three.pause_events()} == {"spam_rate"}
        assert three.alerts()[0][2] == (
            f"Sending Domain {DOMAIN_ID} spam rate 0.30%; 2 inboxes paused"
        )
    else:
        assert three.pause_events() == []


def test_a_blocklist_and_spam_pause_pause_each_inbox_once(three: World) -> None:
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)
    three.postmaster.rates[DOMAIN] = 5000

    result = three.check()

    assert len(result.paused) == 2
    assert [event[4] for event in three.pause_events()] == ["blocklist", "blocklist"]
    assert [alert[0] for alert in three.alerts()] == ["domain_blocklisted", "spam_rate_pause"]
    assert three.alerts()[1][2].endswith("0 inboxes paused")


def test_no_data_is_null_never_zero_and_does_nothing(three: World) -> None:
    result = three.check()  # FakePostmaster: no data

    assert (result.spam_rate, result.spam_status) == (None, SpamStatus.NO_DATA)
    assert three.stored() == [('{"dbl.spamhaus.org":"clean"}', None, "no_data")]
    assert three.alerts() == []
    assert three.issues() == []
    assert auth_row(three.view()).spam_rate == "No data"


@pytest.mark.parametrize(
    "code", [GOOGLE_NOT_GRANTED, GOOGLE_NEEDS_RECONSENT, GOOGLE_CLIENT_NOT_IMPORTED]
)
def test_a_missing_grant_records_no_data(three: World, code: str) -> None:
    three.postmaster.errors[DOMAIN] = AdapterError(False, code, "no grant")

    result = three.check()

    assert result is not None
    assert three.stored() == [('{"dbl.spamhaus.org":"clean"}', None, "no_data")]
    assert three.issues(grant_missing=True) == [
        ("postmaster_grant_missing", "postmaster", "Postmaster grant missing")
    ]
    assert three.issues(grant_missing=False) == []


def test_the_grant_issue_needs_a_listed_domain(world: World) -> None:
    asked: list[bool] = []

    def missing() -> bool:
        asked.append(True)
        return True

    assert world.read(lambda: sending_queries.health_issues(world.clock.now(), missing)) == ()
    assert asked == []  # not even asked without a listed domain


# Outages ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where",
    [
        ("postmaster", AdapterError(True, POSTMASTER_UNAVAILABLE, "503")),
        ("postmaster", AdapterError(False, POSTMASTER_REJECTED, "400")),
        ("dns", AdapterError(True, "dns_timeout", "timed out")),
    ],
)
def test_an_outage_records_nothing_and_pauses_nothing(
    three: World, where: tuple[str, AdapterError]
) -> None:
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)
    kind, error = where
    if kind == "postmaster":
        three.postmaster.errors[DOMAIN] = error
    else:
        three.dns.errors[DBL_NAME] = error

    with pytest.raises(AdapterError) as raised:
        three.check()

    assert raised.value is error
    assert three.stored() == []
    assert set(three.statuses().values()) == {"active", "connecting"}
    assert three.alerts() == []


# Storage ----------------------------------------------------------------------------------------


def test_the_spam_rate_status_check_refuses_an_unknown_status(three: World) -> None:
    three.check()
    with pytest.raises(IntegrityError):
        three.sql("UPDATE domain_checks SET spam_rate_status = 'zero'")


def test_a_check_recorded_before_the_story_reads_as_not_checked(three: World) -> None:
    three.check()
    three.sql(
        "UPDATE domain_checks SET dnsbl_result = NULL, spam_rate = NULL, spam_rate_status = NULL"
    )

    view = three.view()

    assert view.latest is not None
    assert (view.latest.dnsbl, view.latest.spam_status, view.latest.dnsbl_outcome) == (
        None,
        None,
        None,
    )
    row = auth_row(view)
    assert (row.dnsbl, row.spam_rate) == ("Not checked yet", "Not checked yet")
    assert three.issues() == []


def test_a_spam_rate_shows_as_a_percentage(three: World) -> None:
    three.postmaster.rates[DOMAIN] = 1234

    three.check()

    assert auth_row(three.view()).spam_rate == "0.12%"


# Review follow-ups ------------------------------------------------------------------------------


def test_a_listing_leaves_paused_auth_failed_and_removed_inboxes_untouched(
    world: World,
) -> None:
    world.domain(
        (f"a@{DOMAIN}", "active"),
        (f"p@{DOMAIN}", "paused"),
        (f"f@{DOMAIN}", "auth_failed"),
        (f"r@{DOMAIN}", "removed"),
    )
    world.dns.a_answers[DBL_NAME] = ("127.0.1.2",)

    result = world.check()

    assert world.statuses() == {
        f"a@{DOMAIN}": "paused",
        f"f@{DOMAIN}": "auth_failed",
        f"p@{DOMAIN}": "paused",
        f"r@{DOMAIN}": "removed",
    }
    active = world.inboxes[f"a@{DOMAIN}"]
    assert result.paused == (active,)
    assert [event[1] for event in world.pause_events()] == [active]  # no event for the others


def test_a_spam_pause_re_pauses_an_inbox_resumed_the_same_utc_day_without_a_second_alert(
    three: World,
) -> None:
    three.postmaster.rates[DOMAIN] = 3000
    first = three.check()
    inbox = three.inboxes[f"a@{DOMAIN}"]
    three.sql(f"UPDATE sending_inboxes SET status = 'active' WHERE id = '{inbox}'")  # a resume
    three.clock.set(T0 + timedelta(hours=2))

    second = three.check()

    assert second.paused == (inbox,)
    assert three.statuses()[f"a@{DOMAIN}"] == "paused"
    assert len(three.pause_events()) == 3
    assert three.pause_events()[-1][4:6] == ("spam_rate", second.check_id)
    assert second.check_id != first.check_id
    assert [alert[0] for alert in three.alerts()] == ["spam_rate_pause"]  # deduplicated


def test_an_unparsable_zone_setting_falls_back_to_spamhaus_dbl(three: World) -> None:
    three.sql(  # bypasses the setting's validation, as an older or hand-edited row might
        "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
        (new_id(), "dnsbl_zones", "evil.example.com", STAMP),
    )
    three.dns.a_answers[DBL_NAME] = ("127.0.1.2",)

    result = three.check()

    assert [query for query in three.dns.queries if query[0] == "a"] == [("a", DBL_NAME)]
    assert result.dnsbl == {"dbl.spamhaus.org": ZoneOutcome.LISTED}
    assert three.statuses() == {
        f"a@{DOMAIN}": "paused",
        f"b@{DOMAIN}": "paused",
        f"c@{DOMAIN}": "connecting",
    }


class StubGoogle:
    """A ``GoogleAccess`` stand-in for ``main.postmaster_grant_missing``: records each grant
    asked about and raises ``error`` when one is set."""

    def __init__(self, error: AdapterError | None = None) -> None:
        self.error = error
        self.asked: list[str] = []

    def check_stored_grant(self, grant: str) -> None:
        self.asked.append(grant)
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize(
    ("error", "missing"),
    [(AdapterError(False, GOOGLE_NOT_GRANTED, "no grant"), True), (None, False)],
)
def test_postmaster_grant_missing_asks_the_stored_postmaster_grant(
    error: AdapterError | None, missing: bool
) -> None:
    google = StubGoogle(error)

    assert main.postmaster_grant_missing(google) is missing  # type: ignore[arg-type]
    assert google.asked == ["postmaster"]
