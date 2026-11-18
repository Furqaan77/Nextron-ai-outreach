"""``DomainChecks`` and the ``queries.sending`` authentication reads (Story 6.5; FR-64, NFR-4,
NFR-20) on a temporary database at head, with ``FakeDns`` and ``FrozenClock``: every I/O matrix
row, a DNS timeout re-queued through ``JobQueue.fail`` with nothing recorded, ``queue_daily``,
``recheck``, ``schedule``, ``health_issues``, ``domain_auth``, the CHECK and the foreign key on
raw writes, the ``check_domains`` handler's dispatch (``InvalidJob`` included), and
``main.build_app`` building ``DomainChecks`` on its ``DnspythonDns``."""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import sending as sending_queries
from nextron.app.sending.checks import DomainChecks
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory, in_transaction
from nextron.domain.domain_auth import (
    RECHECK_HINT,
    AuthItem,
    AuthReason,
    DomainCheckSchedule,
)
from nextron.domain.health import HealthIssue
from nextron.domain.job import InvalidJob, Job, JobStatus, Lane
from nextron.ports.errors import AdapterError
from tests.fakes.clock import FrozenClock
from tests.fakes.dns import FakeDns
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP = "2026-11-18T09:00:00.000000Z"
LOGGER = "nextron.app.sending.checks"
DOMAIN = "nextron-ai.com"
OTHER = "nextron-mail.com"
GONE = "nextron-old.com"
DOMAIN_ID = "01JD3QZ8X5V9K2M7N4P6R8T0D1"
OTHER_ID = "01JD3QZ8X5V9K2M7N4P6R8T0D2"
GONE_ID = "01JD3QZ8X5V9K2M7N4P6R8T0D3"
UNKNOWN_ID = "01JD3QZ8X5V9K2M7N4P6R8T0D9"
SPF_OK = "v=spf1 include:_spf.google.com ~all"
DKIM_OK = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA"
DMARC_OK = "v=DMARC1; p=none"
CHECK_COLUMNS = (
    "sending_domain_id, checked_at, spf_ok, dkim_ok, dmarc_ok, aligned_ok, passed, details"
)


def passing_records(domain: str) -> dict[str, list[str]]:
    return {
        domain: [SPF_OK, "google-site-verification=abc"],
        f"google._domainkey.{domain}": [DKIM_OK],
        f"_dmarc.{domain}": [DMARC_OK],
    }


class OutsideTransactionDns(FakeDns):
    """``FakeDns`` that fails the test if a query runs inside a transaction (AD-3)."""

    def txt(self, name: str) -> tuple[str, ...]:
        assert not in_transaction(), "a DNS query ran inside a transaction"
        return super().txt(name)


def no_jobs() -> Any:
    raise AssertionError("only check_domains jobs run in these tests")


@dataclass
class World:
    """``DomainChecks`` on real units of work, with a queue on the real job types."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    dns: OutsideTransactionDns = field(default_factory=OutsideTransactionDns)
    queue: JobQueue = field(init=False)
    checks: DomainChecks = field(init=False)

    def __post_init__(self) -> None:
        job_types = main.build_job_types(
            alerts=no_jobs,
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
            domain_checks=lambda: self.checks,
        )
        self.queue = JobQueue(self.clock, job_types)
        self.checks = DomainChecks(self.clock, self.dns, self.queue)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def domain(self, domain_id: str, domain: str, *inboxes: tuple[str, str]) -> None:
        """A Sending Domain and its inboxes, each ``(address, status)``."""
        self.sql(
            "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, ?, ?)",
            (domain_id, domain, STAMP),
        )
        for address, status in inboxes:
            self.sql(
                "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, "
                "status, created_at) VALUES (?, ?, 'Main', ?, 'Furqaan Ahmed', ?, ?)",
                (new_id(), address, domain_id, status, STAMP),
            )

    def check(self, domain_id: str = DOMAIN_ID) -> Any:
        with self.units.open():
            return self.checks.check_domain(domain_id)

    def rows(self) -> list[tuple[Any, ...]]:
        return self.sql(f"SELECT {CHECK_COLUMNS} FROM domain_checks ORDER BY rowid")

    def jobs(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT type, lane, status, idempotency_key, payload, priority FROM jobs "
            "ORDER BY rowid"
        )

    def read[R](self, fn: Any) -> Any:
        with self.units.open(read_only=True):
            return fn()

    def auth(self, domain_id: str = DOMAIN_ID) -> sending_queries.DomainAuthView | None:
        return self.read(lambda: sending_queries.domain_auth(domain_id, self.clock.now()))

    def issues(self) -> tuple[HealthIssue, ...]:
        return self.read(lambda: sending_queries.health_issues(self.clock.now()))


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    return World(units, db_engine, clock)


@pytest.fixture
def listed(world: World) -> World:
    """``nextron-ai.com`` with one connecting inbox."""
    world.domain(DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "connecting"))
    return world


def details(**reasons: str) -> str:
    every = {"alignment": "ok", "dkim": "ok", "dmarc": "ok", "spf": "ok", **reasons}
    return json.dumps(every, sort_keys=True, separators=(",", ":"))


# Passing ----------------------------------------------------------------------------------------


def test_a_passing_domain_records_one_row_and_allows_cold_sends(
    listed: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    listed.dns.txt_answers.update(passing_records(DOMAIN))

    result = listed.check()

    assert result is not None
    assert (result.sending_domain_id, result.domain, result.checked_at) == (DOMAIN_ID, DOMAIN, T0)
    assert (result.passed, result.failing) == (True, ())
    assert listed.dns.queries == [
        ("txt", DOMAIN),
        ("txt", f"google._domainkey.{DOMAIN}"),
        ("txt", f"_dmarc.{DOMAIN}"),
    ]
    assert listed.rows() == [(DOMAIN_ID, STAMP, 1, 1, 1, 1, 1, details())]
    view = listed.auth()
    assert view is not None
    assert view.latest is not None
    assert (view.latest.id, view.latest.checked_at, view.latest.passed) == (
        result.check_id,
        T0,
        True,
    )
    assert view.latest.reasons == dict.fromkeys(AuthItem, AuthReason.OK)
    assert (view.verdict.blocked, view.verdict.text) == (False, "Cold sends allowed")
    assert listed.issues() == ()
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.getMessage() == "domain_check_recorded"
    fields = {name: getattr(record, name) for name in ("sending_domain_id", "passed", "failing")}
    assert fields == {"sending_domain_id": DOMAIN_ID, "passed": True, "failing": ""}


# Failing ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("records", "stored", "flags", "text"),
    [
        (
            {DOMAIN: []},
            {"spf": "spf_missing"},
            (0, 1, 1, 1),
            "Cold sends blocked: SPF failing",
        ),
        (
            {DOMAIN: [SPF_OK, "v=spf1 -all"]},
            {"spf": "spf_multiple"},
            (0, 1, 1, 1),
            "Cold sends blocked: SPF failing",
        ),
        (
            {DOMAIN: ["v=spf1 include:_spf.example.net -all"]},
            {"spf": "spf_no_google"},
            (0, 1, 1, 1),
            "Cold sends blocked: SPF failing",
        ),
        (
            {f"google._domainkey.{DOMAIN}": []},
            {"dkim": "dkim_missing"},
            (1, 0, 1, 1),
            "Cold sends blocked: DKIM failing",
        ),
        (
            {f"google._domainkey.{DOMAIN}": ["v=DKIM1; p="]},
            {"dkim": "dkim_empty_key"},
            (1, 0, 1, 1),
            "Cold sends blocked: DKIM failing",
        ),
        (
            {f"_dmarc.{DOMAIN}": []},  # NXDOMAIN
            {"dmarc": "dmarc_missing"},
            (1, 1, 0, 1),
            "Cold sends blocked: DMARC failing",
        ),
        (
            {f"_dmarc.{DOMAIN}": ["v=DMARC2; p=none"]},
            {"dmarc": "dmarc_missing"},
            (1, 1, 0, 1),
            "Cold sends blocked: DMARC failing",
        ),
        (
            {f"_dmarc.{DOMAIN}": [DMARC_OK, "v=DMARC1; p=reject"]},
            {"dmarc": "dmarc_multiple"},
            (1, 1, 0, 1),
            "Cold sends blocked: DMARC failing",
        ),
        (
            {DOMAIN: [], f"google._domainkey.{DOMAIN}": []},
            {"spf": "spf_missing", "dkim": "dkim_missing", "alignment": "unauthenticated"},
            (0, 0, 1, 0),
            "Cold sends blocked: SPF, DKIM, Alignment failing",
        ),
    ],
)
def test_each_failure_is_recorded_blocks_cold_sends_and_is_a_health_issue(
    listed: World,
    records: dict[str, list[str]],
    stored: dict[str, str],
    flags: tuple[int, int, int, int],
    text: str,
) -> None:
    listed.dns.txt_answers.update(passing_records(DOMAIN))
    listed.dns.txt_answers.update(records)

    result = listed.check()

    assert result is not None
    assert not result.passed
    assert listed.rows() == [(DOMAIN_ID, STAMP, *flags, 0, details(**stored))]
    view = listed.auth()
    assert view is not None
    assert (view.verdict.blocked, view.verdict.text) == (True, text)
    assert listed.issues() == (
        HealthIssue("domain_auth_failing", DOMAIN_ID, f"{DOMAIN}: {text}", detail=RECHECK_HINT),
    )


def test_a_from_domain_on_another_organizational_domain_is_misaligned(world: World) -> None:
    world.domain(
        DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "active"), ("mac@elsewhere.example", "paused")
    )
    world.dns.txt_answers.update(passing_records(DOMAIN))

    result = world.check()

    assert result is not None
    assert result.judgement.alignment is AuthReason.FROM_DOMAIN_MISMATCH
    assert world.rows() == [
        (DOMAIN_ID, STAMP, 1, 1, 1, 0, 0, details(alignment="from_domain_mismatch"))
    ]
    view = world.auth()
    assert view is not None
    assert view.verdict.text == "Cold sends blocked: Alignment failing"


def test_a_removed_inboxs_address_is_not_a_from_domain(world: World) -> None:
    world.domain(
        DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "active"), ("mac@elsewhere.example", "removed")
    )
    world.dns.txt_answers.update(passing_records(DOMAIN))

    result = world.check()

    assert result is not None
    assert result.passed


# A check that couldn't run ----------------------------------------------------------------------


def test_a_dns_timeout_records_nothing_is_retried_and_an_earlier_pass_still_counts(
    listed: World,
) -> None:
    listed.dns.txt_answers.update(passing_records(DOMAIN))
    listed.check()  # a pass at T0
    listed.dns.errors[f"_dmarc.{DOMAIN}"] = AdapterError(True, "dns_timeout", "timed out")
    listed.clock.advance(timedelta(hours=20))
    with listed.units.open():
        assert listed.checks.recheck(DOMAIN_ID) is True
    with listed.units.open():
        claim = listed.queue.claim(Lane.INTERACTIVE)
    assert claim is not None
    handler = listed.queue._job_types.get("check_domains").handler  # noqa: SLF001

    with pytest.raises(AdapterError) as caught:
        with listed.units.open():
            handler(claim.job)
    with listed.units.open():
        status = listed.queue.fail(claim, caught.value)

    assert status is JobStatus.QUEUED  # retried with backoff
    assert len(listed.rows()) == 1  # nothing recorded for the check that couldn't run
    [(_, _, job_status, _, _, _)] = listed.jobs()
    assert job_status == "queued"
    view = listed.auth()
    assert view is not None
    assert (view.verdict.blocked, view.verdict.text) == (False, "Cold sends allowed")
    listed.clock.advance(timedelta(hours=6))  # the pass is now 26 hours old
    stale = listed.auth()
    assert stale is not None
    assert stale.verdict.text == "Cold sends blocked: no check passed in the last 26 hours"


def test_a_non_retryable_dns_error_propagates_and_records_nothing(listed: World) -> None:
    listed.dns.errors[DOMAIN] = AdapterError(False, "dns_failed", "failed")

    with pytest.raises(AdapterError):
        listed.check()

    assert listed.rows() == []
    view = listed.auth()
    assert view is not None
    assert view.verdict.text == "Cold sends blocked: not checked yet"


def test_an_unknown_domain_is_skipped_without_a_query(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    assert world.check(UNKNOWN_ID) is None

    assert (world.dns.queries, world.rows()) == ([], [])
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.getMessage() == "domain_check_skipped"
    assert record.sending_domain_id == UNKNOWN_ID  # type: ignore[attr-defined]


def test_a_domain_whose_inboxes_are_all_removed_is_skipped_without_a_query(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """A queued or retrying job can outlive the domain's last inbox: decision 6 says such a
    domain is neither checked nor reported, so nothing vacuous is recorded."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    world.domain(GONE_ID, GONE, (f"old@{GONE}", "removed"))
    world.dns.txt_answers.update(passing_records(GONE))

    assert world.check(GONE_ID) is None

    assert (world.dns.queries, world.rows()) == ([], [])
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.getMessage() == "domain_check_skipped"
    assert record.sending_domain_id == GONE_ID  # type: ignore[attr-defined]


# Stale or never checked -------------------------------------------------------------------------


def test_never_checked_stale_and_future_dated_passes_block(listed: World) -> None:
    never = listed.auth()
    assert never is not None
    assert (never.latest, never.verdict.text) == (None, "Cold sends blocked: not checked yet")
    assert listed.issues() == (
        HealthIssue(
            "domain_auth_failing",
            DOMAIN_ID,
            f"{DOMAIN}: Cold sends blocked: not checked yet",
            detail=RECHECK_HINT,
        ),
    )
    listed.dns.txt_answers.update(passing_records(DOMAIN))
    listed.check()

    listed.clock.advance(timedelta(hours=25, minutes=59))
    assert listed.issues() == ()
    listed.clock.advance(timedelta(minutes=1))
    stale = listed.auth()
    assert stale is not None
    assert stale.verdict.text == "Cold sends blocked: no check passed in the last 26 hours"
    assert len(listed.issues()) == 1
    listed.clock.set(T0 - timedelta(minutes=1))  # the clock was set back: the pass is ahead
    future = listed.auth()
    assert future is not None
    assert future.verdict.blocked


def test_the_newest_check_decides(listed: World) -> None:
    listed.dns.txt_answers.update(passing_records(DOMAIN))
    listed.check()
    listed.clock.advance(timedelta(hours=1))
    listed.dns.txt_answers[DOMAIN] = []
    listed.check()

    view = listed.auth()

    assert view is not None
    assert view.latest is not None
    assert (view.latest.checked_at, view.latest.failing) == (
        T0 + timedelta(hours=1),
        (AuthItem.SPF,),
    )
    assert view.verdict.text == "Cold sends blocked: SPF failing"


def test_a_future_stamped_pass_never_hides_a_check_recorded_after_it(listed: World) -> None:
    """The clock was set back after a check: the newest check is the last recorded, so once
    real time passes the future stamp an old pass still can't outrank a newer failure."""
    listed.dns.txt_answers.update(passing_records(DOMAIN))
    listed.clock.set(T0 + timedelta(hours=2))
    listed.check()  # a pass stamped 11:00
    listed.clock.set(T0)  # the clock is set back to 09:00
    assert listed.read(listed.checks.schedule).missed is True  # a stamp ahead proves nothing
    listed.dns.txt_answers[DOMAIN] = []
    listed.check()  # a failure stamped 09:00, recorded after the pass
    listed.clock.set(T0 + timedelta(hours=3))  # 12:00: past the pass's stamp

    view = listed.auth()

    assert view is not None
    assert view.latest is not None
    assert (view.latest.checked_at, view.latest.failing) == (T0, (AuthItem.SPF,))
    assert view.verdict.text == "Cold sends blocked: SPF failing"
    assert listed.read(listed.checks.schedule).missed is False  # the newest is 09:00, after 07:00


# Recheck and the daily tick ---------------------------------------------------------------------


def test_a_recheck_twice_queues_one_interactive_job(
    listed: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    with listed.units.open():
        first = listed.checks.recheck(DOMAIN_ID)
    with listed.units.open():
        second = listed.checks.recheck(DOMAIN_ID)

    assert (first, second) == (True, True)
    logged = [
        (r.getMessage(), getattr(r, "sending_domain_id"), getattr(r, "job_created"))
        for r in caplog.records
        if r.name == LOGGER
    ]
    assert logged == [
        ("domain_recheck_queued", DOMAIN_ID, True),
        ("domain_recheck_queued", DOMAIN_ID, False),  # absorbed by the queued job
    ]
    assert listed.jobs() == [
        (
            "check_domains",
            "interactive",
            "queued",
            f"check_domains:{DOMAIN_ID}:tick",
            json.dumps({"sending_domain_id": DOMAIN_ID, "v": 1}, separators=(",", ":")),
            60,
        )
    ]


def test_a_recheck_of_an_unlisted_or_unknown_domain_queues_nothing(world: World) -> None:
    world.domain(GONE_ID, GONE, (f"old@{GONE}", "removed"))

    with world.units.open():
        gone = world.checks.recheck(GONE_ID)
    with world.units.open():
        unknown = world.checks.recheck(UNKNOWN_ID)

    assert (gone, unknown) == (False, False)
    assert world.jobs() == []


def test_the_daily_tick_queues_one_job_per_listed_domain(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    world.domain(DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "active"))
    world.domain(OTHER_ID, OTHER, (f"furqaan@{OTHER}", "paused"), (f"mac@{OTHER}", "removed"))
    world.domain(GONE_ID, GONE, (f"old@{GONE}", "removed"))

    with world.units.open():
        count = world.checks.queue_daily()

    assert count == 2
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert (record.getMessage(), getattr(record, "count")) == ("domain_checks_queued", 2)
    assert [(job[0], job[1], job[3]) for job in world.jobs()] == [
        ("check_domains", "interactive", f"check_domains:{DOMAIN_ID}:tick"),
        ("check_domains", "interactive", f"check_domains:{OTHER_ID}:tick"),
    ]
    with world.units.open():
        assert world.checks.queue_daily() == 2  # absorbed by the queued jobs
    assert len(world.jobs()) == 2


def test_the_handler_checks_a_domain_queues_the_daily_checks_or_refuses_a_bad_id(
    listed: World,
) -> None:
    handler = listed.queue._job_types.get("check_domains").handler  # noqa: SLF001
    listed.dns.txt_answers.update(passing_records(DOMAIN))
    payload = {"v": 1, "sending_domain_id": DOMAIN_ID}

    with listed.units.open():
        handler(Job("01JD0000000000000000000099", "check_domains", Lane.INTERACTIVE, payload, 1))
    with listed.units.open():
        handler(Job("01JD0000000000000000000098", "check_domains", Lane.INTERACTIVE, {"v": 1}, 1))

    assert len(listed.rows()) == 1
    assert [job[3] for job in listed.jobs()] == [f"check_domains:{DOMAIN_ID}:tick"]
    for bad in ("not-a-ulid", 7, None):
        job = Job(
            "01JD0000000000000000000097",
            "check_domains",
            Lane.INTERACTIVE,
            {"v": 1, "sending_domain_id": bad},
            1,
        )
        with pytest.raises(InvalidJob, match="sending_domain_id field must be a ULID"):
            handler(job)
    assert len(listed.rows()) == 1


# The schedule -----------------------------------------------------------------------------------


def test_the_schedule_is_07_00_in_the_sender_timezone_and_catches_up_a_missed_check(
    world: World,
) -> None:
    def schedule() -> DomainCheckSchedule:
        return world.read(world.checks.schedule)

    assert schedule() == DomainCheckSchedule(time(7, 0), "UTC", False)  # nothing listed
    world.domain(DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "connecting"))
    assert schedule() == DomainCheckSchedule(time(7, 0), "UTC", True)  # never checked
    world.dns.txt_answers.update(passing_records(DOMAIN))
    world.check()  # 09:00 UTC, after today's 07:00 UTC slot
    assert schedule().missed is False
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/Los_Angeles"})
    # 09:00 UTC is 01:00 in Los Angeles: the latest 07:00 there was 15:00 UTC yesterday
    assert schedule() == DomainCheckSchedule(time(7, 0), "America/Los_Angeles", False)
    world.clock.advance(timedelta(hours=6))  # 15:00 UTC: today's 07:00 in Los Angeles is due
    assert schedule().missed is True


def test_the_schedule_reads_each_domains_newest_check(world: World) -> None:
    world.domain(DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "active"))
    world.domain(OTHER_ID, OTHER, (f"furqaan@{OTHER}", "active"))
    world.dns.txt_answers.update({**passing_records(DOMAIN), **passing_records(OTHER)})
    world.clock.set(T0 - timedelta(days=1))  # yesterday 09:00, before today's 07:00 slot
    world.check(DOMAIN_ID)
    world.check(OTHER_ID)
    world.clock.set(T0)
    assert world.read(world.checks.schedule).missed is True  # both newest before the slot
    world.check(DOMAIN_ID)  # 09:00 today, after the slot; OTHER's newest is still yesterday's
    assert world.read(world.checks.schedule).missed is True
    world.check(OTHER_ID)

    assert world.read(world.checks.schedule).missed is False  # an old and a newer check each


def test_read_domain_check_schedule_mirrors_the_backup_schedule(world: World) -> None:
    world.domain(DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "connecting"))

    run = main.read_domain_check_schedule(world.units, world.checks)

    assert (run.at, run.timezone, run.catch_up) == (time(7, 0), "UTC", True)


# Reads ------------------------------------------------------------------------------------------


def test_the_reads_list_only_listed_domains_sorted_by_domain(world: World) -> None:
    world.domain(OTHER_ID, OTHER, (f"furqaan@{OTHER}", "auth_failed"))
    world.domain(DOMAIN_ID, DOMAIN, (f"furqaan@{DOMAIN}", "active"))
    world.domain(GONE_ID, GONE, (f"old@{GONE}", "removed"))

    views = world.read(lambda: sending_queries.domain_auths(world.clock.now()))

    assert [(view.sending_domain_id, view.domain) for view in views] == [
        (DOMAIN_ID, DOMAIN),
        (OTHER_ID, OTHER),
    ]
    assert world.auth(GONE_ID) is None
    assert world.auth(UNKNOWN_ID) is None
    assert world.auth("not-a-ulid") is None
    assert [issue.subject for issue in world.issues()] == [DOMAIN_ID, OTHER_ID]


def test_the_page_read_uses_the_services_clock(listed: World) -> None:
    views = listed.read(listed.checks.auths)

    assert [(view.domain, view.verdict.text) for view in views] == [
        (DOMAIN, "Cold sends blocked: not checked yet")
    ]


# The table --------------------------------------------------------------------------------------


def test_passed_must_be_the_and_of_the_flags(listed: World) -> None:
    with pytest.raises(IntegrityError, match="ck_domain_checks_passed"):
        listed.sql(
            f"INSERT INTO domain_checks (id, {CHECK_COLUMNS}) "
            "VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0C1', ?, ?, 0, 1, 1, 1, 1, ?)",
            (DOMAIN_ID, STAMP, details()),
        )
    with pytest.raises(IntegrityError, match="ck_domain_checks_passed"):
        listed.sql(
            f"INSERT INTO domain_checks (id, {CHECK_COLUMNS}) "
            "VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0C2', ?, ?, 1, 1, 1, 1, 0, ?)",
            (DOMAIN_ID, STAMP, details()),
        )


def test_a_check_needs_a_stored_sending_domain(world: World) -> None:
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        world.sql(
            f"INSERT INTO domain_checks (id, {CHECK_COLUMNS}) "
            "VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0C3', ?, ?, 1, 1, 1, 1, 1, ?)",
            (UNKNOWN_ID, STAMP, details()),
        )


# Wiring -----------------------------------------------------------------------------------------


def test_build_app_builds_domain_checks_on_its_dnspython_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    clocks: list[FrozenClock] = []
    built_dns: list[Any] = []
    built_checks: list[DomainChecks] = []
    registries: list[dict[str, Any]] = []
    real_dns, real_checks = main.DnspythonDns, main.DomainChecks
    real_build_job_types = main.build_job_types

    def system_clock() -> FrozenClock:
        clocks.append(FrozenClock(T0))
        return clocks[-1]

    def dnspython(**kwargs: Any) -> Any:
        built_dns.append(real_dns(**kwargs))
        return built_dns[-1]

    def domain_checks(*args: Any) -> DomainChecks:
        built_checks.append(real_checks(*args))
        return built_checks[-1]

    def recording_job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_build_job_types(**kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "SystemClock", system_clock)
    monkeypatch.setattr(main, "DnspythonDns", dnspython)
    monkeypatch.setattr(main, "DomainChecks", domain_checks)
    monkeypatch.setattr(main, "build_job_types", recording_job_types)

    main.build_app(load_bootstrap(), units=FakeUnits())

    [clock] = clocks  # one clock: the health issues read the Sending Domains on it too
    [dns] = built_dns
    [checks] = built_checks
    assert dns._resolver is None  # noqa: SLF001 - building it built no resolver
    assert checks._dns is dns  # noqa: SLF001 - the wiring under test
    assert checks._clock is clock  # noqa: SLF001
    [given] = registries  # the check_domains jobs reach the built service, not the default
    assert given["domain_checks"]() is checks


def test_build_app_takes_an_injected_dns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[DomainChecks] = []
    real_checks = main.DomainChecks

    def domain_checks(*args: Any) -> DomainChecks:
        built.append(real_checks(*args))
        return built[-1]

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "DomainChecks", domain_checks)
    fake = FakeDns()

    main.build_app(load_bootstrap(), units=FakeUnits(), dns=fake)

    [checks] = built
    assert checks._dns is fake  # noqa: SLF001
