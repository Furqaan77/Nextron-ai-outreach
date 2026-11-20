"""Read API of the ``playbook`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.playbook`` only. All read through the
current unit of work, read-only ones included.

- ``active_tuple(route) -> RouteTuple | None`` (FR-71, AD-8, Story 2.8): the tuple ``route``
  runs. It reads the route's active activation and the prompt snapshot it names, and builds the
  ``RouteTuple`` from the activation's model, effort, prompt version and schema version, with
  ``system_prompt`` set to the **snapshot's** text, so the gateway never sees the live prompt
  file. It raises ``ValueError`` when the snapshot is missing or its text's SHA-256 isn't the
  activation's ``prompt_sha256`` (a database edited by hand), and ``TypeError`` for a route that
  isn't a ``str``. It returns ``None`` when the route has no active activation (an unknown route
  included): the caller skips the call and records why. Stories 2.9, 2.10 and 2.16 call it
  before every extraction, and record ``tuple.actor`` (``ai:<route>@<prompt_version>``) on what
  they store. When the activation names a Sales Playbook snapshot (``playbook_sha256``, Story
  4.4), the tuple's ``playbook`` is that **snapshot** (``PlaybookSnapshot``), never the live
  file; a missing snapshot, or content whose SHA-256 isn't ``playbook_sha256``, raises
  ``ValueError`` naming the route only.

The Sales Playbook and the candidate tuples (FR-71, AD-8, NFR-25, Story 4.4):

- ``tuple_by_key(route, key) -> RouteTuple | None`` (Story 4.17): the route's tuple whose
  ``RouteTuple.key`` is ``key``: the active one, else a stored tuple row's; ``None`` for none.
- ``route_tuples() -> tuple[RouteTuples, ...]``: by route name, every route with a tuple row
  (``ai_route_tuples``) or an active activation. ``active`` is an ``ActiveTuple`` (the
  activation's model, effort, prompt and schema versions, its ``PlaybookRef`` or ``None``,
  ``activated_at``, ``bootstrap`` and ``actor``) or ``None``; ``candidates`` lists the route's
  tuple rows that aren't the active tuple, newest first, each a ``CandidateTuple`` (its id,
  fields, ``PlaybookRef`` or ``None``, and ``created_at``). The AI content view shows it.
- ``playbook_status() -> PlaybookStatus``: the newest Playbook check (``LastCheck``:
  ``checked_at``, ``trigger``, ``problem`` or ``None``) and the snapshot of the newest check
  that read one (``PlaybookVersion``: version, SHA-256, ``captured_at``), each ``None`` when
  there is none.
- ``health_issues()``: when the newest check recorded a problem, one
  ``HealthIssue("playbook_invalid", "playbook", "Sales Playbook: " + problem_text(problem),
  detail=FIX_PLAYBOOK)`` (how to clear it); otherwise none (``nextron.main.build_health_issues``
  registers it after the backups).

``PlaybookRef(version, sha256)`` names a stored Playbook snapshot; ``short`` is its first 12
hex digits.

The AI test gate (FR-73, AD-12, NFR-26, UX-DR30, Story 4.5):

- ``active_tuple_id(route) -> str | None`` (Story 4.14): the id of the tuple row whose fields
  are the route's active activation's (what a grounding result stores as ``route_tuple_id``), or
  ``None`` when the route has no active activation or no such row (a bootstrap activation
  recorded before its row); ``TypeError`` for a route that isn't a ``str``.
- ``tuple_of(tuple_id) -> RouteTuple | None``: the ``RouteTuple`` of the stored tuple row
  ``tuple_id`` (``None`` for an unknown id, a text that isn't a ULID included), built from its
  prompt snapshot and Playbook snapshot and checked exactly as ``active_tuple`` checks an
  activation's (``ValueError`` naming the route only). ``AiTestRuns.run`` calls it.
- ``route_tuples()`` also gives each ``ActiveTuple`` its ``tuple_id`` (the tuple row with the
  activation's fields, or ``None``), ``ungated`` (the activation is a bootstrap one: it serves
  without a test run) and ``run``, and each ``CandidateTuple`` its ``run`` and ``passing`` (it
  has a ``passed`` run on its route's set, so it can be rolled back to). ``run`` is the newest
  ``RunSummary`` of the tuple row on its route's set (``ROUTE_SETS``), or ``None``: the run's
  id, set, outcome, error, case and passed counts, metrics, the ``CaseResult``s of its failing
  cases, cost and ``finished_at``.
- **The case-set binding** (Story 4.6). ``case_set_fingerprint(test_set, route)`` is
  ``nextron.domain.ai_test_cases.case_set_sha256`` over the SHA-256s of the live cases of that
  set and route (both origins: fixtures and Operator overrides): the set a run must have
  measured to count. A ``RunSummary``'s ``current`` is whether its run measured that live set
  (a run stored before Story 4.6 never did), and a ``CandidateTuple``'s ``passing`` means a
  ``passed`` run on the current set, exactly what ``Playbook.activate`` accepts, so a rollback
  is offered only where it would succeed.
- ``health_issues()`` also reports, per route (by name) whose active tuple's newest run on its
  set failed, ``HealthIssue("ai_tuple_failed", <route>, "<route>: active tuple failed its test
  set", detail=TUPLE_FAILED_DETAIL)``, until a passing tuple replaces it (or the active tuple's
  newest run passes). The detail says the tuple keeps serving until a tuple passes its test set
  and is activated, and that the AI content page shows the run's metrics, its failing cases or
  why it failed (a ``set_too_small`` or ``unknown_route`` run has no failing case).

The Approved Company Facts (FR-72, Story 4.3). A fact's versions are append-only; its *current*
version is its highest version that isn't retracted, or none (a retraction retracts every
version, so a retracted fact has none until the Operator edits it again).

- ``company_facts()``: every fact as a ``FactHistory(fact_id, current, versions)``, by
  ``fact_id``. ``versions`` lists every version, newest first; ``current`` is a ``FactVersion``
  or ``None``. The Approved Company Facts page reads it.
- ``fact(fact_id)``: one ``FactHistory``, or ``None`` when the fact has no version.
- ``allowed_facts(segment_id, market_id)``: the current version of every fact whose allowed
  Segments hold ``segment_id`` and whose allowed Markets hold ``market_id`` (``segments.id`` and
  ``markets.id`` ULIDs), by ``fact_id``. A retracted fact is never returned. Drafting (Story
  4.15) and grounding (4.13, 4.14) read the facts a Draft may cite only through it, so ABA
  Formal Opinion 512 reaches only US Drafts and the SRA notice only UK Drafts.

``FactVersion(id, fact_id, version, content, created_by, created_at, retracted_at)`` is one
version: its row ``id``, its ``nextron.domain.company_facts.FactContent`` (with ULID references),
who made it (``FactCreator``), when, and when it was retracted (``None`` if it wasn't). A stored
row that no longer validates raises ``InvalidCompanyFacts``.

The subject access exporter (Story 3.15, ``nextron.domain.contact_export``):
``export_pii(contact_id) -> {"override_cases": tuple[dict, ...]}``, every column of each of
the Contact's override cases (Story 4.6: an Operator's pseudonymised Reply, live or
retired; ``[erased]`` once the Contact is erased), read with a generic select, by
``loaded_at``, then ``id``. A ``contact_id`` that isn't a ULID has none. A fixture case is
synthetic and never about a Contact.

``override_case_counts(contact_id) -> (cases, erased)`` (the merge of Stories 3.14 and 4.6):
the number of the Contact's override cases, live or retired, and how many of them hold
``[erased]``; ``(0, 0)`` for a ``contact_id`` that isn't a ULID. The erasure's AI Test Sets
store reads it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from typing import Final

from nextron.db.repos.playbook import activations as activations_repo
from nextron.db.repos.playbook import company_facts as company_facts_repo
from nextron.db.repos.playbook import playbook_checks as checks_repo
from nextron.db.repos.playbook import playbook_snapshots as playbook_snapshots_repo
from nextron.db.repos.playbook import route_tuples as tuples_repo
from nextron.db.repos.playbook import snapshots as snapshots_repo
from nextron.db.repos.playbook import test_cases as cases_repo
from nextron.db.repos.playbook import test_runs as runs_repo
from nextron.db.repos.playbook.activations import StoredActivation
from nextron.db.repos.playbook.company_facts import StoredFact
from nextron.db.repos.playbook.route_tuples import StoredTuple
from nextron.db.repos.playbook.test_runs import StoredRun
from nextron.domain.ai import Effort, RouteTuple
from nextron.domain.ai_test_cases import case_set_sha256
from nextron.domain.ai_test_sets import case_route
from nextron.domain.ai_test_gate import (
    ROUTE_SETS,
    CaseResult,
    Metric,
    RunError,
    RunOutcome,
    TestSet,
    metrics_from_json,
    results_from_json,
)
from nextron.domain.ids import is_ulid
from nextron.domain.company_facts import FactContent, FactCreator
from nextron.domain.health import HealthIssue
from nextron.domain.playbook import (
    SHORT_SHA_LENGTH,
    PlaybookProblem,
    PlaybookSnapshot,
    SyncTrigger,
    problem_text,
)

__all__ = [
    "ActiveTuple",
    "CandidateTuple",
    "FactHistory",
    "FactVersion",
    "LastCheck",
    "PlaybookRef",
    "PlaybookStatus",
    "PlaybookVersion",
    "RouteTuples",
    "RunSummary",
    "active_tuple",
    "active_tuple_id",
    "allowed_facts",
    "case_set_fingerprint",
    "company_facts",
    "export_pii",
    "fact",
    "health_issues",
    "override_case_counts",
    "playbook_status",
    "route_tuples",
    "tuple_of",
]

PLAYBOOK_INVALID: Final = "playbook_invalid"
PLAYBOOK_SUBJECT: Final = "playbook"
ISSUE_LEAD: Final = "Sales Playbook: "
FIX_PLAYBOOK: Final = (
    "Fix the file, then use Check for changes on the AI content page (/ai) or restart the engine."
)
TUPLE_FAILED: Final = "ai_tuple_failed"
TUPLE_FAILED_TEXT: Final = "{route}: active tuple failed its test set"
TUPLE_FAILED_DETAIL: Final = (
    "It keeps serving until a tuple passes its test set and is activated. The AI content page "
    "(/ai) shows the run's metrics, its failing cases or why it failed."
)


def active_tuple(route: str) -> RouteTuple | None:
    """The pinned tuple of ``route``, with the snapshot's text, or ``None``."""
    if not isinstance(route, str):
        raise TypeError("route must be a str")
    activation = activations_repo.active(route)
    if activation is None:
        return None
    return _built(activation, "active tuple")


def active_tuple_id(route: str) -> str | None:
    """The id of the tuple row (``ai_route_tuples``) with ``route``'s active activation's
    fields, or ``None`` when the route has no active activation or no such row; see the module
    docstring."""
    if not isinstance(route, str):
        raise TypeError("route must be a str")
    activation = activations_repo.active(route)
    if activation is None:
        return None
    found = _active_row(activation, _rows_by_route().get(route, []))
    return None if found is None else found.id


def case_set_fingerprint(test_set: TestSet, route: str) -> str:
    """The fingerprint of the live cases of ``test_set`` and ``route``'s case route
    (``case_route``); see the module docstring."""
    if not isinstance(test_set, TestSet):
        raise TypeError("test_set must be a TestSet")
    return case_set_sha256(cases_repo.live_sha256s(test_set.value, case_route(route)))


def tuple_of(tuple_id: str) -> RouteTuple | None:
    """The ``RouteTuple`` of the stored tuple row ``tuple_id``, checked as ``active_tuple``
    checks an activation's, or ``None`` when there is no such row."""
    if not is_ulid(tuple_id):
        return None
    row = tuples_repo.get(tuple_id)
    return None if row is None else _built(row, "tuple")


def tuple_by_key(route: str, key: str) -> RouteTuple | None:
    """The ``RouteTuple`` of ``route`` whose ``key`` is ``key`` (Story 4.17: a Message Batch's
    tuple, by the key its row stores): the active tuple when its key matches, else the newest
    stored tuple row that builds with that key, else ``None``. A row whose snapshot is missing
    or doesn't match is passed over."""
    if not isinstance(route, str) or not isinstance(key, str):
        raise TypeError("route and key must be str")
    active = active_tuple(route)
    if active is not None and active.key == key:
        return active
    for row in _rows_by_route().get(route, []):
        try:
            built = _built(row, "tuple")
        except ValueError:
            continue
        if built.key == key:
            return built
    return None


def _built(row: StoredActivation | StoredTuple, what: str) -> RouteTuple:
    """The ``RouteTuple`` of an activation or a tuple row, with the snapshots' text, checked
    against the row's SHA-256s (``ValueError`` naming the route only)."""
    where = f"{row.route} {row.prompt_version}"
    snapshot = snapshots_repo.get(row.route, row.prompt_version)
    if snapshot is None:
        raise ValueError(f"the {what} of {where} names a prompt snapshot that isn't stored")
    route_tuple = RouteTuple(
        route=row.route,
        model=row.model,
        effort=Effort(row.effort),
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        system_prompt=snapshot.text,
        playbook=_pinned_playbook(row, what),
    )
    if not route_tuple.prompt_sha256 == snapshot.sha256 == row.prompt_sha256:
        raise ValueError(f"the stored prompt of {where} doesn't match its SHA-256")
    return route_tuple


def _pinned_playbook(row: StoredActivation | StoredTuple, what: str) -> PlaybookSnapshot | None:
    """The Playbook snapshot the row names, checked against its SHA-256, or ``None``."""
    if row.playbook_sha256 is None:
        return None
    stored = playbook_snapshots_repo.get(row.playbook_sha256)
    if stored is None:
        raise ValueError(f"the {what} of {row.route} names a Playbook snapshot that isn't stored")
    pinned = PlaybookSnapshot(stored.version, stored.content)
    if not pinned.sha256 == stored.sha256 == row.playbook_sha256:
        raise ValueError(f"the stored Playbook of {row.route} doesn't match its SHA-256")
    return pinned


@dataclass(frozen=True, slots=True)
class PlaybookRef:
    """A stored Sales Playbook snapshot: its version and SHA-256."""

    version: str
    sha256: str

    @property
    def short(self) -> str:
        """The first 12 hex digits of ``sha256``."""
        return self.sha256[:SHORT_SHA_LENGTH]


@dataclass(frozen=True, slots=True)
class RunSummary:
    """A tuple's test run as the AI content view shows it: ``failing`` holds the
    ``CaseResult`` of each case that failed, in case order, and ``current`` is whether the run
    measured the live case set (Story 4.6)."""

    id: str
    test_set: TestSet
    outcome: RunOutcome
    error: RunError | None
    case_count: int
    passed_count: int
    metrics: tuple[Metric, ...]
    failing: tuple[CaseResult, ...]
    cost_micros: int
    finished_at: datetime
    current: bool


@dataclass(frozen=True, slots=True)
class ActiveTuple:
    """The tuple a route runs: its active activation, the tuple row with its fields
    (``tuple_id``, or ``None``), whether it serves without a test run (``ungated``) and that
    row's newest run on the route's set."""

    model: str
    effort: Effort
    prompt_version: str
    schema_version: int
    playbook: PlaybookRef | None
    activated_at: datetime
    bootstrap: bool
    actor: str
    tuple_id: str | None
    ungated: bool
    run: RunSummary | None


@dataclass(frozen=True, slots=True)
class CandidateTuple:
    """A stored tuple of a route that isn't its active one, its newest run on the route's set
    and whether it has a passing run on the current case set (``passing``: a rollback
    target)."""

    id: str
    model: str
    effort: Effort
    prompt_version: str
    schema_version: int
    playbook: PlaybookRef | None
    created_at: datetime
    run: RunSummary | None
    passing: bool


@dataclass(frozen=True, slots=True)
class RouteTuples:
    """A route's active tuple (``None`` when it has none) and its candidates, newest first."""

    route: str
    active: ActiveTuple | None
    candidates: tuple[CandidateTuple, ...]


@dataclass(frozen=True, slots=True)
class LastCheck:
    """The newest read of the Sales Playbook file: when, why, and its problem or ``None``."""

    checked_at: datetime
    trigger: SyncTrigger
    problem: PlaybookProblem | None


@dataclass(frozen=True, slots=True)
class PlaybookVersion:
    """The Sales Playbook snapshot the newest successful check read."""

    version: str
    sha256: str
    captured_at: datetime

    @property
    def short(self) -> str:
        """The first 12 hex digits of ``sha256``."""
        return self.sha256[:SHORT_SHA_LENGTH]


@dataclass(frozen=True, slots=True)
class PlaybookStatus:
    """The newest check and the snapshot of the newest successful one, each possibly
    ``None``."""

    last_check: LastCheck | None
    current: PlaybookVersion | None


def _ref(route: str, sha256: str | None, versions: dict[str, str]) -> PlaybookRef | None:
    if sha256 is None:
        return None
    if sha256 not in versions:
        stored = playbook_snapshots_repo.get(sha256)
        if stored is None:
            raise ValueError(f"a tuple of {route} names a Playbook snapshot that isn't stored")
        versions[sha256] = stored.version
    return PlaybookRef(versions[sha256], sha256)


def _identity(row: StoredActivation | StoredTuple) -> tuple[str, str, str, int, str | None]:
    """What makes a tuple distinct: the fields of the tuples' unique indexes but the route."""
    return (row.model, row.effort, row.prompt_version, row.schema_version, row.playbook_sha256)


class _Runs:
    """Every stored run, read once: the newest of each tuple on each set, which tuples passed
    on which case set, and each route's current case-set fingerprint (read once each)."""

    def __init__(self) -> None:
        self.newest: dict[tuple[str, str], StoredRun] = {}
        self.passed: set[tuple[str, str, str | None]] = set()
        self._fingerprints: dict[str, str] = {}
        for run in runs_repo.every():  # by tuple and set, newest first
            key = (run.tuple_id, run.test_set)
            self.newest.setdefault(key, run)
            if run.outcome == RunOutcome.PASSED.value:
                self.passed.add((*key, run.cases_sha256))

    def fingerprint(self, route: str, test_set: TestSet) -> str:
        """The current case-set fingerprint of ``route``."""
        if route not in self._fingerprints:
            self._fingerprints[route] = case_set_fingerprint(test_set, route)
        return self._fingerprints[route]

    def summary(self, route: str, tuple_id: str | None) -> RunSummary | None:
        """The newest run of ``tuple_id`` on ``route``'s set, or ``None``."""
        test_set = ROUTE_SETS.get(route)
        if tuple_id is None or test_set is None:
            return None
        run = self.newest.get((tuple_id, test_set.value))
        if run is None:
            return None
        return _summary(run, current=run.cases_sha256 == self.fingerprint(route, test_set))

    def passing(self, route: str, tuple_id: str) -> bool:
        """Whether ``tuple_id`` has a ``passed`` run on ``route``'s current case set."""
        test_set = ROUTE_SETS.get(route)
        if test_set is None:
            return False
        return (tuple_id, test_set.value, self.fingerprint(route, test_set)) in self.passed


def _summary(run: StoredRun, *, current: bool) -> RunSummary:
    return RunSummary(
        id=run.id,
        test_set=TestSet(run.test_set),
        outcome=RunOutcome(run.outcome),
        error=None if run.error is None else RunError(run.error),
        case_count=run.case_count,
        passed_count=run.passed_count,
        metrics=metrics_from_json(run.metrics),
        failing=tuple(result for result in results_from_json(run.results) if not result.passed),
        cost_micros=run.cost_micros,
        finished_at=run.finished_at,
        current=current,
    )


def _active(
    activation: StoredActivation, tuple_id: str | None, runs: _Runs, versions: dict[str, str]
) -> ActiveTuple:
    return ActiveTuple(
        model=activation.model,
        effort=Effort(activation.effort),
        prompt_version=activation.prompt_version,
        schema_version=activation.schema_version,
        playbook=_ref(activation.route, activation.playbook_sha256, versions),
        activated_at=activation.activated_at,
        bootstrap=activation.bootstrap,
        actor=activation.actor,
        tuple_id=tuple_id,
        ungated=activation.bootstrap,
        run=runs.summary(activation.route, tuple_id),
    )


def _candidate(row: StoredTuple, runs: _Runs, versions: dict[str, str]) -> CandidateTuple:
    return CandidateTuple(
        id=row.id,
        model=row.model,
        effort=Effort(row.effort),
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        playbook=_ref(row.route, row.playbook_sha256, versions),
        created_at=row.created_at,
        run=runs.summary(row.route, row.id),
        passing=runs.passing(row.route, row.id),
    )


def _rows_by_route() -> dict[str, list[StoredTuple]]:
    rows: dict[str, list[StoredTuple]] = {}
    for row in tuples_repo.every():  # by route, newest first
        rows.setdefault(row.route, []).append(row)
    return rows


def _active_row(activation: StoredActivation, rows: list[StoredTuple]) -> StoredTuple | None:
    """The tuple row with the activation's fields, or ``None``."""
    identity = _identity(activation)
    return next((row for row in rows if _identity(row) == identity), None)


def route_tuples() -> tuple[RouteTuples, ...]:
    """Every route's active tuple and candidates, by route name; see the module docstring."""
    versions: dict[str, str] = {}  # Playbook SHA-256 -> version, read once each
    actives = {activation.route: activation for activation in activations_repo.all_active()}
    rows = _rows_by_route()
    runs = _Runs()
    found: list[RouteTuples] = []
    for route in sorted(actives.keys() | rows.keys()):
        activation = actives.get(route)
        active_identity = None if activation is None else _identity(activation)
        candidates = tuple(
            _candidate(row, runs, versions)
            for row in rows.get(route, [])
            if _identity(row) != active_identity
        )
        active = None
        if activation is not None:
            matching = _active_row(activation, rows.get(route, []))
            tuple_id = None if matching is None else matching.id
            active = _active(activation, tuple_id, runs, versions)
        found.append(RouteTuples(route, active, candidates))
    return tuple(found)


def playbook_status() -> PlaybookStatus:
    """The newest Playbook check and the snapshot of the newest successful one."""
    newest = checks_repo.newest()
    last_check = (
        None
        if newest is None
        else LastCheck(
            checked_at=newest.checked_at,
            trigger=SyncTrigger(newest.trigger),
            problem=None if newest.problem is None else PlaybookProblem(newest.problem),
        )
    )
    read = checks_repo.newest_read()
    current = None
    if read is not None and read.playbook_sha256 is not None:
        stored = playbook_snapshots_repo.get(read.playbook_sha256)
        if stored is not None:  # the foreign key keeps it
            current = PlaybookVersion(stored.version, stored.sha256, stored.captured_at)
    return PlaybookStatus(last_check, current)


def health_issues() -> tuple[HealthIssue, ...]:
    """``playbook_invalid`` when the newest Playbook check recorded a problem, then
    ``ai_tuple_failed`` for each route whose active tuple's newest run failed; see the module
    docstring."""
    issues: list[HealthIssue] = []
    newest = checks_repo.newest()
    if newest is not None and newest.problem is not None:
        text = ISSUE_LEAD + problem_text(PlaybookProblem(newest.problem))
        issues.append(HealthIssue(PLAYBOOK_INVALID, PLAYBOOK_SUBJECT, text, detail=FIX_PLAYBOOK))
    rows = _rows_by_route()
    for activation in activations_repo.all_active():  # by route
        test_set = ROUTE_SETS.get(activation.route)
        matching = _active_row(activation, rows.get(activation.route, []))
        if test_set is None or matching is None:
            continue
        run = runs_repo.newest(matching.id, test_set.value)
        if run is not None and run.outcome == RunOutcome.FAILED.value:
            issues.append(
                HealthIssue(
                    TUPLE_FAILED,
                    activation.route,
                    TUPLE_FAILED_TEXT.format(route=activation.route),
                    detail=TUPLE_FAILED_DETAIL,
                )
            )
    return tuple(issues)


@dataclass(frozen=True, slots=True)
class FactVersion:
    """One version of an Approved Company Fact."""

    id: str
    fact_id: str
    version: int
    content: FactContent
    created_by: FactCreator
    created_at: datetime
    retracted_at: datetime | None


@dataclass(frozen=True, slots=True)
class FactHistory:
    """A fact's versions, newest first, and its current version (``None`` when retracted)."""

    fact_id: str
    current: FactVersion | None
    versions: tuple[FactVersion, ...]


def _version(stored: StoredFact) -> FactVersion:
    return FactVersion(
        id=stored.id,
        fact_id=stored.fact_id,
        version=stored.version,
        content=stored.content,
        created_by=stored.created_by,
        created_at=stored.created_at,
        retracted_at=stored.retracted_at,
    )


def _history(fact_id: str, rows: Iterable[StoredFact]) -> FactHistory:
    newest_first = tuple(
        sorted((_version(row) for row in rows), key=lambda found: found.version, reverse=True)
    )
    current = next((found for found in newest_first if found.retracted_at is None), None)
    return FactHistory(fact_id=fact_id, current=current, versions=newest_first)


def company_facts() -> tuple[FactHistory, ...]:
    """Every fact with its versions, by ``fact_id``."""
    rows = company_facts_repo.all_versions()  # by fact_id, then version
    return tuple(
        _history(fact_id, group) for fact_id, group in groupby(rows, key=lambda row: row.fact_id)
    )


def fact(fact_id: str) -> FactHistory | None:
    """The fact ``fact_id`` with its versions, or ``None`` when it has none."""
    rows = company_facts_repo.versions_of(fact_id)
    return _history(rows[0].fact_id, rows) if rows else None


def allowed_facts(segment_id: str, market_id: str) -> tuple[FactVersion, ...]:
    """The current version of every fact allowed for the Segment ``segment_id`` and the Market
    ``market_id``, by ``fact_id``."""
    found: list[FactVersion] = []
    for history in company_facts():
        current = history.current
        if (
            current is not None
            and segment_id in current.content.segments
            and market_id in current.content.markets
        ):
            found.append(current)
    return tuple(found)


def override_case_counts(contact_id: str) -> tuple[int, int]:
    """``(cases, erased)``: the Contact's override cases, live or retired, and how many of them
    are erased; see the module docstring."""
    rows = cases_repo.override_rows(contact_id) if is_ulid(contact_id) else ()
    return len(rows), sum(1 for row in rows if row.input == cases_repo.ERASED)


def export_pii(contact_id: str) -> dict[str, tuple[dict[str, object], ...]]:
    """The Contact's override cases, every column; see the module docstring."""
    rows = cases_repo.override_export(contact_id) if is_ulid(contact_id) else ()
    return {"override_cases": rows}
