"""``UnsubscribePolls.poll`` (FR-70, FR-41, AD-10, AD-15, AD-20, Stories 3.11 and 3.12)
through the ``poll_unsubscribes`` job handler of the real job type registry, on a temporary
database at head with the real subscribers, ``FakeTokenCodec``, ``ScriptedUnsubscribe``,
``InMemoryUsageMeter`` and ``FrozenClock``: polling off while the host is blank, a fetch applied
in one transaction, a repeat across polls, a crash before the commit that keeps nothing and lands
once on the next poll, a 3-day backlog from while the PC was off, invalid tokens and malformed
entries (one dashboard notification a day, a WARNING each poll, nothing recorded), a Worker
failure that applies nothing, the budget exhausted, and no token, address, host or bearer in a
log line, a payload or the notification.

Story 3.12's monitoring, every matrix row: the ``unsub_poll_state`` row after a good poll and a
failure, a missing bearer for 25 hours (each job ``done``, one ``unsub_poll_failing`` alert, one
health issue, no ``job_dead``), the recovery that clears the episode, failures under 24 hours,
a poll that never succeeded, a bug (``internal_error`` recorded and re-raised), the
``repoll_unsubscribes`` recovery step after a restore (and pending while the host is blank), and
the KV list notice at 800 calls a day."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.unsub_poll import PollCounts, UnsubscribePolls
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import DeadJobAlerts, Recovery
from nextron.app.queries import compliance as compliance_queries
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.budget import ApiCall
from nextron.domain.contactability import key_hash
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.health import HealthIssue
from nextron.domain.job import Job, Lane
from nextron.domain.recovery import RecoveryContext, RecoveryStep
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.segments import validate_segments
from nextron.domain.settings_schema import Key
from nextron.domain.unsub_token import token_hash
from nextron.ports.errors import AdapterError
from nextron.jobs.recovery import RecoveryRunner
from nextron.ports.secrets import CREDENTIAL_NOT_SET
from nextron.ports.unsubscribe import (
    UNSUB_BAD_RESPONSE,
    UNSUB_UNAUTHORIZED,
    UNSUB_UNAVAILABLE,
    PendingFetch,
    PendingUnsub,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.token_codec import FakeTokenCodec
from tests.fakes.unsubscribe import ScriptedUnsubscribe, pending
from tests.fakes.usage_meter import InMemoryUsageMeter

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
HOST = "u.example-sender.com"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
EMAIL = "jane.doe@firm.co.uk"
OTHER_EMAIL = "sam.lee@firm.co.uk"
ADDRESS = key_hash("address", EMAIL)
OTHER = key_hash("address", OTHER_EMAIL)
TAMPERED = "A" + "B" * 107  # the right shape; the fake never encoded it: bad_tag
JOB_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AA"
LOGGER = "nextron.app.compliance"
QUOTA_KEYS = (
    Key.QUOTA_HUNTER_CREDITS,
    Key.QUOTA_PROSPEO_CREDITS,
    Key.QUOTA_PLACES_ENTERPRISE,
    Key.QUOTA_PLACES_PRO,
    Key.QUOTA_JOB_AGGREGATOR,
)


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    codec: FakeTokenCodec
    port: ScriptedUnsubscribe
    meter: InMemoryUsageMeter
    notify: Notify
    compliance: Compliance
    polls: UnsubscribePolls
    discovery: Discovery
    research: Research
    handler: Callable[[Job], object]
    segment: str = ""

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def set(self, key: str, value: object) -> None:
        with self.units.open():
            Setup(self.clock).set(key, value)

    def run_job(self) -> object:
        """Run the ``poll_unsubscribes`` job's handler as the runner does: in a unit of work of
        its own, with a trigger's tick payload."""
        with self.units.open():
            return self.handler(Job(JOB_ID, "poll_unsubscribes", Lane.REALTIME, {"v": 1}, 1))

    def poll(self) -> PollCounts | None:
        with self.units.open():
            return self.polls.poll()

    def lead_with(self, email: str, web_domain: str) -> str:
        identity = LeadIdentity(name="Firm LLP", segment_id=self.segment, web_domain=web_domain)
        lead_id = self.transact(
            lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)
        ).lead_id
        candidate = ContactCandidate(name="Jane Doe", email=email)
        self.transact(
            lambda: self.research.add_contact(
                lead_id,
                candidate,
                source=ProvenanceSource.CSV,
                detail="Law Society directory export",
                retrieved_at=T0,
            )
        )
        return lead_id

    def token(self, address_hash: str = ADDRESS) -> str:
        return self.codec.encode(1, bytes.fromhex(address_hash), int(T0.timestamp()))

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    def entries(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT kind, value_hash, source FROM suppression_entries ORDER BY rowid")

    def requests(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT token_hash, received_at FROM unsub_requests ORDER BY rowid")

    def notices(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT kind, subject, text FROM notifications ORDER BY rowid")

    def stage(self, lead_id: str) -> str:
        [(stage,)] = self.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (lead_id,))
        return str(stage)

    def state(self) -> tuple[Any, ...] | None:
        """The ``unsub_poll_state`` row: last success, failing since, last failure and code."""
        with self.units.open(read_only=True):
            found = compliance_queries.unsub_poll_status(True).state
        if found is None:
            return None
        return (
            found.last_success_at,
            found.failing_since,
            found.last_failure_at,
            found.last_failure_code,
        )

    def issues(self, *, polling_on: bool = True) -> tuple[HealthIssue, ...]:
        with self.units.open(read_only=True):
            return compliance_queries.unsub_poll_issues(self.clock.now(), polling_on=polling_on)

    def alerts(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT kind, subject, channel, text FROM alerts ORDER BY rowid")

    def open_notices(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT kind, subject, text FROM notifications WHERE dismissed_at IS NULL "
            "ORDER BY rowid"
        )


def no_jobs() -> Any:
    raise AssertionError("only poll_unsubscribes runs in these tests")


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    codec = FakeTokenCodec()
    port = ScriptedUnsubscribe()
    compliance = Compliance(clock, codec=codec)
    holder: dict[str, UnsubscribePolls] = {}
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
        job_boards=no_jobs,
        legal_forms=no_jobs,
        scores=no_jobs,
        unsubscribes=lambda: holder["polls"],
    )
    queue = JobQueue(clock, job_types)
    notify = Notify(clock, queue, alert_ports={})
    meter = InMemoryUsageMeter(clock)
    polls = UnsubscribePolls(clock, compliance, port, notify, meter)
    holder["polls"] = polls
    found = World(
        units,
        db_engine,
        clock,
        codec,
        port,
        meter,
        notify,
        compliance,
        polls,
        Discovery(clock, Pipeline(clock), queue),
        Research(clock),
        job_types.get("poll_unsubscribes").handler,
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    found.set(Key.UNSUB_WORKER_HOST, HOST)
    return found


def entry(token: str, at: datetime = T0 - timedelta(minutes=3)) -> PendingUnsub:
    return PendingUnsub(token, at)


def assert_clean_logs(caplog: pytest.LogCaptureFixture, *tokens: str) -> None:
    text = "\n".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    )
    for planted in (*tokens, HOST, EMAIL, ADDRESS, "@"):
        assert planted not in text


# Polling off ------------------------------------------------------------------------------------


def test_a_blank_host_makes_no_request_and_logs_skipped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    world.port.answers.append(pending(entry(world.token())))
    with world.units.open():
        Setup(world.clock).save({Key.UNSUB_WORKER_HOST: ""})  # back to the default, blank

    assert world.run_job() is None

    assert world.port.hosts == []
    assert world.requests() == []
    assert [r.msg for r in caplog.records if r.name.startswith(LOGGER)] == ["unsub_poll_skipped"]


# Applying ---------------------------------------------------------------------------------------


def test_a_poll_applies_every_entry_and_logs_the_counts(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lead_id = world.lead_with(EMAIL, "firm.co.uk")
    first, second = world.token(), world.token(OTHER)
    world.port.answers.append(pending(entry(first), entry(second)))

    world.run_job()

    assert world.port.hosts == [HOST]
    assert world.requests() == [
        (token_hash(first), "2026-11-18T08:57:00.000000Z"),
        (token_hash(second), "2026-11-18T08:57:00.000000Z"),
    ]
    assert ("address", ADDRESS, "one_click") in world.entries()
    assert ("address", OTHER, "one_click") in world.entries()
    assert world.stage(lead_id) == "lost"
    [applied] = [r for r in caplog.records if r.msg == "unsub_poll_applied"]
    assert (applied.fetched, applied.applied, applied.duplicate, applied.invalid) == (2, 2, 0, 0)  # type: ignore[attr-defined]
    assert applied.malformed == 0  # type: ignore[attr-defined]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert world.notices() == []
    assert_clean_logs(caplog, first, second)


def test_a_token_seen_again_on_the_next_poll_is_a_duplicate(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    world.lead_with(EMAIL, "firm.co.uk")
    token = world.token()
    world.port.answers.append(pending(entry(token)))  # the Worker keeps it: same list each poll

    first = world.poll()
    entries = world.entries()
    second = world.poll()

    assert first == PollCounts(fetched=1, applied=1, duplicate=0, invalid=0, malformed=0)
    assert second == PollCounts(fetched=1, applied=0, duplicate=1, invalid=0, malformed=0)
    assert world.entries() == entries
    assert len(world.requests()) == 1


def test_a_crash_before_the_commit_keeps_nothing_and_the_next_poll_lands_each_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead_id = world.lead_with(EMAIL, "firm.co.uk")
    first, second = world.token(), world.token(OTHER)
    world.port.answers.append(pending(entry(first), entry(second)))
    original = world.compliance.unsubscribe_by_token
    calls: list[int] = []

    def crash_on_the_second(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the engine stopped")
        return original(*args)

    monkeypatch.setattr(world.compliance, "unsubscribe_by_token", crash_on_the_second)
    with pytest.raises(RuntimeError, match="the engine stopped"):
        world.run_job()

    assert (world.entries(), world.requests(), world.stage(lead_id)) == ([], [], "new")
    monkeypatch.setattr(world.compliance, "unsubscribe_by_token", original)

    world.run_job()

    assert sorted(hashed for hashed, _ in world.requests()) == sorted(
        [token_hash(first), token_hash(second)]
    )
    assert world.stage(lead_id) == "lost"


def test_a_three_day_backlog_from_while_the_pc_was_off_lands_with_its_own_times(
    world: World,
) -> None:
    tokens = [world.token(key_hash("address", f"person{n}@firm.co.uk")) for n in range(3)]
    days = [T0 - timedelta(days=3, hours=2), T0 - timedelta(days=2), T0 - timedelta(hours=20)]
    world.port.answers.append(pending(*(entry(t, at) for t, at in zip(tokens, days, strict=True))))

    counts = world.poll()

    assert counts == PollCounts(fetched=3, applied=3, duplicate=0, invalid=0, malformed=0)
    assert world.requests() == [
        (token_hash(token), at.strftime("%Y-%m-%dT%H:%M:%S.000000Z"))
        for token, at in zip(tokens, days, strict=True)
    ]


def test_the_poll_still_runs_when_the_budget_is_exhausted(world: World) -> None:
    for key in QUOTA_KEYS:
        world.set(key, 0)
    world.set(Key.AI_BUDGET_MONTHLY, 0)
    world.port.answers.append(pending(entry(world.token())))

    world.run_job()

    assert len(world.requests()) == 1


# Failures ---------------------------------------------------------------------------------------


def test_invalid_tokens_and_malformed_entries_are_not_recorded_and_notify_once_a_day(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    good = world.token()
    world.port.answers.append(pending(entry(TAMPERED), entry("short"), entry(good), malformed=2))

    counts = world.poll()

    assert counts == PollCounts(fetched=5, applied=1, duplicate=0, invalid=2, malformed=2)
    assert [hashed for hashed, _ in world.requests()] == [token_hash(good)]
    assert world.notices() == [
        (
            "unsub_tokens_invalid",
            "unsub_invalid:2026-11-18",
            "4 unsubscribe requests from the Worker could not be decoded and were not applied; "
            "check the unsubscribe token keys",
        )
    ]
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert (warning.msg, warning.count) == ("unsub_tokens_invalid", 4)  # type: ignore[attr-defined]

    caplog.clear()
    again = world.poll()  # the Worker still holds them: tried again, noticed once a day

    assert again == PollCounts(fetched=5, applied=0, duplicate=1, invalid=2, malformed=2)
    assert len(world.notices()) == 1
    assert [r.msg for r in caplog.records if r.levelno == logging.WARNING] == [
        "unsub_tokens_invalid"
    ]
    world.clock.set(T0 + timedelta(days=1))
    world.poll()
    assert [subject for _, subject, _ in world.notices()] == [
        "unsub_invalid:2026-11-18",
        "unsub_invalid:2026-11-19",
    ]
    assert_clean_logs(caplog, TAMPERED, good)


@pytest.mark.parametrize("retryable", [True, False])
def test_a_worker_failure_applies_nothing_and_the_job_ends_normally(
    world: World, retryable: bool
) -> None:
    world.port.answers.append(AdapterError(retryable, UNSUB_UNAVAILABLE, "unavailable"))

    assert world.run_job() is None  # Story 3.12: recorded, not raised; the next firing retries

    assert (world.requests(), world.entries(), world.notices()) == ([], [], [])
    assert world.state() == (None, T0, T0, "unsub_unavailable")


def test_an_empty_list_applies_nothing_and_raises_no_notice(world: World) -> None:
    world.port.answers.append(PendingFetch((), 0))

    assert world.poll() == PollCounts(fetched=0, applied=0, duplicate=0, invalid=0, malformed=0)
    assert (world.requests(), world.notices()) == ([], [])


def test_the_fetch_runs_outside_any_transaction(world: World) -> None:
    from nextron.db.uow import in_transaction  # noqa: PLC0415

    seen: list[bool] = []

    def fetch() -> PendingFetch:
        seen.append(in_transaction())
        return PendingFetch((), 0)

    world.port.answers.append(fetch)

    world.poll()

    assert seen == [False]


# Wiring -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("env", "minutes"), [("test", 30), ("dev", 30), ("prod", 5)])
def test_build_app_wires_the_poll_on_the_worker_adapter_and_the_env_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str, minutes: int
) -> None:
    from nextron.adapters.crypto.unsub_token import TokenCodec  # noqa: PLC0415
    from nextron.adapters.unsubscribe import WorkerUnsubscribe  # noqa: PLC0415
    from nextron.bootstrap import load_bootstrap  # noqa: PLC0415
    from tests.fakes.secrets import InMemorySecrets  # noqa: PLC0415
    from tests.fakes.units import FakeUnits  # noqa: PLC0415

    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[UnsubscribePolls] = []
    registries: list[dict[str, Any]] = []
    trigger_args: list[dict[str, Any]] = []
    real_polls, real_job_types, real_triggers = (
        main.UnsubscribePolls,
        main.build_job_types,
        main.build_triggers,
    )

    def polls(*args: Any) -> UnsubscribePolls:
        built.append(real_polls(*args))
        return built[-1]

    def job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_job_types(**kwargs)

    def triggers(*args: Any, **kwargs: Any) -> Any:
        trigger_args.append(kwargs)
        return real_triggers(*args, **kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "UnsubscribePolls", polls)
    monkeypatch.setattr(main, "build_job_types", job_types)
    monkeypatch.setattr(main, "build_triggers", triggers)

    main.build_app(load_bootstrap(env), units=FakeUnits())

    [poll] = built
    assert isinstance(poll._port, WorkerUnsubscribe)  # noqa: SLF001 - the wiring under test
    assert poll._port._http is None  # noqa: SLF001 - building it built no HTTP client
    assert isinstance(poll._compliance._codec, TokenCodec)  # noqa: SLF001
    [given] = registries
    assert given["unsubscribes"]() is poll
    [kwargs] = trigger_args
    assert kwargs["unsub_poll_period"] == timedelta(minutes=minutes)


def test_build_app_takes_an_injected_unsubscribe_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nextron.bootstrap import load_bootstrap  # noqa: PLC0415
    from tests.fakes.secrets import InMemorySecrets  # noqa: PLC0415
    from tests.fakes.units import FakeUnits  # noqa: PLC0415

    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[UnsubscribePolls] = []
    real_polls = main.UnsubscribePolls

    def polls(*args: Any) -> UnsubscribePolls:
        built.append(real_polls(*args))
        return built[-1]

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "UnsubscribePolls", polls)
    fake = ScriptedUnsubscribe()

    main.build_app(load_bootstrap(), units=FakeUnits(), unsubscribe=fake)

    [poll] = built
    assert poll._port is fake  # noqa: SLF001


def test_build_app_registers_the_repoll_step_on_its_poll_and_its_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nextron.bootstrap import load_bootstrap  # noqa: PLC0415
    from tests.fakes.secrets import InMemorySecrets  # noqa: PLC0415
    from tests.fakes.units import FakeUnits  # noqa: PLC0415

    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[UnsubscribePolls] = []
    registries: list[Any] = []
    real_polls, real_steps = main.UnsubscribePolls, main.build_recovery_steps

    def polls(*args: Any) -> UnsubscribePolls:
        built.append(real_polls(*args))
        return built[-1]

    def steps(**kwargs: Any) -> Any:
        registries.append(real_steps(**kwargs))
        return registries[-1]

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "UnsubscribePolls", polls)
    monkeypatch.setattr(main, "build_recovery_steps", steps)
    repolled: list[RecoveryContext] = []

    main.build_app(load_bootstrap(), units=FakeUnits())

    [poll] = built
    assert poll._meter is not None  # noqa: SLF001 - the engine's own meter
    [registry] = registries
    handler = registry.get(RecoveryStep.REPOLL_UNSUBSCRIBES)
    assert (handler.owner, handler.startup) == ("compliance", False)
    monkeypatch.setattr(poll, "repolled", lambda context: repolled.append(context) or True)
    context = RecoveryContext("01JD3QZ8X5V9K2M7N4P6R8T0B9", T0 - timedelta(days=1), T0)
    assert handler.run(context) is True
    assert repolled == [context]


# Monitoring (Story 3.12) ------------------------------------------------------------------------

BEARER_MISSING = AdapterError(False, CREDENTIAL_NOT_SET, "credential unsubscribe_bearer is not set")
FAILING_KIND = "unsub_poll_failing"
FAILING_SINCE_T0 = "Unsubscribe polling failing since 2026-11-18 09:00 UTC"


def failing_subject(since: datetime) -> str:
    return f"unsub_poll_failing:{int(since.timestamp())}"


def test_a_good_poll_records_its_time(world: World) -> None:
    world.port.answers.append(pending(entry(world.token())))

    world.run_job()

    assert world.state() == (T0, None, None, None)
    assert (world.alerts(), world.issues()) == ([], ())


def test_a_blank_host_records_nothing(world: World) -> None:
    with world.units.open():
        Setup(world.clock).save({Key.UNSUB_WORKER_HOST: ""})  # blank: polling off

    world.run_job()

    assert world.state() is None


def test_a_missing_bearer_for_25_hours_is_one_alert_one_issue_and_no_dead_job(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.port.answers.append(BEARER_MISSING)
    polls = 0
    while world.clock.now() <= T0 + timedelta(hours=25):
        assert world.run_job() is None  # the job is done each time
        polls += 1
        world.clock.advance(timedelta(minutes=30))

    assert polls == 51
    last = T0 + timedelta(hours=25)
    assert world.state() == (None, T0, last, "unsub_bearer_invalid")
    text = f"{FAILING_SINCE_T0} (unsub_bearer_invalid)"
    assert world.alerts() == [(FAILING_KIND, failing_subject(T0), "telegram", text)]
    assert world.open_notices() == [(FAILING_KIND, failing_subject(T0), text)]
    assert world.issues() == (HealthIssue(FAILING_KIND, "unsub_poll", FAILING_SINCE_T0),)
    assert world.issues(polling_on=False) == ()  # a blank host is off, never an issue
    with world.units.open():
        assert DeadJobAlerts(world.notify).alert_dead_jobs() == 0
    assert world.sql("SELECT count(*) FROM jobs WHERE status = 'dead'") == [(0,)]
    failed = [r for r in caplog.records if r.msg == "unsub_poll_failed"]
    assert len(failed) == polls
    assert failed[0].code == "unsub_bearer_invalid"  # type: ignore[attr-defined]
    assert_clean_logs(caplog)


def test_a_recovery_after_30_hours_ends_the_episode_and_clears_its_notice(world: World) -> None:
    world.port.answers.append(AdapterError(False, UNSUB_UNAUTHORIZED, "unauthorized"))
    world.run_job()
    world.clock.advance(timedelta(hours=30))
    world.run_job()
    assert len(world.open_notices()) == 1
    assert len(world.issues()) == 1
    world.port.answers[:] = [pending(entry(world.token()))]
    world.clock.advance(timedelta(minutes=5))

    world.run_job()

    now = T0 + timedelta(hours=30, minutes=5)
    assert world.state() == (now, None, None, None)
    assert world.issues() == ()
    assert world.open_notices() == []
    [(dismissed,)] = world.sql("SELECT dismissed_at FROM notifications")
    assert dismissed == now.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    world.run_job()  # a second success has no episode to clear
    assert world.state() == (now, None, None, None)


def test_failures_under_24_hours_after_a_success_show_but_raise_nothing(world: World) -> None:
    world.port.answers.append(pending())
    world.run_job()  # the last success
    world.port.answers[:] = [AdapterError(True, UNSUB_UNAVAILABLE, "unavailable")]
    world.clock.advance(timedelta(hours=1))
    world.run_job()
    world.clock.advance(timedelta(hours=2))

    world.run_job()

    assert world.state() == (
        T0,
        T0 + timedelta(hours=1),
        T0 + timedelta(hours=3),
        "unsub_unavailable",
    )
    assert (world.alerts(), world.notices(), world.issues()) == ([], [], ())


def test_a_poll_that_never_succeeded_fails_from_its_first_failure(world: World) -> None:
    world.port.answers.append(AdapterError(False, UNSUB_BAD_RESPONSE, "bad response"))
    world.run_job()
    world.clock.advance(timedelta(hours=25))

    world.run_job()

    assert world.issues() == (HealthIssue(FAILING_KIND, "unsub_poll", FAILING_SINCE_T0),)
    [(kind, subject, channel, _)] = world.alerts()
    assert (kind, subject, channel) == (FAILING_KIND, failing_subject(T0), "telegram")


def test_an_unknown_adapter_code_is_internal_error(world: World) -> None:
    world.port.answers.append(AdapterError(True, "keyring_failed", "keyring"))

    assert world.run_job() is None

    assert world.state() == (None, T0, T0, "internal_error")


def test_a_bug_is_recorded_as_internal_error_and_re_raised(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.port.answers.append(pending(entry(world.token())))

    def broken(*args: Any) -> Any:
        raise RuntimeError("a bug")

    monkeypatch.setattr(world.compliance, "unsubscribe_by_token", broken)

    with pytest.raises(RuntimeError, match="a bug"):
        world.run_job()

    assert world.state() == (None, T0, T0, "internal_error")
    assert world.requests() == []


def test_an_apply_side_adapter_error_rolls_back_records_internal_error_and_ends_done(
    world: World,
) -> None:
    lead_id = world.lead_with(EMAIL, "firm.co.uk")
    world.port.answers.append(pending(entry(world.token())))
    world.codec.fail = AdapterError(True, "keyring_failed", "Credential Manager failed")

    assert world.run_job() is None  # the job is done

    assert (world.requests(), world.entries(), world.stage(lead_id)) == ([], [], "new")
    assert world.state() == (None, T0, T0, "internal_error")


def test_an_adapter_failure_whose_recording_fails_still_ends_done(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    world.port.answers.append(AdapterError(True, UNSUB_UNAVAILABLE, "unavailable"))

    def no_meter(*args: Any) -> int:
        raise OSError("the meter broke")

    monkeypatch.setattr(world.meter, "day_calls", no_meter)

    assert world.run_job() is None

    [record] = [r for r in caplog.records if r.msg == "unsub_poll_record_failed"]
    assert (record.levelno, record.code) == (logging.ERROR, "OSError")  # type: ignore[attr-defined]
    assert world.state() is None  # the recording rolled back


def test_a_bug_whose_recording_fails_still_raises_the_bug(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: Any) -> Any:
        raise RuntimeError("a bug")

    def no_meter(*args: Any) -> int:
        raise OSError("the meter broke")

    monkeypatch.setattr(world.port, "fetch_pending", broken)
    monkeypatch.setattr(world.meter, "day_calls", no_meter)

    with pytest.raises(RuntimeError, match="a bug"):
        world.run_job()

    [record] = [r for r in caplog.records if r.msg == "unsub_poll_record_failed"]
    assert (record.levelno, record.code) == (logging.ERROR, "OSError")  # type: ignore[attr-defined]
    assert world.state() is None  # the recording rolled back


def test_dead_poll_jobs_alert_at_most_once_a_day(world: World) -> None:
    rows = [
        ("01JD3QZ8X5V9K2M7N4P6R8T0B1", "2026-11-18T01:00:00.000000Z"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0B2", "2026-11-18T23:00:00.000000Z"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0B3", "2026-11-19T00:30:00.000000Z"),
    ]
    for job_id, at in rows:
        world.sql(
            "INSERT INTO jobs (id, type, lane, status, priority, payload, idempotency_key, "
            "run_after, attempts, created_at, updated_at) VALUES (?, 'poll_unsubscribes', "
            "'realtime', 'dead', 80, '{}', ?, ?, 3, ?, ?)",
            (job_id, job_id, at, at, at),
        )

    with world.units.open():
        assert DeadJobAlerts(world.notify).alert_dead_jobs() == 2
    with world.units.open():
        assert DeadJobAlerts(world.notify).alert_dead_jobs() == 0

    assert [subject for _, subject, _, _ in world.alerts()] == [
        "poll_unsubscribes:2026-11-18",
        "poll_unsubscribes:2026-11-19",
    ]


# The recovery step ------------------------------------------------------------------------------


def restore(world: World) -> RecoveryRunner:
    """Turn recovery mode on, as ``nextron restore`` does, and return a runner on a registry
    holding only the ``repoll_unsubscribes`` step."""
    recovery = Recovery(world.clock)
    with world.units.open():
        recovery.begin(
            backup_at=T0 - timedelta(days=1), source_name="nextron-test.db", pre_restore_name=None
        )
    return RecoveryRunner(
        world.units, recovery, main.build_recovery_steps(repoll=world.polls.repolled)
    )


def recovery_on(world: World) -> bool:
    [(count,)] = world.sql("SELECT count(*) FROM recovery_state WHERE mode = 'on'")
    return bool(count)


def repoll_status(world: World) -> str:
    [(status,)] = world.sql("SELECT repoll_unsubscribes FROM recovery_state")
    return str(status)


def test_the_first_good_poll_after_a_restore_lets_the_step_finish(world: World) -> None:
    world.port.answers.append(pending())
    world.run_job()  # a success before the restore, as the restored database holds
    world.clock.advance(timedelta(minutes=1))
    runner = restore(world)
    world.clock.advance(timedelta(minutes=1))
    jobs_before = world.sql("SELECT count(*) FROM jobs")

    assert runner.check() is True  # no poll since the restore: pending, recovery stays on
    assert repoll_status(world) == "pending"
    world.run_job()  # the poll trigger's startup firing
    assert runner.check() is False

    assert recovery_on(world) is False
    assert repoll_status(world) == "done"
    assert world.sql("SELECT count(*) FROM jobs") == jobs_before  # the step enqueued nothing


def test_with_a_blank_host_the_step_stays_pending_and_recovery_stays_on(world: World) -> None:
    with world.units.open():
        Setup(world.clock).save({Key.UNSUB_WORKER_HOST: ""})  # blank: polling off
    runner = restore(world)
    world.clock.advance(timedelta(minutes=5))
    world.run_job()

    assert runner.check() is True
    assert recovery_on(world) is True
    assert repoll_status(world) == "pending"
    context = RecoveryContext("01JD3QZ8X5V9K2M7N4P6R8T0B9", T0 - timedelta(days=1), T0)
    with world.units.open():
        assert world.polls.repolled(context) is False


def test_a_failed_poll_after_a_restore_keeps_the_step_pending(world: World) -> None:
    world.port.answers.append(AdapterError(True, UNSUB_UNAVAILABLE, "unavailable"))
    runner = restore(world)
    world.clock.advance(timedelta(minutes=5))
    world.run_job()

    assert runner.check() is True
    assert repoll_status(world) == "pending"
    assert main.build_recovery_steps(repoll=world.polls.repolled).registered() == frozenset(
        {RecoveryStep.REPOLL_UNSUBSCRIBES}
    )


# The KV list count ------------------------------------------------------------------------------


def kv_calls(world: World, count: int, at: datetime) -> None:
    """Record ``count`` Worker requests at ``at``, as the adapter's meter does."""
    clock = world.meter.clock
    world.meter.clock = FrozenClock(at)
    for _ in range(count):
        world.meter.record(ApiCall("unsub_worker", "kv_list", ok=True))
    world.meter.clock = clock


def test_the_800th_kv_list_call_of_the_day_raises_one_dashboard_notice(world: World) -> None:
    kv_calls(world, 50, T0 - timedelta(days=1))  # yesterday's calls don't count
    kv_calls(world, 799, T0 - timedelta(hours=1))
    world.port.answers.append(pending(entry(world.token())))
    world.run_job()
    assert world.notices() == []

    kv_calls(world, 1, T0)
    world.run_job()
    kv_calls(world, 5, T0)
    world.run_job()

    text = "Unsubscribe Worker KV list calls today: 800 of 1,000"
    assert world.notices() == [("kv_list_quota", "kv_list:2026-11-18", text)]
    assert world.alerts() == []  # the dashboard only
    assert len(world.requests()) == 1  # the poll went on


def test_a_failed_poll_also_runs_the_kv_check(world: World) -> None:
    kv_calls(world, 1000, T0)
    world.port.answers.append(BEARER_MISSING)

    world.run_job()

    [(kind, subject, _)] = world.notices()
    assert (kind, subject) == ("kv_list_quota", "kv_list:2026-11-18")
