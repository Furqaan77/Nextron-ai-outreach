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
4. each default route with no active activation that doesn't use the Playbook gets one
   (decision 4: a Playbook route is activated only through Story 4.5's gate): the default's
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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.playbook import activations as activations_repo
from nextron.db.repos.playbook import company_facts as company_facts_repo
from nextron.db.repos.playbook import playbook_checks as checks_repo
from nextron.db.repos.playbook import playbook_snapshots as playbook_snapshots_repo
from nextron.db.repos.playbook import route_tuples as tuples_repo
from nextron.db.repos.playbook import snapshots as snapshots_repo
from nextron.db.repos.playbook.company_facts import StoredFact
from nextron.db.uow import command, current, use_case
from nextron.domain import company_facts as facts_domain
from nextron.domain.ai import RouteSpec
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
from nextron.domain.playbook import PlaybookProblem, PlaybookSnapshot, SyncTrigger
from nextron.domain.prompts import USED_PROMPT_NEVER_CHANGES, InvalidPrompts, PromptFile
from nextron.ports.clock import Clock

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


class Playbook:
    """The versioned AI prompts, the Sales Playbook snapshots, and which tuple each AI route
    runs."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

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
        if spec.uses_playbook:  # decision 4: only Story 4.5's gate activates a Playbook route
            continue
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
