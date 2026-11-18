"""Job contracts (AD-10), each with a live proof.

- **One registry each.** Only ``build_job_types()`` in ``nextron/main.py`` constructs
  ``JobTypes`` or calls ``register_job_type``, and only ``build_triggers()`` there constructs
  ``Triggers`` or calls ``register_trigger``, under any alias.
- **They ship frozen.** The job types are exactly the ones the stories registered so far
  (``send_alert``, Story 1.12, ``backup_db``, Story 1.16, ``restore_test``, Story 1.18,
  ``check_recovery``, Story 1.19, ``resolve_contact_markets``, Story 3.2, ``enrich_lead``,
  ``canonicalize_lead`` and ``purge_page_text``, Story 2.5, ``render_pages``, Story 2.6,
  ``extract_lead``, Story 2.9, ``select_primary_contact``, Story 2.10, and ``run_ai_test_sets``,
  Story 4.5,
  with their settings); the triggers
  are exactly ``backup_db``'s and ``restore_test``'s daily schedules, ``check_recovery``'s
  ``Every`` with ``active`` and ``purge_page_text``'s daily ``Every`` at startup: the one-off
  ``resolve_contact_markets`` has no trigger.
- **The jobs modules' imports.** Every module under ``nextron/jobs``, nested packages and
  ``__init__.py`` files included, has a rule in ``JOBS_IMPORTS`` (a new module fails until it
  gets one) and imports only the modules its rule allows, and the standard library: nothing
  from ``nextron.ports`` or ``nextron.db``, and no third-party package but ``apscheduler``,
  which only ``scheduler.py`` imports. ``__init__.py``: nothing; ``runner.py``:
  ``nextron.app.jobqueue.api``, ``nextron.app.queries.setup`` and the domain; ``triggers.py``:
  the domain; ``scheduler.py``: ``apscheduler``, ``nextron.app.jobqueue.api``,
  ``nextron.jobs.triggers`` and the domain; ``heartbeat.py``: ``nextron.app.ops.api``;
  ``handlers.py`` and ``recovery.py``: the domain.
- **Indexes and checks.** Every table's indexes (partial ones' ``sqlite_where`` included) and
  checks are the same in the model and in the database at head. Alembic's drift comparison sees
  neither a ``WHERE`` clause nor a check, so this compares the SQL itself; ``jobs`` is the first
  table with either.

The AST checks read the source; their proofs run them on a temporary copy of the tree with a
probe module (``tests/contracts/support.py``), never on the real tree.
"""

import ast
import re
import sys
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from nextron import main
from nextron.db.models import Base
from nextron.db.models.jobqueue import JobRecord
from nextron.db.models.notify import AlertRecord, NotificationRecord
from nextron.db.models.ops import HeartbeatRecord
from nextron.domain.job import DEFERRED, InvalidJob, Job, JobGroup, JobTypes, Lane
from nextron.jobs.triggers import DailyAt, DailyRun, Every, Trigger, Triggers
from tests.contracts.support import ENGINE_ROOT, copy_with_probe

MAIN = "nextron/main.py"
JOBS_DIR = "nextron/jobs"
JOBS_INIT = "nextron/jobs/__init__.py"
RUNNER = "nextron/jobs/runner.py"
TRIGGERS_PY = "nextron/jobs/triggers.py"
SCHEDULER_PY = "nextron/jobs/scheduler.py"
HEARTBEAT_PY = "nextron/jobs/heartbeat.py"
HANDLERS_PY = "nextron/jobs/handlers.py"
RECOVERY_PY = "nextron/jobs/recovery.py"
UNUSED = '"""unused"""\n'


@dataclass(frozen=True)
class Registry:
    """A registry that only one function of ``nextron/main.py`` may build and fill."""

    builder: str  # the function in main.py
    cls: str  # the registry class
    module: str  # the module that defines it
    register: str  # its registration method


JOB_TYPES = Registry("build_job_types", "JobTypes", "nextron.domain.job", "register_job_type")
TRIGGERS = Registry("build_triggers", "Triggers", "nextron.jobs.triggers", "register_trigger")
REGISTRIES = (JOB_TYPES, TRIGGERS)

# Each module under nextron/jobs, nested ones included, by its path: (the nextron modules it may
# import, the third-party packages it may import).
type ImportRules = dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
JOBS_IMPORTS: ImportRules = {
    JOBS_INIT: ((), ()),
    RUNNER: (("nextron.app.jobqueue.api", "nextron.app.queries.setup", "nextron.domain"), ()),
    TRIGGERS_PY: (("nextron.domain",), ()),
    SCHEDULER_PY: (
        ("nextron.app.jobqueue.api", "nextron.jobs.triggers", "nextron.domain"),
        ("apscheduler",),
    ),
    HEARTBEAT_PY: (("nextron.app.ops.api",), ()),
    HANDLERS_PY: (("nextron.domain",), ()),
    RECOVERY_PY: (("nextron.domain",), ()),
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# One registry each ------------------------------------------------------------------------------


class _RegistrationChecker(ast.NodeVisitor):
    def __init__(self, label: str, tree: ast.Module, registry: Registry) -> None:
        self.label = label
        self.registry = registry
        self.problems: list[str] = []
        self.functions: list[str] = []
        self.registry_names = {registry.cls} | {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == registry.module
            for alias in node.names
            if alias.name == registry.cls
        }

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        registry = self.registry
        what = None
        if name == registry.register:
            what = f"calls {registry.register}"
        elif (isinstance(func, ast.Name) and name in self.registry_names) or (
            isinstance(func, ast.Attribute) and name == registry.cls
        ):
            what = f"constructs {registry.cls}"
        if what and (self.label, self.functions[:1]) != (MAIN, [registry.builder]):
            self.problems.append(f"{self.label}:{node.lineno} {what}")
        self.generic_visit(node)


def registration_problems(root: Path, registry: Registry) -> list[str]:
    """Each call outside ``main.<builder>`` that builds or fills ``registry``."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        label = path.relative_to(root).as_posix()
        tree = _parse(path)
        checker = _RegistrationChecker(label, tree, registry)
        checker.visit(tree)
        problems += checker.problems
    return problems


@pytest.mark.parametrize("registry", REGISTRIES, ids=lambda registry: registry.builder)
def test_only_its_builder_in_main_builds_each_registry(registry: Registry) -> None:
    assert registration_problems(ENGINE_ROOT, registry) == []
    tree = _parse(ENGINE_ROOT / MAIN)
    [build] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == registry.builder
    ]
    assert any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) == registry.cls
        for node in ast.walk(build)
    )


PROBES = [
    (JOB_TYPES, "from nextron.domain.job import JobTypes\nregistry = JobTypes()", "constructs"),
    (
        JOB_TYPES,
        "from nextron.domain.job import JobTypes as Types\nregistry = Types()",
        "constructs",
    ),
    (JOB_TYPES, "from nextron.domain import job\nregistry = job.JobTypes()", "constructs"),
    (
        JOB_TYPES,
        "import nextron.domain.job\nregistry = nextron.domain.job.JobTypes()",
        "constructs",
    ),
    (JOB_TYPES, "def wire(registry, job_type):\n    registry.register_job_type(job_type)", "calls"),
    (
        JOB_TYPES,
        "from nextron.domain.job import JobTypes\n"
        "def wire(registry, job_type):\n"
        "    JobTypes.register_job_type(registry, job_type)",
        "calls",
    ),
    (
        TRIGGERS,
        "from nextron.jobs.triggers import Triggers\nregistry = Triggers(None)",
        "constructs",
    ),
    (
        TRIGGERS,
        "from nextron.jobs.triggers import Triggers as Schedule\nregistry = Schedule(None)",
        "constructs",
    ),
    (
        TRIGGERS,
        "from nextron.jobs import triggers\nregistry = triggers.Triggers(None)",
        "constructs",
    ),
    (
        TRIGGERS,
        "import nextron.jobs.triggers\nregistry = nextron.jobs.triggers.Triggers(None)",
        "constructs",
    ),
    (
        TRIGGERS,
        "def wire(registry, trigger, every):\n    registry.register_trigger(trigger, every)",
        "calls",
    ),
    (
        TRIGGERS,
        "from nextron.jobs.triggers import Triggers\n"
        "def wire(registry, trigger, every):\n"
        "    Triggers.register_trigger(registry, trigger, every)",
        "calls",
    ),
]


@pytest.mark.parametrize(("registry", "source", "what"), PROBES)
def test_the_registry_check_catches_registration_outside_main(
    registry: Registry, source: str, what: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", source + "\n")

    [problem] = registration_problems(root, registry)

    assert problem.startswith("nextron/jobs/_lint_probe.py:")
    assert what in problem
    [other] = [found for found in REGISTRIES if found is not registry]
    assert registration_problems(root, other) == []


LATE_BUILDERS = {
    JOB_TYPES: (
        "\n\ndef late_job_types(job_type) -> JobTypes:\n"
        "    registry = JobTypes()\n"
        "    registry.register_job_type(job_type)\n"
        "    return registry\n"
    ),
    TRIGGERS: (
        "\n\ndef late_triggers(job_types, trigger, every) -> Triggers:\n"
        "    registry = Triggers(job_types)\n"
        "    registry.register_trigger(trigger, every)\n"
        "    return registry\n"
    ),
}


@pytest.mark.parametrize("registry", REGISTRIES, ids=lambda registry: registry.builder)
def test_the_registry_check_catches_another_function_in_main(
    registry: Registry, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", UNUSED)
    main_py = root / MAIN
    main_py.write_text(
        main_py.read_text(encoding="utf-8") + LATE_BUILDERS[registry], encoding="utf-8"
    )

    problems = registration_problems(root, registry)

    assert [problem.split(" ", 1)[1] for problem in problems] == [
        f"constructs {registry.cls}",
        f"calls {registry.register}",
    ]
    assert all(problem.startswith(f"{MAIN}:") for problem in problems)


class RecordingAlerts:
    """Stands in for the ``notify`` service: records each alert id it is asked to send."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, alert_id: str) -> None:
        self.sent.append(alert_id)


class RecordingBackups:
    """Stands in for the ``ops`` service's ``Backups``."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def back_up(self, kind: str) -> str:
        self.kinds.append(kind)
        return "01JD0000000000000000000002"


class RecordingRecovery:
    """Stands in for the job layer's ``RecoveryRunner``."""

    def __init__(self) -> None:
        self.checks = 0

    def check(self) -> bool:
        self.checks += 1
        return False


class RecordingRestoreTests:
    """Stands in for the ``ops`` service's ``RestoreTests``."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def run(self, kind: str) -> str | None:
        self.kinds.append(kind)
        return None


class RecordingContactMarkets:
    """Stands in for the ``research`` service's ``ContactMarkets``."""

    def __init__(self) -> None:
        self.backfills = 0

    def backfill(self) -> int:
        self.backfills += 1
        return 0


class RecordingCrawls:
    """Stands in for the ``research`` service's ``WebsiteCrawls``; ``allowed`` is what
    ``enrich_lead`` and ``render_pages`` return (``False``: the budget refused)."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, bool]] = []
        self.renders: list[tuple[str, str]] = []

    def enrich_lead(self, lead_id: str, *, may_retry: bool) -> bool:
        self.calls.append((lead_id, may_retry))
        return self.allowed

    def render_pages(self, lead_id: str, run_id: str) -> bool:
        self.renders.append((lead_id, run_id))
        return self.allowed


class RecordingDiscovery:
    """Stands in for the ``discovery`` service."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def canonicalize_lead(self, lead_id: str, run_id: str) -> str:
        self.calls.append((lead_id, run_id))
        return "kept"


class RecordingResearch:
    """Stands in for the ``research`` service."""

    def __init__(self) -> None:
        self.purges = 0

    def purge_page_text(self) -> int:
        self.purges += 1
        return 0


class RecordingExtractions:
    """Stands in for the ``research`` service's ``SignalExtractions``; ``allowed`` is what
    ``extract_lead`` returns (``False``: the budget refused)."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    def extract_lead(self, lead_id: str, run_id: str) -> bool:
        self.calls.append((lead_id, run_id))
        return self.allowed


class RecordingPrimary:
    """Stands in for the ``research`` service's ``PrimaryContacts``."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def select_primary_contact(self, lead_id: str) -> None:
        self.calls.append(lead_id)


class RecordingTestRuns:
    """Stands in for the ``playbook`` service's ``AiTestRuns``; ``allowed`` is what ``run``
    returns (``False``: the budget refused)."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, bool]] = []

    def run(self, tuple_id: str, test_set: str, *, activate: bool = True) -> bool:
        self.calls.append((tuple_id, test_set, activate))
        return self.allowed


def registry(**overrides: object) -> JobTypes:
    """``main.build_job_types`` with recording services, each overridable."""
    services: dict[str, object] = {
        "alerts": RecordingAlerts,
        "backups": RecordingBackups,
        "restore_tests": RecordingRestoreTests,
        "recovery": RecordingRecovery,
        "contact_markets": RecordingContactMarkets,
        "crawls": RecordingCrawls,
        "discovery": RecordingDiscovery,
        "research": RecordingResearch,
        "extractions": RecordingExtractions,
        "primary": RecordingPrimary,
        "test_runs": RecordingTestRuns,
    }
    services.update(overrides)
    return main.build_job_types(**services)  # type: ignore[arg-type]


def test_build_job_types_is_frozen_and_registers_the_job_types_in_order() -> None:
    alerts = RecordingAlerts()
    backups = RecordingBackups()
    restore_tests = RecordingRestoreTests()
    recovery = RecordingRecovery()
    contact_markets = RecordingContactMarkets()
    calls: list[str] = []

    def lookup() -> RecordingAlerts:
        calls.append("looked up")
        return alerts

    def backups_lookup() -> RecordingBackups:
        calls.append("backups looked up")
        return backups

    def restore_tests_lookup() -> RecordingRestoreTests:
        calls.append("restore tests looked up")
        return restore_tests

    def recovery_lookup() -> RecordingRecovery:
        calls.append("recovery looked up")
        return recovery

    def contact_markets_lookup() -> RecordingContactMarkets:
        calls.append("contact markets looked up")
        return contact_markets

    job_types = registry(
        alerts=lookup,
        backups=backups_lookup,
        restore_tests=restore_tests_lookup,
        recovery=recovery_lookup,
        contact_markets=contact_markets_lookup,
    )

    assert isinstance(job_types, JobTypes)
    assert job_types.frozen
    assert job_types.names() == (
        "send_alert",
        "backup_db",
        "restore_test",
        "check_recovery",
        "resolve_contact_markets",
        "enrich_lead",
        "canonicalize_lead",
        "render_pages",
        "purge_page_text",
        "extract_lead",
        "select_primary_contact",
        "run_ai_test_sets",
    )
    send_alert = job_types.get("send_alert")
    assert (
        send_alert.default_lane,
        send_alert.lanes,
        send_alert.priority,
        send_alert.max_runtime,
        send_alert.retry_limit,
        send_alert.group,
    ) == (Lane.REALTIME, frozenset({Lane.REALTIME}), 90, timedelta(minutes=3), 5, JobGroup.OTHER)
    assert calls == []  # building looks nothing up
    alert_id = "01JD0000000000000000000000"
    job = Job("01JD0000000000000000000001", "send_alert", Lane.REALTIME, {"alert_id": alert_id}, 1)
    assert send_alert.handler(job) is None  # type: ignore[func-returns-value]
    assert (calls, alerts.sent) == (["looked up"], [alert_id])
    backup_db = job_types.get("backup_db")
    assert (
        backup_db.default_lane,
        backup_db.lanes,
        backup_db.priority,
        backup_db.max_runtime,
        backup_db.retry_limit,
        backup_db.group,
    ) == (Lane.BULK, frozenset({Lane.BULK}), 80, timedelta(minutes=30), 2, JobGroup.BACKUP)
    tick = Job("01JD0000000000000000000003", "backup_db", Lane.BULK, {"v": 1}, 1)
    assert backup_db.handler(tick) is None  # type: ignore[func-returns-value]
    assert (calls[-1], backups.kinds) == ("backups looked up", ["daily"])
    restore_test = job_types.get("restore_test")
    assert (
        restore_test.default_lane,
        restore_test.lanes,
        restore_test.priority,
        restore_test.max_runtime,
        restore_test.retry_limit,
        restore_test.group,
    ) == (Lane.BULK, frozenset({Lane.BULK}), 70, timedelta(minutes=30), 2, JobGroup.BACKUP)
    restore_tick = Job("01JD0000000000000000000004", "restore_test", Lane.BULK, {"v": 1}, 1)
    assert restore_test.handler(restore_tick) is None  # type: ignore[func-returns-value]
    assert (calls[-1], restore_tests.kinds) == ("restore tests looked up", ["monthly"])
    check_recovery = job_types.get("check_recovery")
    assert (
        check_recovery.default_lane,
        check_recovery.lanes,
        check_recovery.priority,
        check_recovery.max_runtime,
        check_recovery.retry_limit,
        check_recovery.group,
    ) == (
        Lane.REALTIME,
        frozenset({Lane.REALTIME}),
        85,
        timedelta(minutes=10),
        2,
        JobGroup.BACKUP,
    )
    recovery_tick = Job("01JD0000000000000000000005", "check_recovery", Lane.REALTIME, {"v": 1}, 1)
    assert check_recovery.handler(recovery_tick) is None  # type: ignore[func-returns-value]
    assert (calls[-1], recovery.checks) == ("recovery looked up", 1)
    resolve = job_types.get("resolve_contact_markets")
    assert (
        resolve.default_lane,
        resolve.lanes,
        resolve.priority,
        resolve.max_runtime,
        resolve.retry_limit,
        resolve.group,
    ) == (Lane.BULK, frozenset({Lane.BULK}), 40, timedelta(minutes=30), 3, JobGroup.OTHER)
    resolve_job = Job(
        "01JD0000000000000000000006", "resolve_contact_markets", Lane.BULK, {"v": 1}, 1
    )
    assert resolve.handler(resolve_job) is None  # type: ignore[func-returns-value]
    assert (calls[-1], contact_markets.backfills) == ("contact markets looked up", 1)
    main_registry = registry(
        alerts=lookup,
        backups=backups_lookup,
        restore_tests=restore_tests_lookup,
        recovery=recovery_lookup,
        contact_markets=contact_markets_lookup,
    )
    assert main_registry is not job_types  # a new registry each time


LEAD = "01JD0000000000000000000010"
RUN = "01JD0000000000000000000011"


def test_build_job_types_registers_the_crawl_jobs_as_specified() -> None:
    job_types = registry()

    shapes = {
        name: (
            found.default_lane,
            found.lanes,
            found.priority,
            found.max_runtime,
            found.retry_limit,
            found.group,
        )
        for name in (
            "enrich_lead",
            "canonicalize_lead",
            "render_pages",
            "purge_page_text",
            "extract_lead",
            "select_primary_contact",
        )
        for found in [job_types.get(name)]
    }

    both = frozenset({Lane.BULK, Lane.INTERACTIVE})
    assert shapes == {
        "enrich_lead": (Lane.BULK, both, 40, timedelta(minutes=20), 3, JobGroup.ENRICHMENT),
        "canonicalize_lead": (Lane.BULK, both, 45, timedelta(minutes=2), 2, JobGroup.ENRICHMENT),
        "extract_lead": (Lane.BULK, both, 45, timedelta(minutes=15), 3, JobGroup.ENRICHMENT),
        "render_pages": (  # Story 2.6: bulk only, at the enrich_lead chain's priority
            Lane.BULK,
            frozenset({Lane.BULK}),
            40,
            timedelta(minutes=5),
            2,
            JobGroup.ENRICHMENT,
        ),
        "select_primary_contact": (
            Lane.BULK,
            both,
            45,
            timedelta(minutes=5),
            3,
            JobGroup.ENRICHMENT,
        ),
        "purge_page_text": (
            Lane.BULK,
            frozenset({Lane.BULK}),
            20,
            timedelta(minutes=10),
            2,
            JobGroup.OTHER,
        ),
    }


@pytest.mark.parametrize(("attempts", "may_retry"), [(1, True), (3, True), (4, False)])
def test_enrich_lead_may_retry_until_its_last_allowed_attempt(
    attempts: int, may_retry: bool
) -> None:
    crawls = RecordingCrawls()
    handler = registry(crawls=lambda: crawls).get("enrich_lead").handler

    result = handler(Job(RUN, "enrich_lead", Lane.BULK, {"v": 1, "lead_id": LEAD}, attempts))

    assert result is None
    assert crawls.calls == [(LEAD, may_retry)]


def test_enrich_lead_is_deferred_when_the_budget_refuses() -> None:
    crawls = RecordingCrawls(allowed=False)
    handler = registry(crawls=lambda: crawls).get("enrich_lead").handler

    assert handler(Job(RUN, "enrich_lead", Lane.BULK, {"v": 1, "lead_id": LEAD}, 1)) is DEFERRED


def test_canonicalize_lead_and_purge_page_text_call_their_services_once() -> None:
    discovery = RecordingDiscovery()
    research = RecordingResearch()
    job_types = registry(discovery=lambda: discovery, research=lambda: research)
    payload = {"v": 1, "lead_id": LEAD, "run_id": RUN}

    job_types.get("canonicalize_lead").handler(
        Job(RUN, "canonicalize_lead", Lane.BULK, payload, 1)
    )
    job_types.get("purge_page_text").handler(Job(RUN, "purge_page_text", Lane.BULK, {"v": 1}, 1))

    assert (discovery.calls, research.purges) == ([(LEAD, RUN)], 1)


def test_extract_lead_calls_its_service_once_and_is_deferred_when_the_budget_refuses() -> None:
    extractions = RecordingExtractions()
    refused = RecordingExtractions(allowed=False)
    payload = {"v": 1, "lead_id": LEAD, "run_id": RUN}
    job = Job(RUN, "extract_lead", Lane.BULK, payload, 1)

    assert registry(extractions=lambda: extractions).get("extract_lead").handler(job) is None
    assert registry(extractions=lambda: refused).get("extract_lead").handler(job) is DEFERRED
    assert (extractions.calls, refused.calls) == ([(LEAD, RUN)], [(LEAD, RUN)])


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 1, "lead_id": LEAD},
        {"v": 1, "run_id": RUN},
        {"v": 1, "lead_id": "not-a-ulid", "run_id": RUN},
        {"v": 1, "lead_id": LEAD, "run_id": 7},
    ],
)
def test_an_extract_lead_job_without_its_ids_is_invalid(payload: dict[str, object]) -> None:
    extractions = RecordingExtractions()
    handler = registry(extractions=lambda: extractions).get("extract_lead").handler

    with pytest.raises(InvalidJob, match="must be a ULID"):
        handler(Job(RUN, "extract_lead", Lane.BULK, payload, 1))
    assert extractions.calls == []


def test_select_primary_contact_calls_its_service_once() -> None:
    primary = RecordingPrimary()
    handler = registry(primary=lambda: primary).get("select_primary_contact").handler

    result = handler(Job(RUN, "select_primary_contact", Lane.BULK, {"v": 1, "lead_id": LEAD}, 1))

    assert result is None
    assert primary.calls == [LEAD]


@pytest.mark.parametrize("payload", [{"v": 1}, {"v": 1, "lead_id": "not-a-ulid"}])
def test_a_select_primary_contact_job_without_its_lead_is_invalid(
    payload: dict[str, object],
) -> None:
    primary = RecordingPrimary()
    handler = registry(primary=lambda: primary).get("select_primary_contact").handler

    with pytest.raises(InvalidJob, match="must be a ULID"):
        handler(Job(RUN, "select_primary_contact", Lane.BULK, payload, 1))
    assert primary.calls == []


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("enrich_lead", {"v": 1}),
        ("enrich_lead", {"v": 1, "lead_id": "not-a-ulid"}),
        ("canonicalize_lead", {"v": 1, "lead_id": LEAD}),
        ("canonicalize_lead", {"v": 1, "run_id": RUN}),
        ("render_pages", {"v": 1, "lead_id": LEAD}),
        ("render_pages", {"v": 1, "run_id": RUN}),
        ("render_pages", {"v": 1, "lead_id": LEAD, "run_id": "not-a-ulid"}),
    ],
)
def test_a_crawl_job_without_its_ids_is_invalid(name: str, payload: dict[str, object]) -> None:
    crawls, discovery = RecordingCrawls(), RecordingDiscovery()
    job_types = registry(crawls=lambda: crawls, discovery=lambda: discovery)

    with pytest.raises(InvalidJob, match="must be a ULID"):
        job_types.get(name).handler(Job(RUN, name, Lane.BULK, payload, 1))
    assert (crawls.calls, crawls.renders, discovery.calls) == ([], [], [])


@pytest.mark.parametrize(("allowed", "result"), [(True, None), (False, DEFERRED)])
def test_render_pages_calls_its_service_once_and_is_deferred_when_the_budget_refuses(
    allowed: bool, result: object
) -> None:
    crawls = RecordingCrawls(allowed=allowed)
    handler = registry(crawls=lambda: crawls).get("render_pages").handler
    payload = {"v": 1, "lead_id": LEAD, "run_id": RUN}

    assert handler(Job(RUN, "render_pages", Lane.BULK, payload, 1)) is result
    assert (crawls.renders, crawls.calls) == ([(LEAD, RUN)], [])


TUPLE = "01JD0000000000000000000012"


def test_run_ai_test_sets_is_bulk_only_and_runs_the_payloads_tuple_and_set() -> None:
    runs = RecordingTestRuns()
    found = registry(test_runs=lambda: runs).get("run_ai_test_sets")
    payload = {"v": 1, "tuple_id": TUPLE, "test_set": "extraction"}

    result = found.handler(Job(RUN, "run_ai_test_sets", Lane.BULK, payload, 1))

    assert (
        found.default_lane,
        found.lanes,
        found.priority,
        found.max_runtime,
        found.retry_limit,
        found.group,
    ) == (Lane.BULK, frozenset({Lane.BULK}), 30, timedelta(hours=2), 3, JobGroup.OTHER)
    assert result is None
    assert runs.calls == [(TUPLE, "extraction", True)]


def test_run_ai_test_sets_is_deferred_when_the_budget_refuses() -> None:
    runs = RecordingTestRuns(allowed=False)
    handler = registry(test_runs=lambda: runs).get("run_ai_test_sets").handler
    payload = {"v": 1, "tuple_id": TUPLE, "test_set": "reply"}

    assert handler(Job(RUN, "run_ai_test_sets", Lane.BULK, payload, 1)) is DEFERRED


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({"v": 1, "test_set": "extraction"}, "must be a ULID"),
        ({"v": 1, "tuple_id": "not-a-ulid", "test_set": "extraction"}, "must be a ULID"),
        ({"v": 1, "tuple_id": TUPLE}, "must be an AI Test Set"),
        ({"v": 1, "tuple_id": TUPLE, "test_set": "drafting"}, "must be an AI Test Set"),
        ({"v": 1, "tuple_id": TUPLE, "test_set": 3}, "must be an AI Test Set"),
    ],
)
def test_a_run_ai_test_sets_job_without_its_tuple_or_set_is_invalid(
    payload: dict[str, object], problem: str
) -> None:
    runs = RecordingTestRuns()
    handler = registry(test_runs=lambda: runs).get("run_ai_test_sets").handler

    with pytest.raises(InvalidJob, match=problem):
        handler(Job(RUN, "run_ai_test_sets", Lane.BULK, payload, 1))
    assert runs.calls == []


def test_build_triggers_registers_backup_restore_test_recovery_check_then_purge() -> None:
    job_types = registry()
    schedule = lambda: DailyRun(time(3, 15), "UTC")  # noqa: E731
    restore_schedule = lambda: DailyRun(time(4, 15), "UTC")  # noqa: E731
    recovery_active = lambda: True  # noqa: E731

    triggers = main.build_triggers(
        job_types,
        backup_schedule=schedule,
        restore_test_schedule=restore_schedule,
        recovery_active=recovery_active,
    )

    assert isinstance(triggers, Triggers)
    assert triggers.frozen
    [spec, restore, recovery, purge] = triggers.specs()
    assert (spec.trigger, spec.job_type) == (Trigger.BACKUP_DB, "backup_db")
    assert spec.schedule == DailyAt(schedule, fallback=DailyRun(time(10, 0), "UTC"))
    assert spec.schedule.grace == timedelta(hours=24)
    assert (restore.trigger, restore.job_type) == (Trigger.RESTORE_TEST, "restore_test")
    assert restore.schedule == DailyAt(restore_schedule, fallback=DailyRun(time(11, 0), "UTC"))
    assert (recovery.trigger, recovery.job_type) == (Trigger.CHECK_RECOVERY, "check_recovery")
    assert recovery.schedule == Every(
        timedelta(minutes=1), at_startup=True, active=recovery_active
    )
    assert (purge.trigger, purge.job_type) == (Trigger.PURGE_PAGE_TEXT, "purge_page_text")
    assert purge.schedule == Every(timedelta(days=1), at_startup=True)
    built_again = main.build_triggers(
        job_types,
        backup_schedule=schedule,
        restore_test_schedule=restore_schedule,
        recovery_active=recovery_active,
    )
    assert built_again is not triggers  # a new registry each time


# The jobs modules' imports ----------------------------------------------------------------------


def _package(module: str) -> str:
    """The dotted package a module path such as ``nextron/jobs/handlers/send.py`` is in (a
    package's own ``__init__.py`` is in the package itself)."""
    return ".".join(Path(module).parent.parts)


def _targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:  # a relative import, resolved against the module's own package
        parts = package.split(".")
        base = ".".join(parts[: len(parts) - node.level + 1])
        base += f".{node.module}" if node.module else ""
        return [f"{base}.{alias.name}" for alias in node.names]
    module = node.module or ""
    if module.startswith("nextron"):
        # ``from nextron.app.queries import setup`` imports the module nextron.app.queries.setup
        return [f"{module}.{alias.name}" for alias in node.names]
    return [module]


def _allowed(target: str, modules: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    if target == "nextron" or target.startswith("nextron."):
        return any(target == p or target.startswith(p + ".") for p in modules) or any(
            # a name imported from an allowed module, such as nextron.app.jobqueue.api.Claim
            target.rpartition(".")[0] == p for p in modules
        )
    top = target.split(".")[0]
    return top in sys.stdlib_module_names or top in packages


def import_problems(root: Path, module: str, rules: ImportRules = JOBS_IMPORTS) -> list[str]:
    """Each import in the jobs ``module`` outside its allowed modules and the standard
    library."""
    modules, packages = rules[module]
    package = _package(module)
    problems = []
    for node in ast.walk(_parse(root / module)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            problems += [
                f"{module}:{node.lineno} imports {target}"
                for target in _targets(node, package)
                if not _allowed(target, modules, packages)
            ]
    return problems


def rule_coverage(root: Path) -> tuple[list[str], list[str]]:
    """The modules under ``nextron/jobs`` (nested ones included) with no import rule, and the
    rules whose module is gone."""
    modules = {
        path.relative_to(root).as_posix() for path in (root / JOBS_DIR).rglob("*.py")
    }
    return sorted(modules - set(JOBS_IMPORTS)), sorted(set(JOBS_IMPORTS) - modules)


def test_every_jobs_module_has_its_import_rule() -> None:
    assert rule_coverage(ENGINE_ROOT) == ([], [])


def test_the_rule_coverage_check_catches_a_new_module_nested_or_not_and_a_stale_rule(
    tmp_path: Path,
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", UNUSED)
    handlers = root / JOBS_DIR / "handlers"
    handlers.mkdir()
    (handlers / "__init__.py").write_text(UNUSED, encoding="utf-8")
    (handlers / "send_due_messages.py").write_text(UNUSED, encoding="utf-8")
    (root / HEARTBEAT_PY).unlink()

    assert rule_coverage(root) == (
        [
            "nextron/jobs/_lint_probe.py",
            "nextron/jobs/handlers/__init__.py",
            "nextron/jobs/handlers/send_due_messages.py",
        ],
        [HEARTBEAT_PY],
    )


def test_a_nested_modules_relative_imports_resolve_against_its_own_package(
    tmp_path: Path,
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", UNUSED)
    nested = "nextron/jobs/handlers/send_due_messages.py"
    (root / JOBS_DIR / "handlers").mkdir()
    (root / nested).write_text(
        "from .. import scheduler\n"
        "from ..runner import Runner\n"
        "from . import tick\n"
        "from .tick import Tick\n"
        "from nextron.domain.job import Job\n",
        encoding="utf-8",
    )

    problems = import_problems(root, nested, {nested: (("nextron.domain",), ())})

    assert [problem.split(" imports ")[1] for problem in problems] == [
        "nextron.jobs.scheduler",
        "nextron.jobs.runner.Runner",
        "nextron.jobs.handlers.tick",
        "nextron.jobs.handlers.tick.Tick",
    ]


@pytest.mark.parametrize(
    ("module", "needle"),
    [
        (JOBS_INIT, '"""Delivery'),
        (RUNNER, "nextron.app.jobqueue.api"),
        (TRIGGERS_PY, "nextron.domain.job"),
        (SCHEDULER_PY, "apscheduler"),
        (HEARTBEAT_PY, "nextron.app.ops.api"),
        (HANDLERS_PY, "nextron.domain.job"),
        (RECOVERY_PY, "nextron.domain.recovery"),
    ],
)
def test_each_jobs_module_imports_only_what_it_may(module: str, needle: str) -> None:
    assert import_problems(ENGINE_ROOT, module) == []
    assert needle in (ENGINE_ROOT / module).read_text(encoding="utf-8")


def _with_line(tmp_path: Path, module: str, line: str) -> Path:
    """A copy of the tree with ``line`` appended to ``module``; the copy's root."""
    _, root = copy_with_probe(tmp_path, "nextron.jobs", UNUSED)
    path = root / module
    path.write_text(path.read_text(encoding="utf-8") + f"\n{line}\n", encoding="utf-8")
    return root


FORBIDDEN_IMPORTS = [
    (RUNNER, "from nextron.ports.errors import AdapterError", "nextron.ports.errors.AdapterError"),
    (RUNNER, "import nextron.ports.clock", "nextron.ports.clock"),
    (RUNNER, "from nextron.db import uow", "nextron.db.uow"),
    (RUNNER, "from nextron.db.uow import current", "nextron.db.uow.current"),
    (RUNNER, "from nextron.app.setup import api", "nextron.app.setup.api"),
    (RUNNER, "from nextron.app.queries import jobqueue", "nextron.app.queries.jobqueue"),
    (
        RUNNER,
        "from nextron.adapters.clock import SystemClock",
        "nextron.adapters.clock.SystemClock",
    ),
    (RUNNER, "import sqlalchemy", "sqlalchemy"),
    (
        RUNNER,
        "from apscheduler.schedulers.asyncio import AsyncIOScheduler",
        "apscheduler.schedulers.asyncio",
    ),
    (TRIGGERS_PY, "import apscheduler", "apscheduler"),
    (TRIGGERS_PY, "from nextron.ports.clock import Clock", "nextron.ports.clock.Clock"),
    (TRIGGERS_PY, "from nextron.db.uow import current", "nextron.db.uow.current"),
    (
        TRIGGERS_PY,
        "from nextron.app.jobqueue.api import JobQueue",
        "nextron.app.jobqueue.api.JobQueue",
    ),
    (
        SCHEDULER_PY,
        "from nextron.ports.errors import AdapterError",
        "nextron.ports.errors.AdapterError",
    ),
    (SCHEDULER_PY, "from nextron.db import uow", "nextron.db.uow"),
    (SCHEDULER_PY, "from nextron.app.ops.api import Heartbeat", "nextron.app.ops.api.Heartbeat"),
    (SCHEDULER_PY, "from nextron.jobs import runner", "nextron.jobs.runner"),
    (SCHEDULER_PY, "import sqlalchemy", "sqlalchemy"),
    (
        HEARTBEAT_PY,
        "from apscheduler.schedulers.asyncio import AsyncIOScheduler",
        "apscheduler.schedulers.asyncio",
    ),
    (HEARTBEAT_PY, "from nextron.domain.job import Lane", "nextron.domain.job.Lane"),
    (HEARTBEAT_PY, "from nextron.db.uow import current", "nextron.db.uow.current"),
    (HEARTBEAT_PY, "from nextron.ports.clock import Clock", "nextron.ports.clock.Clock"),
    (HEARTBEAT_PY, "from nextron.jobs.runner import Runner", "nextron.jobs.runner.Runner"),
    (HEARTBEAT_PY, "from . import scheduler", "nextron.jobs.scheduler"),
    (HANDLERS_PY, "from nextron.app.notify.api import Notify", "nextron.app.notify.api.Notify"),
    (HANDLERS_PY, "from nextron.app.queries import notify", "nextron.app.queries.notify"),
    (HANDLERS_PY, "from nextron.ports.alert import AlertPort", "nextron.ports.alert.AlertPort"),
    (HANDLERS_PY, "from nextron.db.uow import current", "nextron.db.uow.current"),
    (HANDLERS_PY, "from . import runner", "nextron.jobs.runner"),
    (HANDLERS_PY, "import httpx2", "httpx2"),
]


@pytest.mark.parametrize(("module", "line", "target"), FORBIDDEN_IMPORTS)
def test_the_import_check_catches_any_other_import(
    module: str, line: str, target: str, tmp_path: Path
) -> None:
    root = _with_line(tmp_path, module, line)

    [problem] = import_problems(root, module)

    assert problem.startswith(f"{module}:")
    assert problem.endswith(f"imports {target}")


@pytest.mark.parametrize(
    ("module", "line"),
    [
        (RUNNER, "from nextron.app.queries import setup as settings_reader"),
        (RUNNER, "from nextron.domain.ids import is_ulid"),
        (RUNNER, "import nextron.domain.job"),
        (RUNNER, "from collections import deque"),
        (TRIGGERS_PY, "from nextron.domain.ids import is_ulid"),
        (TRIGGERS_PY, "import re"),
        (SCHEDULER_PY, "from apscheduler.triggers.cron import CronTrigger"),
        (SCHEDULER_PY, "from nextron.jobs import triggers"),
        (SCHEDULER_PY, "from .triggers import Every"),
        (SCHEDULER_PY, "from nextron.domain.job import Lane"),
        (HEARTBEAT_PY, "from nextron.app.ops import api"),
        (HEARTBEAT_PY, "import time"),
        (HANDLERS_PY, "from nextron.domain.alert import SEND_ALERT_JOB"),
        (HANDLERS_PY, "import functools"),
    ],
)
def test_the_import_check_allows_each_modules_own_imports(
    module: str, line: str, tmp_path: Path
) -> None:
    assert import_problems(_with_line(tmp_path, module, line), module) == []


# Indexes and checks -----------------------------------------------------------------------------

_CHECK = re.compile(r"CONSTRAINT (ck_\w+) CHECK \((.*)\),?$")


def _squeeze(sql: str) -> str:
    return " ".join(sql.split())


def _checks(table_sql: str) -> dict[str, str]:
    return {
        match[1]: match[2]
        for line in table_sql.splitlines()
        if (match := _CHECK.match(line.strip()))
    }


def _model_schema(table: Table) -> tuple[dict[str, str], dict[str, str]]:
    dialect = sqlite.dialect()
    indexes = {
        str(index.name): _squeeze(str(CreateIndex(index).compile(dialect=dialect)))
        for index in table.indexes
    }
    return indexes, _checks(str(CreateTable(table).compile(dialect=dialect)))


def _stored_schema(engine: Engine, name: str) -> tuple[dict[str, str], dict[str, str]]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL",
            (name,),
        ).all()
    indexes = {index: _squeeze(sql) for kind, index, sql in rows if kind == "index"}
    tables = [sql for kind, _, sql in rows if kind == "table"]
    return indexes, _checks("\n".join(tables))


def schema_differences(engine: Engine) -> list[str]:
    """Each index or check of a model table that differs between the model and ``engine``."""
    problems = []
    for table in Base.metadata.sorted_tables:
        model_indexes, model_checks = _model_schema(table)
        stored_indexes, stored_checks = _stored_schema(engine, table.name)
        for kind, model, stored in (
            ("index", model_indexes, stored_indexes),
            ("check", model_checks, stored_checks),
        ):
            for name in sorted(model.keys() | stored.keys()):
                if model.get(name) != stored.get(name):
                    problems.append(
                        f"{table.name}: {kind} {name}: model {model.get(name)!r}, "
                        f"database {stored.get(name)!r}"
                    )
    return problems


def test_every_tables_indexes_and_checks_match_the_database_at_head(db_engine: Engine) -> None:
    assert schema_differences(db_engine) == []
    assert {table.name for table in Base.metadata.sorted_tables} >= {
        "events",
        "settings",
        "jobs",
        "runner_heartbeat",
        "alerts",
        "notifications",
    }


def test_the_notify_indexes_and_checks_are_the_specified_ones() -> None:
    alert_indexes, alert_checks = _model_schema(AlertRecord.__table__)  # type: ignore[arg-type]
    notice_indexes, notice_checks = _model_schema(
        NotificationRecord.__table__  # type: ignore[arg-type]
    )

    assert alert_indexes == {}
    assert alert_checks == {"ck_alerts_status": "status IN ('pending','sent','unknown','failed')"}
    assert notice_indexes == {
        "ix_notifications_open": (
            "CREATE INDEX ix_notifications_open ON notifications (dismissed_at, created_at)"
        )
    }
    assert notice_checks == {}
    unique = {
        table: {
            str(constraint.name): tuple(column.name for column in constraint.columns)
            for constraint in record.__table__.constraints  # type: ignore[attr-defined]
            if type(constraint).__name__ == "UniqueConstraint"
        }
        for table, record in (("alerts", AlertRecord), ("notifications", NotificationRecord))
    }
    assert unique == {
        "alerts": {"uq_alerts_kind_subject_channel": ("kind", "subject", "channel")},
        "notifications": {"uq_notifications_kind_subject": ("kind", "subject")},
    }


def test_the_heartbeat_checks_are_the_specified_ones() -> None:
    indexes, checks = _model_schema(HeartbeatRecord.__table__)  # type: ignore[arg-type]

    assert indexes == {}
    assert checks == {
        "ck_runner_heartbeat_singleton": "id = '00000000000000000000000000'",
        "ck_runner_heartbeat_pid": "pid > 0",
    }


def test_the_jobs_partial_indexes_and_checks_are_the_specified_ones() -> None:
    indexes, checks = _model_schema(JobRecord.__table__)  # type: ignore[arg-type]

    assert indexes["uq_jobs_live_key"].endswith("WHERE status IN ('queued','running')")
    assert indexes["ix_jobs_running_concurrency"].endswith("WHERE status = 'running'")
    assert set(checks) == {
        "ck_jobs_status",
        "ck_jobs_lane",
        "ck_jobs_attempts",
        "ck_jobs_priority",
        "ck_jobs_lease",
    }


@pytest.mark.parametrize(
    ("statements", "named"),
    [
        (
            [
                "DROP INDEX uq_jobs_live_key",
                "CREATE UNIQUE INDEX uq_jobs_live_key ON jobs (idempotency_key)",
            ],
            "jobs: index uq_jobs_live_key",
        ),
        (
            [
                "DROP INDEX ix_jobs_running_concurrency",
                "CREATE INDEX ix_jobs_running_concurrency ON jobs (concurrency_key) "
                "WHERE status = 'queued'",
            ],
            "jobs: index ix_jobs_running_concurrency",
        ),
        (["DROP INDEX ix_jobs_lease"], "jobs: index ix_jobs_lease"),
        (["DROP INDEX ix_events_at"], "events: index ix_events_at"),
        (
            ["CREATE INDEX ix_settings_updated_at ON settings (updated_at) WHERE value = ''"],
            "settings: index ix_settings_updated_at",
        ),
    ],
)
def test_the_schema_check_catches_an_index_the_model_does_not_declare(
    statements: list[str], named: str, db_engine: Engine
) -> None:
    with db_engine.connect() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.commit()

    [problem] = schema_differences(db_engine)

    assert problem.startswith(named)


def test_the_schema_check_catches_a_changed_check(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        connection.exec_driver_sql("ALTER TABLE jobs RENAME TO jobs_old")
        [table_sql] = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'jobs_old'"
        ).one()
        connection.exec_driver_sql(
            table_sql.replace("jobs_old", "jobs", 1).replace("attempts >= 0", "attempts >= 1")
        )
        connection.commit()

    problems = schema_differences(db_engine)

    assert any(problem.startswith("jobs: check ck_jobs_attempts") for problem in problems)
