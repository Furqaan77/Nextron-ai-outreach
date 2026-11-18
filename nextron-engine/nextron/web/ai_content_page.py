"""The AI content view (FR-71, AD-8, AD-13, NFR-25, UX-DR30; Story 4.4).

``create_app`` serves it at ``/ai``, linked from the Settings page (the navigation keeps its
sections, UX-DR1; decision 9). Both routes are ``@personal_data(False)``: the view shows route
codes, models, versions, hashes and times, never a person.

- ``GET /ai`` (``ai_content_page``) renders ``ai_content.html``, which extends ``base.html`` and
  includes ``ai_content_panel.html``, in the GET's read-only unit of work, from
  ``queries.playbook.route_tuples()`` and ``playbook_status()``.
- ``POST /ai/check`` (``ai_content_check``), the "Check for changes" button: its only field is
  ``csrf_token`` (``submitted_check``; anything else is ``MalformedForm``, 400). It runs the
  loader (``nextron.main.load_ai_content``: the verified prompt files, each route's default and
  the Sales Playbook read) outside any unit of work, then ``Playbook.sync_prompts(...,
  trigger=SyncTrigger.OPERATOR_CHECK)`` in its own unit of work, then renders
  ``ai_content_panel.html`` alone in a read-only one. The form posts with ``hx-post``,
  ``hx-target="#ai-content-panel"`` and ``hx-swap="outerHTML"``.

**The panel** (``panel_context``) shows the Sales Playbook block (the current version, its
12-hex short SHA-256 and when it was captured, then the last check with its time, trigger and
problem), the "Check for changes" form, then each route by name: its active tuple (model,
effort, prompt version, schema version, the Playbook version and short SHA-256 or "No
Playbook", and when and by whom it was activated) or "No active tuple", and every candidate
with its tuple id, the same fields and its created time, newest first.

**Outcomes** of the POST. ``check_notice(result)`` (``role="status"``): "Checked for changes:
<n> new candidate tuple(s)." ("tuple" for one), followed by "Stored Sales Playbook version <v>
(<sha12>)." when the check stored a snapshot. ``check_alert(result)`` (``role="alert"``):
"Sales Playbook: <problem text>" when the read failed. ``InvalidPrompts`` (a prompt file and
``manifest.lock`` disagree): 200, an alert "Nothing was stored: <message>", and nothing was
stored. After a check that stored nothing wrong, the POST also queues a test run of each new
tuple (``AiTestRuns.queue_runs``, Story 4.5), in a unit of work of its own after the sync's;
when it queued any, the status notice adds "Queued <n> test run(s)." ("run" for one), because
each run is paid Claude calls.

**The AI test gate** (FR-73, AD-12, UX-DR30, Story 4.5). Each tuple also shows:

- its newest test run on its route's set (``run``, a ``RunView``): the outcome ("Passed" or
  "Failed", with the error, "set too small" or the unknown route, when it failed unscored), the
  set's label, "<p> of <n> cases passed", the cost in dollars and when it finished; a metrics
  table with each metric's label, value ("<p>% (<n> of <d>)", "none (0 of 0)" for a share of
  nothing, or the count), bar ("at least <p>%" or "at most <n>") and "Pass" or "Fail"; and the
  failing cases (case id, expected and got) inside an ``hx-disable`` container, autoescaped.
  "No test run yet" when the tuple has none. A run that didn't measure the live case set
  (``RunSummary.current`` false, Story 4.6) adds "This run measured an earlier version of the
  <set label>." (``stale``): the tuple is due for a new run.
- "Ungated" on an active tuple that serves without a passing test run (a bootstrap one).
- On a candidate with a passing run (``CandidateTuple.passing``), the "Roll back to this tuple"
  form: ``method="post"``, ``hx-post="/ai/rollback"``, ``hx-target="#ai-content-panel"``,
  ``hx-swap="outerHTML"``, ``{{ csrf_field() }}`` and a hidden ``tuple_id``.

``POST /ai/rollback`` (``ai_content_rollback``) takes one ``tuple_id`` that is a ULID and no
field but it and ``csrf_token`` (``submitted_rollback``; anything else is ``MalformedForm``,
400).
It runs ``Playbook.activate(tuple_id, trigger=ActivationTrigger.ROLLBACK)`` in its own unit of
work, then renders the panel in a read-only one with the notice "Rolled back <route> to tuple
<id>." (``rollback_notice``; "<route> already runs tuple <id>." when the tuple already served,
so nothing changed), or, when ``activate`` refused (``ActivationRefused``), 200 with an alert of
the refusal's text and nothing changed.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.playbook.api import Activation, PlaybookRead, PromptSync
from nextron.app.queries.playbook import (
    ActiveTuple,
    CandidateTuple,
    PlaybookRef,
    PlaybookStatus,
    RouteTuples,
    RunSummary,
)
from nextron.domain.ai import RouteSpec
from nextron.domain.ai_test_gate import Metric, RunOutcome
from nextron.domain.ids import is_ulid
from nextron.domain.playbook import (
    SHORT_SHA_LENGTH,
    PlaybookProblem,
    PlaybookSnapshot,
    SyncTrigger,
    problem_text,
)
from nextron.domain.prompts import InvalidPrompts, PromptFile
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "ai_content.html"
PANEL_TEMPLATE: Final = "ai_content_panel.html"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
NO_PLAYBOOK: Final = "No Playbook"
PLAYBOOK_LEAD: Final = "Sales Playbook: "
NOTHING_STORED: Final = "Nothing was stored: "
TRIGGER_LABELS: Final = {
    SyncTrigger.STARTUP: "at engine start",
    SyncTrigger.OPERATOR_CHECK: "with Check for changes",
}
TUPLE_ID_FIELD: Final = "tuple_id"
OUTCOME_LABELS: Final = {RunOutcome.PASSED: "Passed", RunOutcome.FAILED: "Failed"}
MICROS_PER_DOLLAR: Final = 1_000_000
STALE_RUN: Final = "This run measured an earlier version of the {set_label}."

# The AI content view's loader: the verified prompt files, each route's default candidate and
# the Sales Playbook read, read outside any unit of work (nextron.main.load_ai_content).
type ContentLoader = Callable[
    [], tuple[Sequence[PromptFile], Sequence[RouteSpec], PlaybookSnapshot | PlaybookProblem]
]


@dataclass(frozen=True, slots=True)
class MetricView:
    """One row of a run's metrics table."""

    label: str
    value: str
    bar: str
    passed: bool


@dataclass(frozen=True, slots=True)
class CaseView:
    """One failing case: its id, what was expected and what the answer gave."""

    case_key: str
    expected: str
    got: str


@dataclass(frozen=True, slots=True)
class RunView:
    """A tuple's newest test run as the panel shows it."""

    outcome: str
    passed: bool
    error: str | None
    test_set: str
    counts: str
    cost: str
    time: str
    metrics: tuple[MetricView, ...]
    failing: tuple[CaseView, ...]
    stale: str | None = None


@dataclass(frozen=True, slots=True)
class TupleView:
    """One tuple as the panel shows it: its fields, the Playbook it runs under, when (and, for
    the active one, how) it came to be, its newest test run, whether it serves ungated, and
    whether it can be rolled back to."""

    id: str | None
    model: str
    effort: str
    prompt_version: str
    schema_version: int
    playbook: str
    time: str
    note: str | None
    run: RunView | None = None
    ungated: bool = False
    rollback: bool = False


@dataclass(frozen=True, slots=True)
class RouteView:
    """A route's active tuple (``None`` when it has none) and its candidates."""

    route: str
    active: TupleView | None
    candidates: tuple[TupleView, ...]


@dataclass(frozen=True, slots=True)
class PlaybookView:
    """The Sales Playbook block: the current snapshot and the last check."""

    version: str | None
    short_sha: str | None
    captured_at: str | None
    checked_at: str | None
    trigger: str | None
    problem: str | None


def _time(value: datetime) -> str:
    return f"{value:{TIME_FORMAT}}"


def playbook_label(ref: PlaybookRef | None) -> str:
    """``version <v> (<sha12>)``, or "No Playbook"."""
    if ref is None:
        return NO_PLAYBOOK
    return f"version {ref.version} ({ref.short})"


def metric_value(metric: Metric) -> str:
    """``<p>% (<n> of <d>)`` for a share (``none (0 of 0)`` with nothing to measure), or the
    count."""
    if metric.denominator is None:
        return f"{metric.numerator:,}"
    if metric.percent is None:
        return f"none ({metric.numerator:,} of {metric.denominator:,})"
    return f"{metric.percent}% ({metric.numerator:,} of {metric.denominator:,})"


def metric_bar(metric: Metric) -> str:
    """``at least <p>%`` for a share, ``at most <n>`` for a count."""
    if metric.denominator is None:
        return f"at most {metric.bar:,}"
    return f"at least {metric.bar}%"


def dollars(micros: int) -> str:
    """``$<dollars>`` to four decimals."""
    return f"${micros / MICROS_PER_DOLLAR:,.4f}"


def run_view(run: RunSummary) -> RunView:
    """The panel's view of a run."""
    return RunView(
        outcome=OUTCOME_LABELS[run.outcome],
        passed=run.outcome is RunOutcome.PASSED,
        error=None if run.error is None else run.error.text,
        test_set=run.test_set.label,
        counts=f"{run.passed_count:,} of {run.case_count:,} cases passed",
        cost=dollars(run.cost_micros),
        time=_time(run.finished_at),
        metrics=tuple(
            MetricView(metric.label, metric_value(metric), metric_bar(metric), metric.passed)
            for metric in run.metrics
        ),
        failing=tuple(
            CaseView(result.case_key, result.expected, result.got) for result in run.failing
        ),
        stale=None if run.current else STALE_RUN.format(set_label=run.test_set.label),
    )


def _active_view(active: ActiveTuple) -> TupleView:
    how = "bootstrap" if active.bootstrap else "tested"
    return TupleView(
        id=None,
        model=active.model,
        effort=active.effort.value,
        prompt_version=active.prompt_version,
        schema_version=active.schema_version,
        playbook=playbook_label(active.playbook),
        time=_time(active.activated_at),
        note=f"{how}, by {active.actor}",
        run=None if active.run is None else run_view(active.run),
        ungated=active.ungated,
    )


def _candidate_view(candidate: CandidateTuple) -> TupleView:
    return TupleView(
        id=candidate.id,
        model=candidate.model,
        effort=candidate.effort.value,
        prompt_version=candidate.prompt_version,
        schema_version=candidate.schema_version,
        playbook=playbook_label(candidate.playbook),
        time=_time(candidate.created_at),
        note=None,
        run=None if candidate.run is None else run_view(candidate.run),
        rollback=candidate.passing,
    )


def playbook_view(status: PlaybookStatus) -> PlaybookView:
    """The Sales Playbook block of ``status``."""
    current, check = status.current, status.last_check
    return PlaybookView(
        version=None if current is None else current.version,
        short_sha=None if current is None else current.short,
        captured_at=None if current is None else _time(current.captured_at),
        checked_at=None if check is None else _time(check.checked_at),
        trigger=None if check is None else TRIGGER_LABELS[check.trigger],
        problem=None if check is None or check.problem is None else problem_text(check.problem),
    )


def panel_context(
    tuples: Sequence[RouteTuples],
    status: PlaybookStatus,
    *,
    notice: str | None = None,
    alert: str | None = None,
) -> dict[str, object]:
    """What ``ai_content_panel.html`` renders."""
    routes = [
        RouteView(
            route=found.route,
            active=None if found.active is None else _active_view(found.active),
            candidates=tuple(_candidate_view(candidate) for candidate in found.candidates),
        )
        for found in tuples
    ]
    return {
        "playbook": playbook_view(status),
        "routes": routes,
        "notice": notice,
        "alert": alert,
    }


def submitted_check(fields: FormFields) -> None:
    """A "Check for changes" form posts only its ``csrf_token``; ``MalformedForm``
    otherwise."""
    if set(fields) - {CSRF_FIELD}:
        raise MalformedForm(WRONG_FIELDS)


def check_notice(result: PromptSync, queued: int = 0) -> str:
    """The status notice of a finished check that queued ``queued`` test runs."""
    count = len(result.candidates)
    noun = "tuple" if count == 1 else "tuples"
    notice = f"Checked for changes: {count:,} new candidate {noun}."
    read = result.playbook
    if isinstance(read, PlaybookRead) and read.stored:
        short = read.sha256[:SHORT_SHA_LENGTH]
        notice += f" Stored Sales Playbook version {read.version} ({short})."
    if queued > 0:
        notice += f" Queued {queued:,} test {'run' if queued == 1 else 'runs'}."
    return notice


def check_alert(result: PromptSync) -> str | None:
    """The alert of a check whose Playbook read failed, or ``None``."""
    if isinstance(result.playbook, PlaybookProblem):
        return PLAYBOOK_LEAD + problem_text(result.playbook)
    return None


def prompts_refused(refused: InvalidPrompts) -> str:
    """The alert of a check refused because the prompt files and their lock disagree."""
    return NOTHING_STORED + refused.message


def submitted_rollback(fields: FormFields) -> str:
    """The "Roll back to this tuple" form's ``tuple_id``: one ``tuple_id`` that is a ULID,
    and no field but it and ``csrf_token``; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {TUPLE_ID_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[TUPLE_ID_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    if not is_ulid(values[0]):
        raise MalformedForm(WRONG_FIELDS)
    return values[0]


def rollback_notice(result: Activation) -> str:
    """The status notice of a finished rollback: "Rolled back …", or "… already runs …" when the
    tuple already served (nothing changed)."""
    if not result.changed:
        return f"{result.route} already runs tuple {result.tuple_id}."
    return f"Rolled back {result.route} to tuple {result.tuple_id}."
