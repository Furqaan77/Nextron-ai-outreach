"""Public command API of the ``playbook`` service.

Other services and the delivery layers change ``playbook`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

**``Playbook(clock).sync_prompts(files, defaults) -> PromptSync``** (FR-71, AD-8, AD-12, AD-18,
Story 2.8) is a ``@use_case``. ``serve`` runs it at every start, after the database is at head,
with the prompt files ``nextron.main.load_prompts`` read and verified against
``manifest.lock`` (``nextron.domain.prompts.PromptFile``) and each route's default candidate
(``nextron.domain.ai.RouteSpec``, from ``nextron.adapters.ai.routes.default_specs``).

Before any write it raises:

- ``TypeError`` unless every item of ``files`` is a ``PromptFile`` and every item of
  ``defaults`` a ``RouteSpec``;
- ``ValueError`` for a ``(route, version)`` that repeats in ``files``, or a route that repeats in
  ``defaults``;
- ``InvalidPrompts`` when a default names a prompt version that has no file (before any
  transaction), or, inside the transaction, when a stored snapshot of a file's ``(route,
  version)`` has another SHA-256: a used prompt never changes, so the change must be a new
  version. Nothing is written.

It reads the clock once, then works in one transaction:

- each file whose ``(route, version)`` has no snapshot is stored (``prompt_snapshots``, with its
  SHA-256 and text, created now); a file already stored with the same hash writes nothing;
- each default route with no active activation gets one: the default's model, effort, prompt
  version and schema version, the stored prompt's SHA-256, ``actor='seed'``,
  ``bootstrap=True``, no test run and ``activated_at`` = now, plus one ``events`` row (entity
  ``ai_route_activations``, ``None`` to ``active``, cause ``bootstrap``, actor ``seed``);
- an existing active activation is never replaced or changed, even when it differs from the
  default (Epic 4 activates a tested tuple). When a kept activation's model, effort, prompt
  version or schema version differs from the route's default, it logs one WARNING
  ``active_tuple_not_default`` with ``route``, ``active_version`` and ``default_version`` (the
  prompt versions) and nothing else, so drift from the default is never silent.

After the commit it logs ``prompts_synced`` (INFO) with ``stored_count``, ``unchanged_count``,
``activated_count`` and ``kept_count``, and returns ``PromptSync(stored, unchanged, activated,
kept)``: the ``(route, version)`` of each file stored and each file already stored, in input
order, then of each default activated and each active activation kept, in ``defaults`` order.
No prompt text reaches a log record, an event or an error.
"""

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.db import eventlog
from nextron.db.repos.playbook import activations as activations_repo
from nextron.db.repos.playbook import snapshots as snapshots_repo
from nextron.db.uow import current, use_case
from nextron.domain.ai import RouteSpec
from nextron.domain.prompts import USED_PROMPT_NEVER_CHANGES, InvalidPrompts, PromptFile
from nextron.ports.clock import Clock

PROMPTS_SYNCED: Final = "prompts_synced"
NOT_DEFAULT: Final = "active_tuple_not_default"
ACTIVATIONS_ENTITY: Final = "ai_route_activations"
ACTIVE_STATUS: Final = "active"
BOOTSTRAP_CAUSE: Final = "bootstrap"
SEED_ACTOR: Final = "seed"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PromptSync:
    """What ``sync_prompts`` did, each as ``(route, version)``: the files ``stored`` and the
    files already stored (``unchanged``), in input order; the defaults ``activated`` and the
    active activations ``kept``, in ``defaults`` order."""

    stored: tuple[tuple[str, str], ...]
    unchanged: tuple[tuple[str, str], ...]
    activated: tuple[tuple[str, str], ...]
    kept: tuple[tuple[str, str], ...]


class Playbook:
    """The versioned AI prompts and which tuple each AI route runs."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def sync_prompts(
        self, files: Sequence[PromptFile], defaults: Sequence[RouteSpec]
    ) -> PromptSync:
        """Store the prompt files and bootstrap-activate each route's default; see the module
        docstring."""
        chosen_files, chosen_defaults = _prompt_inputs(files, defaults)
        now = self._clock.now()
        with current().transaction():
            result = _sync(chosen_files, chosen_defaults, now)
        log.info(
            PROMPTS_SYNCED,
            extra={
                "stored_count": len(result.stored),
                "unchanged_count": len(result.unchanged),
                "activated_count": len(result.activated),
                "kept_count": len(result.kept),
            },
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
    files: tuple[PromptFile, ...], defaults: tuple[RouteSpec, ...], now: datetime
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
    activated: list[tuple[str, str]] = []
    kept: list[tuple[str, str]] = []
    for spec in defaults:
        current_activation = activations_repo.active(spec.route)
        if current_activation is not None:
            kept.append((current_activation.route, current_activation.prompt_version))
            _warn_if_not_default(current_activation, spec)
            continue
        activation_id = activations_repo.insert(
            route=spec.route,
            model=spec.model,
            effort=spec.effort.value,
            prompt_version=spec.prompt_version,
            prompt_sha256=hashes[(spec.route, spec.prompt_version)],
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
    stored = tuple((prompt.route, prompt.version) for prompt in new)
    return PromptSync(stored, tuple(unchanged), tuple(activated), tuple(kept))


def _warn_if_not_default(active: activations_repo.StoredActivation, default: RouteSpec) -> None:
    """Log ``active_tuple_not_default`` when the kept activation isn't the route's default."""
    if (active.model, active.effort, active.prompt_version, active.schema_version) != (
        default.model,
        default.effort.value,
        default.prompt_version,
        default.schema_version,
    ):
        log.warning(
            NOT_DEFAULT,
            extra={
                "route": active.route,
                "active_version": active.prompt_version,
                "default_version": default.prompt_version,
            },
        )
