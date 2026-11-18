"""Public command API of the ``playbook`` service.

Other services and the delivery layers change ``playbook`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

**``Playbook(clock).sync_prompts(files, defaults, playbook, *, trigger=SyncTrigger.STARTUP)
-> PromptSync``** (FR-71, AD-8, AD-12, AD-18, Stories 2.8 and 4.4) is a ``@use_case``. ``serve``
runs it at every start, after the database is at head, and the AI content view's "Check for
changes" runs it with ``trigger=SyncTrigger.OPERATOR_CHECK``. It takes the prompt files
``nextron.main.load_prompts`` read and verified against ``manifest.lock``
(``nextron.domain.prompts.PromptFile``), each route's default candidate
(``nextron.domain.ai.RouteSpec``, from ``nextron.adapters.ai.routes.default_specs``) and the
Sales Playbook read (``nextron.main.read_playbook_file``): a
``nextron.domain.playbook.PlaybookSnapshot``, or the ``PlaybookProblem`` that kept it from one.
``SyncTrigger`` names the actor and cause of what the Playbook work records: ``startup`` is
``system:playbook`` with ``startup_sync``, ``operator_check`` is ``operator`` with
``operator_check``.

Before any write it raises:

- ``TypeError`` unless every item of ``files`` is a ``PromptFile``, every item of ``defaults`` a
  ``RouteSpec``, ``playbook`` a ``PlaybookSnapshot`` or a ``PlaybookProblem`` and ``trigger`` a
  ``SyncTrigger``;
- ``ValueError`` for a ``(route, version)`` that repeats in ``files``, or a route that repeats in
  ``defaults``;
- ``InvalidPrompts`` when a default names a prompt version that has no file (before any
  transaction), or, inside the transaction, when a stored snapshot of a file's ``(route,
  version)`` has another SHA-256: a used prompt never changes, so the change must be a new
  version. Nothing is written.

With no files and no defaults it touches no table, records no check, and returns an empty
``PromptSync`` whose ``playbook`` is ``None``. Otherwise it reads the clock once, then works in
one transaction, in this order:

1. each file whose ``(route, version)`` has no snapshot is stored (``prompt_snapshots``, with
   its SHA-256 and text, created now); a file already stored with the same hash writes nothing;
2. a Playbook snapshot whose SHA-256 has no row is stored (``playbook_snapshots``, captured
   now); a stored one writes nothing (decision 7: a new hash is stored even when its version
   is already used);
3. the candidate tuples (``ai_route_tuples``, decision 3): per default, in order, its tuple is
   the default's route, model, effort, prompt version and schema version, the stored prompt's
   SHA-256 and, when the default ``uses_playbook``, the read snapshot's SHA-256 (a Playbook
   default gets no tuple when the read was a problem). A tuple with no row is inserted, created
   now, with one ``events`` row (entity ``ai_route_tuples``, ``None`` to ``candidate``, the
   trigger's cause and actor). Then each active activation of a default's route whose tuple has
   no row gets one the same way (the activations stored before Story 4.4);
4. each default route of ``nextron.domain.ai_test_gate.BOOTSTRAP_ROUTES`` (the three
   extraction routes, Story 4.5) with no active activation that doesn't use the Playbook gets
   one (decision 4: a Playbook route, and every route outside ``BOOTSTRAP_ROUTES``, is
   activated only through Story 4.5's gate): the default's
   model, effort, prompt version and schema version, the stored prompt's SHA-256, no Playbook,
   ``actor='seed'``, ``bootstrap=True``, no test run and ``activated_at`` = now, plus one
   ``events`` row (entity ``ai_route_activations``, ``None`` to ``active``, cause
   ``bootstrap``, actor ``seed``). An existing active activation is never replaced or changed,
   even when it differs from the default (Epic 4 activates a tested tuple). When a kept
   activation's model, effort, prompt version, schema version or use of the Playbook differs
   from the route's default, it logs one WARNING ``active_tuple_not_default`` with ``route``,
   ``active_version`` and ``default_version`` (the prompt versions) and nothing else, so drift
   from the default is never silent;
5. one ``playbook_checks`` row records the trigger and the read snapshot's SHA-256 or the
   problem, checked now.

After the commit it logs ``prompts_synced`` (INFO) with ``stored_count``, ``unchanged_count``,
``activated_count``, ``kept_count``, ``candidate_count`` and ``trigger`` (the trigger's value),
and, when the read was a problem, ``playbook_invalid`` (WARNING) with the ``problem`` code and
the ``trigger`` only. It returns ``PromptSync(stored,
unchanged, activated, kept, candidates, playbook)``: the ``(route, version)`` of each file
stored and each file already stored, in input order, then of each default activated and each
active activation kept, in ``defaults`` order; ``candidates``, the ``(route, tuple id)`` of each
tuple created, in creation order; and ``playbook``, a ``PlaybookRead(version, sha256, stored)``
or the ``PlaybookProblem``. No prompt or Playbook text reaches a log record, an event or an
error.

**The AI test gate** (FR-73, FR-71, AD-8, AD-12, NFR-26, Story 4.5). A route tuple serves only
after it passed its route's AI Test Set (``nextron.domain.ai_test_gate``); the bootstrap
activations of ``BOOTSTRAP_ROUTES`` are the one exception, marked "ungated" until a run passes.

- ``@use_case Playbook.sync_test_cases(cases) -> TestCaseSync(added, changed, retired,
  unchanged)``: ``TypeError`` unless every item is a ``nextron.domain.ai_test_cases.TestCase``,
  ``ValueError`` for a ``(test_set, case_key)`` that repeats, both before any transaction. Then,
  in one transaction: a key with no live row is inserted (``added``); a live row with another
  SHA-256 is retired (``retired_at`` = now) and the case inserted (``changed``); a live row with
  the same SHA-256 is left alone (``unchanged``); every live row whose key isn't in ``cases`` is
  retired (``retired``). Each is ``(test_set, case_key)``, in input order (``retired`` by set and
  key). An empty ``cases`` touches no table and changes nothing (as ``sync_prompts`` with no
  input). It logs ``test_cases_synced`` with the four counts. ``serve`` runs it at every start,
  in the prompt step's unit of work (decision 2).
- ``@use_case Playbook.activate(tuple_id, *, trigger) -> Activation(route, tuple_id, run_id,
  changed)``. ``ActivationTrigger`` is ``test_passed`` (actor ``system:playbook``) or
  ``rollback`` (actor ``operator``); anything else is a ``TypeError``. In one transaction it
  refuses, with ``ActivationRefused`` (code ``activation_refused``), writing nothing so the
  previous tuple keeps serving: an unknown tuple (a text that isn't a ULID included), "No such
  tuple"; a tuple whose route has no set, or with no ``passed`` run on its set, "No passing
  test run for this tuple". When the route's active activation has the tuple's model, effort,
  prompt version, schema version and Playbook and isn't a bootstrap one, nothing changes
  (``changed`` false). Otherwise: the active activation (when there is one) is deactivated now,
  with an ``events`` row ``active`` to ``inactive`` (cause the trigger's value, ``cause_ref``
  the qualifying run); an activation copied from the tuple row (its Playbook SHA-256 included)
  is inserted, with ``bootstrap`` false, ``test_run_id`` the tuple's newest passing run and the
  trigger's actor; and an ``events`` row ``None`` to ``active`` follows. It logs
  ``route_activated`` (``route``, ``tuple_id``, ``run_id``, ``trigger``, ``changed``).

**``AiTestRuns(clock, meter, llm, queue)``** runs the AI Test Sets.

- ``@use_case queue_runs() -> int``: in one transaction, each tuple row whose route has a set
  and that has no run on that set gets a ``run_ai_test_sets`` job (scope the tuple id,
  discriminator the set, payload ``{"v": 1, "tuple_id": <id>, "test_set": <set>}``, the bulk
  lane); a live job with that key absorbs it. It returns how many jobs it created and logs
  ``ai_test_runs_queued`` (``count``). ``nextron.main`` runs it at each engine start, after
  ``queue_contact_markets``, and the AI content view after Check for changes (decision 4).
- ``@use_case run(tuple_id, test_set, *, activate=True) -> bool`` (the job's use case):

  1. **First transaction.** When ``nextron.domain.budget.allow(BLOCKABLE, meter.snapshot(now),
     queries.setup.budget_limits())`` is false it returns ``False`` (the job is deferred) with
     nothing called or written (decision 3: a gate run is deferrable spend). An unknown tuple, or
     a ``test_set`` that isn't its route's set, logs ``ai_test_run_skipped`` (``tuple_id``,
     ``reason``) and returns ``True``. It builds the ``RouteTuple`` (``queries.playbook
     .tuple_of``) and reads the set's live cases of the tuple's route (by ``case_key``) and the
     stored Segments. When there are at least ``MIN_CASES`` cases, the set's scorer
     (``SCORERS``) must exist (``LookupError`` otherwise: only ``extraction`` has one, decision
     7), and a case naming a Segment that isn't stored raises ``InvalidTestCases`` before any
     call.
  2. **The calls**, with no transaction open. Fewer cases than ``MIN_CASES`` make a ``failed``
     run with ``set_too_small`` and no call. Otherwise each case's ``llm.run(route_tuple,
     blocks, urgency)`` runs in case order (the scorer builds the inputs). A retryable
     ``AdapterError`` propagates and nothing is stored (the job retries); ``llm_unknown_route``
     ends the run ``failed`` with ``unknown_route``; any other ``AdapterError`` propagates and
     nothing is stored (decision 8). Each answer is scored (an ``LlmOk``'s ``parsed``, or the
     result's type name).
  3. **Last transaction.** The run is inserted (``ai_test_runs``: its outcome, ``passed`` when
     every metric passed; the error; the case and passed counts; the metrics and every case's
     result; the summed ``cost_micros``; ``started_at`` and ``finished_at``). When it passed,
     ``activate`` is true and the tuple is its route's newest tuple row or the active tuple,
     ``activate``'s work runs in this transaction with ``test_passed`` (decision 5: an older tuple
     that passed is a rollback target only). It logs ``ai_test_run_finished`` (``tuple_id``,
     ``run_id``, ``test_set``, ``outcome``, ``error``, ``case_count``, ``passed_count``,
     ``cost_micros`` and ``activated``) and returns ``True``.

No case or prompt text reaches a log record, an event, an error or a job payload.

**Approved Company Facts** (FR-72, AD-7, AD-12, NFR-24, Story 4.3). ``CompanyFacts(clock)``
keeps the facts a Draft may claim as append-only versions in the ``company_facts`` table
(``nextron.db.repos.playbook.company_facts``), validated by ``nextron.domain.company_facts``.
A fact's *latest* row is its highest version; its *current* version is its highest version that
isn't retracted, or none. Retracting a fact retracts every version (decision 4), so an older
wording never silently becomes current; editing a retracted fact saves a new current version,
which restores it. Allowed Segments and Markets are stored as ``segments.id`` and ``markets.id``
ULIDs (read through ``queries.setup``), at least one of each and never "all" (decision 3).

- ``check_seed_facts(facts, segment_keys, market_countries, latest) -> CheckedFacts`` (a module
  function) runs before any transaction: ``TypeError`` for an item that isn't a ``SeedFact``,
  ``ValueError`` for a repeated ``fact_id``; for a non-empty input it validates the file form
  again (``validate_company_facts`` on each ``fact_form``) and then runs the domain's
  ``check_seed_facts`` against this sync's Segment keys and ``{code: country_code}`` (rules 3
  to 5), raising ``InvalidCompanyFacts``. The facts it keeps are the ones validation returns.
- ``@command seed_facts(checked, now) -> CompanyFactSync(created, changed, unchanged,
  conflicts, not_in_file)`` joins the caller's transaction (``nextron.app.ops.api.Seeds``). It
  maps keys and codes to ids through ``queries.setup.segments()`` and ``markets()``, which see
  this transaction's rows. Then, per file fact in file order (decision 5): no row inserts
  version 1 (``seed``), created ``(fact_id, 1)``; a latest row whose content equals the file's
  is unchanged, even when that row is retracted; a latest row that is the seed's and not
  retracted gets version n+1 (``seed``), changed ``(fact_id, n, n+1)``; otherwise (the latest
  row is the Operator's, or it is retracted) the fact is unchanged when the file equals the
  highest ``seed`` row's content, else a conflict ``(fact_id, n)`` and nothing is written. The
  seed never restores a retracted fact. ``not_in_file`` is ``(fact_id, latest version)`` for
  each stored fact the file leaves out, by ``fact_id``. Each insert writes one ``events`` row:
  entity ``company_facts``, ``entity_id`` the new row's id, ``None`` to ``v1`` or ``v<n>`` to
  ``v<n+1>``, cause ``seed_sync``, actor ``seed``.
- ``@use_case add(fact_id, fields) -> FactSaved(fact_id, version, changed)`` reads the clock
  once, then in one transaction validates ``fields`` (``FactFields``) through
  ``fact_from_fields`` against the stored Segment ids and ``{market id: country_code}``, with
  ``latest`` the UTC date of now plus one day. The stripped ``fact_id`` must fit
  ``FACT_ID_PATTERN`` ("Use lowercase letters, digits and underscores, starting with a letter";
  blank: "Enter a value") and have no row ("This fact id is already used"); every problem goes
  in one ``InvalidFact``. It inserts version 1 (``operator``) with an event ``None`` to ``v1``,
  cause ``operator_add``, actor ``operator``.
- ``@use_case edit(fact_id, based_on, fields) -> FactSaved`` validates like ``add``. An unknown
  ``fact_id`` adds ``{"form": "No fact has this id"}``, and a latest version other than
  ``based_on`` adds ``{"form": "This fact changed since the page loaded; reload the page and
  edit again"}`` (decision 9), together with any field problems. When the latest row isn't
  retracted and its content equals the new content it writes nothing and returns ``changed``
  false. Otherwise it inserts version n+1 (``operator``), even for a retracted fact, which
  restores it, with an event ``v<n>`` to ``v<n+1>``, cause ``operator_edit``, actor
  ``operator``.
- ``@use_case retract(fact_id) -> FactRetracted(fact_id, retracted)``: an unknown fact raises
  ``InvalidFact({"form": "No fact has this id"})``. It sets ``retracted_at`` on every version
  not yet retracted, with one event on the current row: ``v<n>`` to ``retracted``, cause
  ``operator_retract``, actor ``operator``. A fact with no current version writes nothing
  (``retracted`` false).
- ``today()``: the UTC date of now, the Add form's default ``last_checked_on``.

**Logs**, after the commit: ``company_facts_synced`` (``log_fact_sync(result)``: INFO with
``created_count``, ``changed_count``, ``unchanged_count``, ``not_in_file_count`` and
``conflict_count``), ``company_fact_saved`` (``fact_id``, ``version``, ``changed``) and
``company_fact_retracted`` (``fact_id``), never a wording or a URL.
"""

import logging
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
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
from nextron.db.uow import command, current, use_case
from nextron.domain import company_facts as facts_domain
from nextron.domain.ai import RouteSpec, RouteTuple, UntrustedBlock, Urgency
from nextron.domain.ai_test_cases import (
    TestCase,
    check_segments,
    extraction_inputs,
    score_extraction,
    stored_case,
)
from nextron.domain.ai_test_gate import (
    BOOTSTRAP_ROUTES,
    MIN_CASES,
    ROUTE_SETS,
    RUN_AI_TEST_SETS_JOB,
    TEST_SET_FIELD,
    TUPLE_ID_FIELD,
    CaseResult,
    Metric,
    RunError,
    RunOutcome,
    TestSet,
    extraction_metrics,
    metrics_json,
    results_json,
    run_passed,
    set_of,
)
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.company_facts import (
    ENTER_A_VALUE,
    FACT_ID_PATTERN,
    FORM_FIELD,
    FactContent,
    FactCreator,
    FactFields,
    InvalidCompanyFacts,
    InvalidFact,
    SeedFact,
    fact_form,
    fact_from_fields,
    validate_company_facts,
)
from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid
from nextron.domain.job import VERSION_FIELD
from nextron.domain.playbook import PlaybookProblem, PlaybookSnapshot, SyncTrigger
from nextron.domain.prompts import USED_PROMPT_NEVER_CHANGES, InvalidPrompts, PromptFile
from nextron.domain.segments import SegmentDef
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmOk, LlmPort, LlmResult
from nextron.ports.usage_meter import UsageMeterPort

PROMPTS_SYNCED: Final = "prompts_synced"
NOT_DEFAULT: Final = "active_tuple_not_default"
PLAYBOOK_INVALID: Final = "playbook_invalid"
ACTIVATIONS_ENTITY: Final = "ai_route_activations"
TUPLES_ENTITY: Final = "ai_route_tuples"
CANDIDATE_STATUS: Final = "candidate"
ACTIVE_STATUS: Final = "active"
BOOTSTRAP_CAUSE: Final = "bootstrap"
SEED_ACTOR: Final = "seed"
COMPANY_FACTS_ENTITY: Final = "company_facts"
SEED_SYNC_CAUSE: Final = "seed_sync"
OPERATOR_ADD: Final = "operator_add"
OPERATOR_EDIT: Final = "operator_edit"
OPERATOR_RETRACT: Final = "operator_retract"
OPERATOR_ACTOR: Final = "operator"
RETRACTED_STATUS: Final = "retracted"
FIRST_VERSION: Final = 1
COMPANY_FACTS_SYNCED: Final = "company_facts_synced"
COMPANY_FACT_SAVED: Final = "company_fact_saved"
COMPANY_FACT_RETRACTED: Final = "company_fact_retracted"
FACT_ID_FIELD: Final = "fact_id"
FACT_ID_FORM_REASON: Final = (
    "Use lowercase letters, digits and underscores, starting with a letter"
)
FACT_ID_TAKEN: Final = "This fact id is already used"
NO_SUCH_FACT: Final = "No fact has this id"
STALE_FACT: Final = "This fact changed since the page loaded; reload the page and edit again"
TEST_CASES_SYNCED: Final = "test_cases_synced"
ROUTE_ACTIVATED: Final = "route_activated"
INACTIVE_STATUS: Final = "inactive"
NO_SUCH_TUPLE: Final = "No such tuple"
NO_PASSING_RUN: Final = "No passing test run for this tuple"
AI_TEST_RUNS_QUEUED: Final = "ai_test_runs_queued"
AI_TEST_RUN_SKIPPED: Final = "ai_test_run_skipped"
AI_TEST_RUN_FINISHED: Final = "ai_test_run_finished"
UNKNOWN_TUPLE: Final = "unknown_tuple"
NOT_THE_ROUTES_SET: Final = "not_the_routes_set"
PAYLOAD_VERSION: Final = 1
# nextron.adapters.ai.LLM_UNKNOWN_ROUTE: the app never imports the gateway (tests pin the two)
UNKNOWN_ROUTE_CODE: Final = "llm_unknown_route"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlaybookRead:
    """The Sales Playbook a sync read: its version and SHA-256, and whether this sync stored
    it (``False`` when it was already stored)."""

    version: str
    sha256: str
    stored: bool


@dataclass(frozen=True, slots=True)
class PromptSync:
    """What ``sync_prompts`` did. Each of the first four is ``(route, version)``: the files
    ``stored`` and the files already stored (``unchanged``), in input order; the defaults
    ``activated`` and the active activations ``kept``, in ``defaults`` order. ``candidates`` is
    the ``(route, tuple id)`` of each candidate tuple created, and ``playbook`` the Playbook
    read (``None`` when the sync had no input)."""

    stored: tuple[tuple[str, str], ...]
    unchanged: tuple[tuple[str, str], ...]
    activated: tuple[tuple[str, str], ...]
    kept: tuple[tuple[str, str], ...]
    candidates: tuple[tuple[str, str], ...]
    playbook: PlaybookRead | PlaybookProblem | None


@dataclass(frozen=True, slots=True)
class TestCaseSync:
    """What ``sync_test_cases`` did, each as ``(test_set, case_key)``."""

    __test__ = False  # pytest: a result, not a test class

    added: tuple[tuple[str, str], ...]
    changed: tuple[tuple[str, str], ...]
    retired: tuple[tuple[str, str], ...]
    unchanged: tuple[tuple[str, str], ...]


class ActivationTrigger(StrEnum):
    """Why a tuple is activated: its test run passed, or the Operator rolled back to it."""

    TEST_PASSED = "test_passed"
    ROLLBACK = "rollback"

    @property
    def actor(self) -> str:
        """Who the activation's rows name."""
        return "system:playbook" if self is ActivationTrigger.TEST_PASSED else OPERATOR_ACTOR


class ActivationRefused(DomainError):
    """``activate`` refused a tuple; nothing was written and the previous tuple keeps
    serving."""

    code = "activation_refused"


@dataclass(frozen=True, slots=True)
class Activation:
    """What ``activate`` did: the route, the tuple, the qualifying run, and whether anything
    changed."""

    route: str
    tuple_id: str
    run_id: str
    changed: bool


def _identity(row: StoredActivation | StoredTuple) -> tuple[str, str, str, int, str | None]:
    """What makes a tuple distinct within its route (the tuples' unique index fields)."""
    return (row.model, row.effort, row.prompt_version, row.schema_version, row.playbook_sha256)


def _activate(tuple_id: str, trigger: ActivationTrigger, now: datetime) -> Activation:
    """``activate``'s work, inside the running use case's transaction; see the module
    docstring."""
    row = tuples_repo.get(tuple_id) if is_ulid(tuple_id) else None
    if row is None:
        raise ActivationRefused(NO_SUCH_TUPLE)
    test_set = ROUTE_SETS.get(row.route)
    passing = None if test_set is None else runs_repo.newest_passed(row.id, test_set.value)
    if passing is None:
        raise ActivationRefused(NO_PASSING_RUN)
    active = activations_repo.active(row.route)
    if active is not None and not active.bootstrap and _identity(active) == _identity(row):
        return Activation(row.route, row.id, passing.id, changed=False)
    if active is not None:
        activations_repo.deactivate(active.id, now)
        eventlog.append(
            entity=ACTIVATIONS_ENTITY,
            entity_id=active.id,
            from_status=ACTIVE_STATUS,
            to_status=INACTIVE_STATUS,
            cause=trigger.value,
            cause_ref=passing.id,
            actor=trigger.actor,
            at=now,
        )
    activation_id = activations_repo.insert(
        route=row.route,
        model=row.model,
        effort=row.effort,
        prompt_version=row.prompt_version,
        prompt_sha256=row.prompt_sha256,
        playbook_sha256=row.playbook_sha256,
        schema_version=row.schema_version,
        actor=trigger.actor,
        bootstrap=False,
        test_run_id=passing.id,
        at=now,
    )
    eventlog.append(
        entity=ACTIVATIONS_ENTITY,
        entity_id=activation_id,
        from_status=None,
        to_status=ACTIVE_STATUS,
        cause=trigger.value,
        cause_ref=passing.id,
        actor=trigger.actor,
        at=now,
    )
    return Activation(row.route, row.id, passing.id, changed=True)


def _log_case_sync(*found: Sequence[tuple[str, str]]) -> None:
    added, changed, retired, unchanged = found
    log.info(
        TEST_CASES_SYNCED,
        extra={
            "added_count": len(added),
            "changed_count": len(changed),
            "retired_count": len(retired),
            "unchanged_count": len(unchanged),
        },
    )


def _log_activation(result: Activation, trigger: ActivationTrigger) -> None:
    log.info(
        ROUTE_ACTIVATED,
        extra={
            "route": result.route,
            "tuple_id": result.tuple_id,
            "run_id": result.run_id,
            "trigger": trigger.value,
            "changed": result.changed,
        },
    )


class Playbook:
    """The versioned AI prompts, the Sales Playbook snapshots, the AI Test Set cases, and which
    tuple each AI route runs."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def sync_test_cases(self, cases: Sequence[TestCase]) -> TestCaseSync:
        """Store the AI Test Set cases, retiring changed and removed ones; see the module
        docstring."""
        chosen = tuple(cases)
        for case in chosen:
            if not isinstance(case, TestCase):
                raise TypeError(f"sync_test_cases takes TestCases, not {type(case).__name__}")
        keys = Counter((case.test_set.value, case.case_key) for case in chosen)
        repeated = sorted(f"{test_set} {key}" for (test_set, key), n in keys.items() if n > 1)
        if repeated:
            raise ValueError(f"sync_test_cases got a test case more than once: {repeated[0]}")
        now = self._clock.now()
        added: list[tuple[str, str]] = []
        changed: list[tuple[str, str]] = []
        unchanged: list[tuple[str, str]] = []
        retired: tuple[tuple[str, str], ...] = ()
        if not chosen:  # nothing to sync: no table is touched (as sync_prompts with no input)
            _log_case_sync(added, changed, retired, unchanged)
            return TestCaseSync((), (), (), ())
        with current().transaction():
            live = {(row.test_set, row.case_key): row for row in cases_repo.all_live()}
            for case in chosen:
                key = (case.test_set.value, case.case_key)
                row = live.pop(key, None)
                if row is None:
                    cases_repo.insert(case, now)
                    added.append(key)
                elif row.sha256 != case.sha256:
                    cases_repo.retire(row.id, now)
                    cases_repo.insert(case, now)
                    changed.append(key)
                else:
                    unchanged.append(key)
            retired = tuple(sorted(live))
            for key in retired:
                cases_repo.retire(live[key].id, now)
        _log_case_sync(added, changed, retired, unchanged)
        return TestCaseSync(tuple(added), tuple(changed), retired, tuple(unchanged))

    @use_case
    def activate(self, tuple_id: str, *, trigger: ActivationTrigger) -> Activation:
        """Activate a tuple that passed its AI Test Set; see the module docstring."""
        if not isinstance(trigger, ActivationTrigger):
            raise TypeError(f"activate takes an ActivationTrigger, not {type(trigger).__name__}")
        now = self._clock.now()
        with current().transaction():
            result = _activate(tuple_id, trigger, now)
        _log_activation(result, trigger)
        return result

    @use_case
    def sync_prompts(
        self,
        files: Sequence[PromptFile],
        defaults: Sequence[RouteSpec],
        playbook: PlaybookSnapshot | PlaybookProblem,
        *,
        trigger: SyncTrigger = SyncTrigger.STARTUP,
    ) -> PromptSync:
        """Store the prompt files and the Playbook, record the candidate tuples and the check,
        and bootstrap-activate each non-Playbook route's default; see the module docstring."""
        chosen_files, chosen_defaults = _prompt_inputs(files, defaults)
        if not isinstance(playbook, PlaybookSnapshot | PlaybookProblem):
            raise TypeError(
                "sync_prompts takes a PlaybookSnapshot or a PlaybookProblem, not "
                f"{type(playbook).__name__}"
            )
        if not isinstance(trigger, SyncTrigger):
            raise TypeError(f"sync_prompts takes a SyncTrigger, not {type(trigger).__name__}")
        now = self._clock.now()
        with current().transaction():
            if chosen_files or chosen_defaults:
                result = _sync(chosen_files, chosen_defaults, playbook, trigger, now)
            else:  # nothing to sync: no table is touched and no check is recorded
                result = PromptSync((), (), (), (), (), None)
        log.info(
            PROMPTS_SYNCED,
            extra={
                "stored_count": len(result.stored),
                "unchanged_count": len(result.unchanged),
                "activated_count": len(result.activated),
                "kept_count": len(result.kept),
                "candidate_count": len(result.candidates),
                "trigger": trigger.value,
            },
        )
        if isinstance(result.playbook, PlaybookProblem):
            log.warning(
                PLAYBOOK_INVALID,
                extra={"problem": result.playbook.value, "trigger": trigger.value},
            )
        return result


def _prompt_inputs(
    files: Sequence[PromptFile], defaults: Sequence[RouteSpec]
) -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
    """``sync_prompts``'s checks before any transaction."""
    chosen_files = tuple(files)
    chosen_defaults = tuple(defaults)
    for prompt in chosen_files:
        if not isinstance(prompt, PromptFile):
            raise TypeError(f"sync_prompts takes PromptFiles, not {type(prompt).__name__}")
    for spec in chosen_defaults:
        if not isinstance(spec, RouteSpec):
            raise TypeError(f"sync_prompts takes RouteSpecs, not {type(spec).__name__}")
    versions = Counter((prompt.route, prompt.version) for prompt in chosen_files)
    repeated = sorted(f"{route} {version}" for (route, version), n in versions.items() if n > 1)
    if repeated:
        raise ValueError(f"sync_prompts got a prompt version more than once: {repeated[0]}")
    routes = Counter(spec.route for spec in chosen_defaults)
    repeated = sorted(route for route, n in routes.items() if n > 1)
    if repeated:
        raise ValueError(f"sync_prompts got a default route more than once: {repeated[0]}")
    for spec in chosen_defaults:
        if (spec.route, spec.prompt_version) not in versions:
            raise InvalidPrompts(
                f"the default of route {spec.route} names prompt {spec.prompt_version}, which "
                "has no file"
            )
    return chosen_files, chosen_defaults


def _sync(
    files: tuple[PromptFile, ...],
    defaults: tuple[RouteSpec, ...],
    playbook: PlaybookSnapshot | PlaybookProblem,
    trigger: SyncTrigger,
    now: datetime,
) -> PromptSync:
    """The sync, inside the running use case's transaction."""
    hashes = {(prompt.route, prompt.version): prompt.sha256 for prompt in files}
    new: list[PromptFile] = []
    unchanged: list[tuple[str, str]] = []
    for prompt in files:  # every check before the first write
        snapshot = snapshots_repo.get(prompt.route, prompt.version)
        if snapshot is None:
            new.append(prompt)
        elif snapshot.sha256 != hashes[(prompt.route, prompt.version)]:
            raise InvalidPrompts(
                f"prompt {prompt.route} {prompt.version} is stored with another SHA-256; "
                f"{USED_PROMPT_NEVER_CHANGES}"
            )
        else:
            unchanged.append((prompt.route, prompt.version))
    for prompt in new:
        snapshots_repo.insert(prompt.route, prompt.version, prompt.sha256, prompt.text, now)
    read = _store_playbook(playbook, now)
    candidates = _record_candidates(defaults, hashes, playbook, trigger, now)
    activated: list[tuple[str, str]] = []
    kept: list[tuple[str, str]] = []
    for spec in defaults:
        current_activation = activations_repo.active(spec.route)
        if current_activation is not None:
            kept.append((current_activation.route, current_activation.prompt_version))
            _warn_if_not_default(current_activation, spec)
            continue
        if spec.uses_playbook or spec.route not in BOOTSTRAP_ROUTES:
            continue  # decision 4 and Story 4.5: every other route serves only after its gate
        activation_id = activations_repo.insert(
            route=spec.route,
            model=spec.model,
            effort=spec.effort.value,
            prompt_version=spec.prompt_version,
            prompt_sha256=hashes[(spec.route, spec.prompt_version)],
            playbook_sha256=None,
            schema_version=spec.schema_version,
            actor=SEED_ACTOR,
            bootstrap=True,
            test_run_id=None,
            at=now,
        )
        eventlog.append(
            entity=ACTIVATIONS_ENTITY,
            entity_id=activation_id,
            from_status=None,
            to_status=ACTIVE_STATUS,
            cause=BOOTSTRAP_CAUSE,
            cause_ref=None,
            actor=SEED_ACTOR,
            at=now,
        )
        activated.append((spec.route, spec.prompt_version))
    if isinstance(playbook, PlaybookSnapshot):
        checks_repo.insert(now, trigger.value, playbook.sha256, None)
    else:
        checks_repo.insert(now, trigger.value, None, playbook.value)
    stored = tuple((prompt.route, prompt.version) for prompt in new)
    return PromptSync(
        stored, tuple(unchanged), tuple(activated), tuple(kept), candidates, read
    )


def _store_playbook(
    playbook: PlaybookSnapshot | PlaybookProblem, now: datetime
) -> PlaybookRead | PlaybookProblem:
    """Store a Playbook snapshot whose SHA-256 has no row; what the read was."""
    if isinstance(playbook, PlaybookProblem):
        return playbook
    sha256 = playbook.sha256
    if playbook_snapshots_repo.get(sha256) is not None:
        return PlaybookRead(playbook.version, sha256, stored=False)
    playbook_snapshots_repo.insert(sha256, playbook.version, playbook.content, now)
    return PlaybookRead(playbook.version, sha256, stored=True)


def _record_candidates(
    defaults: tuple[RouteSpec, ...],
    hashes: Mapping[tuple[str, str], str],
    playbook: PlaybookSnapshot | PlaybookProblem,
    trigger: SyncTrigger,
    now: datetime,
) -> tuple[tuple[str, str], ...]:
    """Each default's tuple, then each active activation's of a default's route, inserted when
    it has no row; the ``(route, tuple id)`` of each inserted."""
    created: list[tuple[str, str]] = []
    read_sha256 = playbook.sha256 if isinstance(playbook, PlaybookSnapshot) else None
    for spec in defaults:
        if spec.uses_playbook and read_sha256 is None:
            continue  # a Playbook default gets no tuple from a problem read
        tuple_id = _record_tuple(
            route=spec.route,
            model=spec.model,
            effort=spec.effort.value,
            prompt_version=spec.prompt_version,
            prompt_sha256=hashes[(spec.route, spec.prompt_version)],
            playbook_sha256=read_sha256 if spec.uses_playbook else None,
            schema_version=spec.schema_version,
            trigger=trigger,
            now=now,
        )
        if tuple_id is not None:
            created.append((spec.route, tuple_id))
    for spec in defaults:
        found = activations_repo.active(spec.route)
        if found is None:
            continue
        tuple_id = _record_tuple(
            route=found.route,
            model=found.model,
            effort=found.effort,
            prompt_version=found.prompt_version,
            prompt_sha256=found.prompt_sha256,
            playbook_sha256=found.playbook_sha256,
            schema_version=found.schema_version,
            trigger=trigger,
            now=now,
        )
        if tuple_id is not None:
            created.append((found.route, tuple_id))
    return tuple(created)


def _record_tuple(
    *,
    route: str,
    model: str,
    effort: str,
    prompt_version: str,
    prompt_sha256: str,
    playbook_sha256: str | None,
    schema_version: int,
    trigger: SyncTrigger,
    now: datetime,
) -> str | None:
    """Insert the tuple with its ``candidate`` event when it has no row; its new id, or
    ``None``."""
    if (
        tuples_repo.find(route, model, effort, prompt_version, schema_version, playbook_sha256)
        is not None
    ):
        return None
    tuple_id = tuples_repo.insert(
        route=route,
        model=model,
        effort=effort,
        prompt_version=prompt_version,
        prompt_sha256=prompt_sha256,
        playbook_sha256=playbook_sha256,
        schema_version=schema_version,
        at=now,
    )
    eventlog.append(
        entity=TUPLES_ENTITY,
        entity_id=tuple_id,
        from_status=None,
        to_status=CANDIDATE_STATUS,
        cause=trigger.cause,
        cause_ref=None,
        actor=trigger.actor,
        at=now,
    )
    return tuple_id


def _warn_if_not_default(active: activations_repo.StoredActivation, default: RouteSpec) -> None:
    """Log ``active_tuple_not_default`` when the kept activation isn't the route's default."""
    if (
        active.model,
        active.effort,
        active.prompt_version,
        active.schema_version,
        active.playbook_sha256 is not None,
    ) != (
        default.model,
        default.effort.value,
        default.prompt_version,
        default.schema_version,
        default.uses_playbook,
    ):
        log.warning(
            NOT_DEFAULT,
            extra={
                "route": active.route,
                "active_version": active.prompt_version,
                "default_version": default.prompt_version,
            },
        )


@dataclass(frozen=True, slots=True)
class CheckedFacts:
    """The seed's facts, checked before any transaction (``check_seed_facts``)."""

    facts: tuple[SeedFact, ...]


@dataclass(frozen=True, slots=True)
class CompanyFactSync:
    """What ``seed_facts`` did: ``created`` as ``(fact_id, version)``, ``changed`` as
    ``(fact_id, from_version, to_version)``, ``unchanged`` fact ids and ``conflicts`` as
    ``(fact_id, latest version)``, each in file order, and ``not_in_file`` as ``(fact_id,
    latest version)`` of each stored fact the file left out, by ``fact_id``."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    conflicts: tuple[tuple[str, int], ...]
    not_in_file: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class FactSaved:
    """What ``add`` or ``edit`` did: the fact's latest version afterwards, and whether a version
    was saved."""

    fact_id: str
    version: int
    changed: bool


@dataclass(frozen=True, slots=True)
class FactRetracted:
    """What ``retract`` did: whether any version was retracted."""

    fact_id: str
    retracted: bool


def latest_allowed(now: datetime) -> date:
    """The latest date a fact may carry: the UTC date of ``now`` plus one day, so any
    timezone's today passes."""
    return now.astimezone(UTC).date() + timedelta(days=1)


def check_seed_facts(
    facts: Sequence[SeedFact],
    segment_keys: Collection[str],
    market_countries: Mapping[str, str],
    latest: date,
) -> CheckedFacts:
    """The seed's facts, checked before any transaction; see the module docstring."""
    chosen = tuple(facts)
    for fact in chosen:
        if not isinstance(fact, SeedFact):
            raise TypeError(f"check_seed_facts takes SeedFacts, not {type(fact).__name__}")
    counts = Counter(fact.fact_id for fact in chosen)
    repeated = sorted(fact_id for fact_id, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"check_seed_facts got a fact_id more than once: {', '.join(repeated)}")
    if not chosen:
        return CheckedFacts(())
    validated = validate_company_facts(
        {"company_facts": [fact_form(fact.fact_id, fact.content) for fact in chosen]}
    )
    facts_domain.check_seed_facts(validated, segment_keys, market_countries, latest)
    return CheckedFacts(validated)


def log_fact_sync(result: CompanyFactSync) -> None:
    """Log ``company_facts_synced`` with the counts of ``result``."""
    log.info(  # "created" itself is a LogRecord attribute, so each count is suffixed
        COMPANY_FACTS_SYNCED,
        extra={
            "created_count": len(result.created),
            "changed_count": len(result.changed),
            "unchanged_count": len(result.unchanged),
            "not_in_file_count": len(result.not_in_file),
            "conflict_count": len(result.conflicts),
        },
    )


def _version_status(version: int) -> str:
    return f"v{version}"


def _record(
    row_id: str, from_status: str | None, to_status: str, cause: str, actor: str, at: datetime
) -> None:
    eventlog.append(
        entity=COMPANY_FACTS_ENTITY,
        entity_id=row_id,
        from_status=from_status,
        to_status=to_status,
        cause=cause,
        cause_ref=None,
        actor=actor,
        at=at,
    )


def _current(rows: Sequence[StoredFact]) -> StoredFact | None:
    """The highest version that isn't retracted, or ``None``."""
    return next((row for row in reversed(rows) if row.retracted_at is None), None)


def _references() -> tuple[frozenset[str], dict[str, str]]:
    """The stored Segment ids and ``{market id: country_code}``, read in this transaction."""
    segment_ids = frozenset(seeded.id for seeded in setup_queries.segments())
    market_countries = {
        seeded.id: seeded.definition.country_code for seeded in setup_queries.markets()
    }
    return segment_ids, market_countries


def _stored_content(
    fact: SeedFact, segment_ids: Mapping[str, str], market_ids: Mapping[str, str]
) -> FactContent:
    """The file fact's content with its Segment keys and Market codes made ids, sorted."""
    content = fact.content
    path = f"company_facts[{fact.fact_id}]"
    segments, markets = [], []
    for key in content.segments:
        if key not in segment_ids:
            raise InvalidCompanyFacts(f"{path}.segments", "isn't a Segment of this sync")
        segments.append(segment_ids[key])
    for code in content.markets:
        if code not in market_ids:
            raise InvalidCompanyFacts(f"{path}.markets", "isn't a Market of this sync")
        markets.append(market_ids[code])
    return replace(content, segments=tuple(sorted(segments)), markets=tuple(sorted(markets)))


class CompanyFacts:
    """The Approved Company Facts: the seed's sync and the Operator's add, edit and retract."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def today(self) -> date:
        """The UTC date of now: the Add form's default ``last_checked_on``."""
        return self._clock.now().astimezone(UTC).date()

    @command
    def seed_facts(self, checked: CheckedFacts, now: datetime) -> CompanyFactSync:
        """Load the seed's facts in the caller's transaction; see the module docstring."""
        if not isinstance(checked, CheckedFacts):
            raise TypeError(f"seed_facts takes CheckedFacts, not {type(checked).__name__}")
        segment_ids = {seeded.key: seeded.id for seeded in setup_queries.segments()}
        market_ids = {seeded.code: seeded.id for seeded in setup_queries.markets()}
        stored: dict[str, list[StoredFact]] = {}
        for row in company_facts_repo.all_versions():  # by fact_id, then version
            stored.setdefault(row.fact_id, []).append(row)
        created: list[tuple[str, int]] = []
        changed: list[tuple[str, int, int]] = []
        unchanged: list[str] = []
        conflicts: list[tuple[str, int]] = []
        for fact in checked.facts:
            content = _stored_content(fact, segment_ids, market_ids)
            rows = stored.get(fact.fact_id, [])
            if not rows:
                row_id = company_facts_repo.insert(
                    fact.fact_id, FIRST_VERSION, content, FactCreator.SEED, now
                )
                _record(row_id, None, "v1", SEED_SYNC_CAUSE, SEED_ACTOR, now)
                created.append((fact.fact_id, FIRST_VERSION))
                continue
            latest = rows[-1]
            if latest.content == content:
                unchanged.append(fact.fact_id)
            elif latest.created_by is FactCreator.SEED and latest.retracted_at is None:
                version = latest.version + 1
                row_id = company_facts_repo.insert(
                    fact.fact_id, version, content, FactCreator.SEED, now
                )
                _record(
                    row_id,
                    _version_status(latest.version),
                    _version_status(version),
                    SEED_SYNC_CAUSE,
                    SEED_ACTOR,
                    now,
                )
                changed.append((fact.fact_id, latest.version, version))
            else:
                seeded = [row for row in rows if row.created_by is FactCreator.SEED]
                if seeded and seeded[-1].content == content:
                    unchanged.append(fact.fact_id)
                else:
                    conflicts.append((fact.fact_id, latest.version))
        in_file = {fact.fact_id for fact in checked.facts}
        not_in_file = tuple(
            (fact_id, rows[-1].version)
            for fact_id, rows in sorted(stored.items())
            if fact_id not in in_file
        )
        return CompanyFactSync(
            tuple(created), tuple(changed), tuple(unchanged), tuple(conflicts), not_in_file
        )

    def _content(
        self, fields: FactFields, now: datetime
    ) -> tuple[FactContent | None, dict[str, str]]:
        """``fields`` validated against the stored Segments and Markets: the content, or the
        problems."""
        segment_ids, market_countries = _references()
        try:
            content = fact_from_fields(
                fields,
                segment_refs=segment_ids,
                market_countries=market_countries,
                latest=latest_allowed(now),
            )
        except InvalidFact as refused:
            return None, refused.problems
        return content, {}

    @use_case
    def add(self, fact_id: str, fields: FactFields) -> FactSaved:
        """Save a new fact at version 1; see the module docstring."""
        now = self._clock.now()
        chosen = fact_id.strip() if isinstance(fact_id, str) else ""
        with current().transaction():
            problems: dict[str, str] = {}
            if not chosen:
                problems[FACT_ID_FIELD] = ENTER_A_VALUE
            elif FACT_ID_PATTERN.fullmatch(chosen) is None:
                problems[FACT_ID_FIELD] = FACT_ID_FORM_REASON
            elif company_facts_repo.versions_of(chosen):
                problems[FACT_ID_FIELD] = FACT_ID_TAKEN
            content, field_problems = self._content(fields, now)
            problems.update(field_problems)
            if problems or content is None:
                raise InvalidFact(problems)
            row_id = company_facts_repo.insert(
                chosen, FIRST_VERSION, content, FactCreator.OPERATOR, now
            )
            _record(row_id, None, "v1", OPERATOR_ADD, OPERATOR_ACTOR, now)
        return _saved(FactSaved(chosen, FIRST_VERSION, True))

    @use_case
    def edit(self, fact_id: str, based_on: int, fields: FactFields) -> FactSaved:
        """Save the Operator's edit as a new version; see the module docstring."""
        now = self._clock.now()
        with current().transaction():
            rows = company_facts_repo.versions_of(fact_id)
            problems: dict[str, str] = {}
            if not rows:
                problems[FORM_FIELD] = NO_SUCH_FACT
            elif rows[-1].version != based_on:
                problems[FORM_FIELD] = STALE_FACT
            content, field_problems = self._content(fields, now)
            problems.update(field_problems)
            if problems or content is None:
                raise InvalidFact(problems)
            latest = rows[-1]
            if latest.retracted_at is None and latest.content == content:
                result = FactSaved(latest.fact_id, latest.version, False)
            else:
                version = latest.version + 1
                row_id = company_facts_repo.insert(
                    latest.fact_id, version, content, FactCreator.OPERATOR, now
                )
                _record(
                    row_id,
                    _version_status(latest.version),
                    _version_status(version),
                    OPERATOR_EDIT,
                    OPERATOR_ACTOR,
                    now,
                )
                result = FactSaved(latest.fact_id, version, True)
        return _saved(result)

    @use_case
    def retract(self, fact_id: str) -> FactRetracted:
        """Retract every version of the fact; see the module docstring."""
        now = self._clock.now()
        with current().transaction():
            rows = company_facts_repo.versions_of(fact_id)
            if not rows:
                raise InvalidFact({FORM_FIELD: NO_SUCH_FACT})
            found = _current(rows)
            if found is None:
                return FactRetracted(rows[-1].fact_id, False)
            company_facts_repo.retract(found.fact_id, now)
            _record(
                found.id,
                _version_status(found.version),
                RETRACTED_STATUS,
                OPERATOR_RETRACT,
                OPERATOR_ACTOR,
                now,
            )
        log.info(COMPANY_FACT_RETRACTED, extra={"fact_id": found.fact_id})
        return FactRetracted(found.fact_id, True)


def _saved(result: FactSaved) -> FactSaved:
    log.info(
        COMPANY_FACT_SAVED,
        extra={"fact_id": result.fact_id, "version": result.version, "changed": result.changed},
    )
    return result


# The AI test runs (Story 4.5) -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scorer:
    """How a set's cases are run and scored: the check before any call, each case's inputs,
    each answer's score and the run's metrics."""

    check: Callable[[Sequence[TestCase], Sequence[SegmentDef]], None]
    inputs: Callable[[TestCase, int, Sequence[SegmentDef]], tuple[list[UntrustedBlock], Urgency]]
    score: Callable[[TestCase, Mapping[str, Any] | str], tuple[Any, CaseResult]]
    metrics: Callable[[str, Sequence[Any]], tuple[Metric, ...]]


# Each set's scorer (decision 7): only extraction has one until its routes ship theirs.
SCORERS: Final[Mapping[TestSet, Scorer]] = MappingProxyType(
    {
        TestSet.EXTRACTION: Scorer(
            check=check_segments,
            inputs=extraction_inputs,
            score=score_extraction,
            metrics=extraction_metrics,
        ),
    }
)


def _answer(result: LlmResult) -> Mapping[str, Any] | str:
    """What a scorer reads: an answer's parsed object, or any other result's type name."""
    return result.parsed if isinstance(result, LlmOk) else type(result).__name__


@dataclass(frozen=True, slots=True)
class _Prepared:
    """What the first transaction read for a run."""

    row: StoredTuple
    test_set: TestSet
    route_tuple: RouteTuple
    cases: tuple[TestCase, ...]
    segments: tuple[SegmentDef, ...]
    scorer: Scorer | None  # None when the set is too small


@dataclass(slots=True)
class _Scored:
    """What the calls gave."""

    error: RunError | None = None
    outcomes: list[Any] = field(default_factory=list)
    results: list[CaseResult] = field(default_factory=list)
    cost_micros: int = 0


class AiTestRuns:
    """The AI Test Set runs and their queueing; see the module docstring."""

    def __init__(
        self, clock: Clock, meter: UsageMeterPort, llm: LlmPort, queue: JobQueue
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._llm = llm
        self._queue = queue

    @use_case
    def queue_runs(self) -> int:
        """Queue a run of each tuple row that has none on its route's set; how many jobs were
        created."""
        created = 0
        with current().transaction():
            done = {(run.tuple_id, run.test_set) for run in runs_repo.every()}
            for row in tuples_repo.every():
                test_set = ROUTE_SETS.get(row.route)
                if test_set is None or (row.id, test_set.value) in done:
                    continue
                enqueued = self._queue.enqueue(
                    RUN_AI_TEST_SETS_JOB,
                    scope=row.id,
                    discriminator=test_set.value,
                    payload={
                        VERSION_FIELD: PAYLOAD_VERSION,
                        TUPLE_ID_FIELD: row.id,
                        TEST_SET_FIELD: test_set.value,
                    },
                )
                created += int(enqueued.created)
        log.info(AI_TEST_RUNS_QUEUED, extra={"count": created})
        return created

    @use_case
    def run(self, tuple_id: str, test_set: str, *, activate: bool = True) -> bool:
        """Run the tuple's AI Test Set and store the run; see the module docstring."""
        uow = current()
        started_at = self._clock.now()
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(BudgetClass.BLOCKABLE, self._meter.snapshot(started_at), limits):
                return False
            prepared = _prepare(tuple_id, test_set)
        if isinstance(prepared, str):
            log.info(AI_TEST_RUN_SKIPPED, extra={"tuple_id": tuple_id, "reason": prepared})
            return True
        scored = self._call(prepared)
        finished_at = self._clock.now()
        cases = prepared.cases
        if scored.error is not None or prepared.scorer is None:
            metrics: tuple[Metric, ...] = ()
            outcome = RunOutcome.FAILED
        else:
            metrics = prepared.scorer.metrics(prepared.row.route, scored.outcomes)
            outcome = RunOutcome.PASSED if run_passed(metrics) else RunOutcome.FAILED
        results = scored.results
        activated: Activation | None = None
        with uow.transaction():
            run_id = runs_repo.insert(
                tuple_id=prepared.row.id,
                test_set=prepared.test_set.value,
                outcome=outcome.value,
                error=None if scored.error is None else scored.error.value,
                case_count=len(cases),
                passed_count=sum(1 for result in results if result.passed),
                metrics=metrics_json(metrics),
                results=results_json(results),
                cost_micros=scored.cost_micros,
                started_at=started_at,
                finished_at=finished_at,
            )
            if outcome is RunOutcome.PASSED and activate and _may_activate(prepared.row):
                activated = _activate(prepared.row.id, ActivationTrigger.TEST_PASSED, finished_at)
        if activated is not None:
            _log_activation(activated, ActivationTrigger.TEST_PASSED)
        log.info(
            AI_TEST_RUN_FINISHED,
            extra={
                "tuple_id": prepared.row.id,
                "run_id": run_id,
                "test_set": prepared.test_set.value,
                "outcome": outcome.value,
                "error": None if scored.error is None else scored.error.value,
                "case_count": len(cases),
                "passed_count": sum(1 for result in results if result.passed),
                "cost_micros": scored.cost_micros,
                "activated": activated is not None and activated.changed,
            },
        )
        return True

    def _call(self, prepared: _Prepared) -> _Scored:
        """Step 2: every case's call and score, with no transaction open."""
        scorer = prepared.scorer
        if scorer is None:
            return _Scored(error=RunError.SET_TOO_SMALL)
        scored = _Scored()
        for number, case in enumerate(prepared.cases, start=1):
            blocks, urgency = scorer.inputs(case, number, prepared.segments)
            try:
                result = self._llm.run(prepared.route_tuple, blocks, urgency)
            except AdapterError as failure:
                if not failure.retryable and failure.code == UNKNOWN_ROUTE_CODE:
                    scored.error = RunError.UNKNOWN_ROUTE
                    return scored
                raise  # decision 8: retried, or the job dies and is queued at the next start
            scored.cost_micros += result.usage.cost_micros
            outcome, case_result = scorer.score(case, _answer(result))
            scored.outcomes.append(outcome)
            scored.results.append(case_result)
        return scored


def _prepare(tuple_id: str, test_set: str) -> _Prepared | str:
    """Step 1's reads, inside the first transaction: what the run needs, or why it is
    skipped."""
    row = tuples_repo.get(tuple_id) if is_ulid(tuple_id) else None
    if row is None:
        return UNKNOWN_TUPLE
    chosen = set_of(test_set)
    if chosen is None or ROUTE_SETS.get(row.route) is not chosen:
        return NOT_THE_ROUTES_SET
    route_tuple = playbook_queries.tuple_of(row.id)
    if route_tuple is None:  # pragma: no cover - the row was just read in this transaction
        return UNKNOWN_TUPLE
    cases = tuple(
        stored_case(
            stored.case_key,
            stored.test_set,
            stored.route,
            stored.input,
            stored.expected,
            stored.covers,
        )
        for stored in cases_repo.live(chosen.value, row.route)
    )
    segments = tuple(seeded.definition for seeded in setup_queries.segments())
    scorer: Scorer | None = None
    if len(cases) >= MIN_CASES[chosen]:
        scorer = SCORERS.get(chosen)
        if scorer is None:
            raise LookupError(f"the {chosen.value} set has no scorer yet")
        scorer.check(cases, segments)
    return _Prepared(row, chosen, route_tuple, cases, segments, scorer)


def _may_activate(row: StoredTuple) -> bool:
    """Decision 5: a passing run activates only its route's newest tuple row, or re-qualifies
    the active tuple."""
    newest = next((found for found in tuples_repo.every() if found.route == row.route), None)
    if newest is not None and newest.id == row.id:
        return True
    active = activations_repo.active(row.route)
    return active is not None and _identity(active) == _identity(row)
