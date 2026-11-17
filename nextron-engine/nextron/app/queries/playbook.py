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
from nextron.db.repos.playbook.activations import StoredActivation
from nextron.db.repos.playbook.company_facts import StoredFact
from nextron.db.repos.playbook.route_tuples import StoredTuple
from nextron.domain.ai import Effort, RouteTuple
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
    "active_tuple",
    "allowed_facts",
    "company_facts",
    "fact",
    "health_issues",
    "playbook_status",
    "route_tuples",
]

PLAYBOOK_INVALID: Final = "playbook_invalid"
PLAYBOOK_SUBJECT: Final = "playbook"
ISSUE_LEAD: Final = "Sales Playbook: "
FIX_PLAYBOOK: Final = (
    "Fix the file, then use Check for changes on the AI content page (/ai) or restart the engine."
)


def active_tuple(route: str) -> RouteTuple | None:
    """The pinned tuple of ``route``, with the snapshot's text, or ``None``."""
    if not isinstance(route, str):
        raise TypeError("route must be a str")
    activation = activations_repo.active(route)
    if activation is None:
        return None
    where = f"{activation.route} {activation.prompt_version}"
    snapshot = snapshots_repo.get(activation.route, activation.prompt_version)
    if snapshot is None:
        raise ValueError(f"the active tuple of {where} names a prompt snapshot that isn't stored")
    route_tuple = RouteTuple(
        route=activation.route,
        model=activation.model,
        effort=Effort(activation.effort),
        prompt_version=activation.prompt_version,
        schema_version=activation.schema_version,
        system_prompt=snapshot.text,
        playbook=_pinned_playbook(activation),
    )
    if not route_tuple.prompt_sha256 == snapshot.sha256 == activation.prompt_sha256:
        raise ValueError(f"the stored prompt of {where} doesn't match its SHA-256")
    return route_tuple


def _pinned_playbook(activation: StoredActivation) -> PlaybookSnapshot | None:
    """The Playbook snapshot the activation names, checked against its SHA-256, or ``None``."""
    if activation.playbook_sha256 is None:
        return None
    stored = playbook_snapshots_repo.get(activation.playbook_sha256)
    if stored is None:
        raise ValueError(
            f"the active tuple of {activation.route} names a Playbook snapshot that isn't stored"
        )
    pinned = PlaybookSnapshot(stored.version, stored.content)
    if not pinned.sha256 == stored.sha256 == activation.playbook_sha256:
        raise ValueError(f"the stored Playbook of {activation.route} doesn't match its SHA-256")
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
class ActiveTuple:
    """The tuple a route runs: its active activation."""

    model: str
    effort: Effort
    prompt_version: str
    schema_version: int
    playbook: PlaybookRef | None
    activated_at: datetime
    bootstrap: bool
    actor: str


@dataclass(frozen=True, slots=True)
class CandidateTuple:
    """A stored tuple of a route that isn't its active one."""

    id: str
    model: str
    effort: Effort
    prompt_version: str
    schema_version: int
    playbook: PlaybookRef | None
    created_at: datetime


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


def _active(activation: StoredActivation, versions: dict[str, str]) -> ActiveTuple:
    return ActiveTuple(
        model=activation.model,
        effort=Effort(activation.effort),
        prompt_version=activation.prompt_version,
        schema_version=activation.schema_version,
        playbook=_ref(activation.route, activation.playbook_sha256, versions),
        activated_at=activation.activated_at,
        bootstrap=activation.bootstrap,
        actor=activation.actor,
    )


def _candidate(row: StoredTuple, versions: dict[str, str]) -> CandidateTuple:
    return CandidateTuple(
        id=row.id,
        model=row.model,
        effort=Effort(row.effort),
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        playbook=_ref(row.route, row.playbook_sha256, versions),
        created_at=row.created_at,
    )


def route_tuples() -> tuple[RouteTuples, ...]:
    """Every route's active tuple and candidates, by route name; see the module docstring."""
    versions: dict[str, str] = {}  # Playbook SHA-256 -> version, read once each
    actives = {activation.route: activation for activation in activations_repo.all_active()}
    rows: dict[str, list[StoredTuple]] = {}
    for row in tuples_repo.every():  # by route, newest first
        rows.setdefault(row.route, []).append(row)
    found: list[RouteTuples] = []
    for route in sorted(actives.keys() | rows.keys()):
        activation = actives.get(route)
        active_identity = None if activation is None else _identity(activation)
        candidates = tuple(
            _candidate(row, versions)
            for row in rows.get(route, [])
            if _identity(row) != active_identity
        )
        active = None if activation is None else _active(activation, versions)
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
    """``playbook_invalid`` when the newest Playbook check recorded a problem; otherwise
    none."""
    newest = checks_repo.newest()
    if newest is None or newest.problem is None:
        return ()
    text = ISSUE_LEAD + problem_text(PlaybookProblem(newest.problem))
    return (HealthIssue(PLAYBOOK_INVALID, PLAYBOOK_SUBJECT, text, detail=FIX_PLAYBOOK),)


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
