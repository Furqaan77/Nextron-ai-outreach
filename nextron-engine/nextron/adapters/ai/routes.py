"""The AI routes as data (AD-8, AD-18): each route's candidate versions.

Import rule: ``nextron.domain``, this package and the standard library.

**``Candidate(name, model, effort, prompt_version, output_schema, batchable, max_tokens,
max_tokens_cap, budget_class, uses_playbook=False)``** is one version of a route. It is frozen,
and refused at construction (``TypeError`` or ``ValueError`` from ``nextron.domain.ai``) unless
its route fields are valid, ``0 < max_tokens <= max_tokens_cap <= MAX_TOKENS_CEILING`` (16,000,
which keeps every request under the SDK's non-streaming limit) and ``uses_playbook`` is a
``bool``. ``budget_class`` is a ``nextron.domain.budget.BudgetClass``: the calling service
passes it to ``domain.budget.allow`` before the call; the gateway never checks the budget.
``uses_playbook`` (Story 4.4) says the route runs under a pinned Sales Playbook snapshot: its
tuples carry one (``RouteTuple.playbook``), the request frames it after the system prompt
(``prompting.build_request``), and ``Playbook.sync_prompts`` never bootstrap-activates it.

**``CANDIDATES``**, by route. The first candidate of each route is its default. No candidate,
and no request, carries ``fallbacks``: Haiku has no server-side fallback.

- ``extract_signals``: ``claude-haiku-5-5``, effort ``medium``, prompt ``v1``,
  ``ExtractSignalsV1`` (schema 1), batchable, ``max_tokens`` 8,000 capped at 16,000;
- ``extract_people``: ``claude-haiku-5-5``, effort ``medium``, prompt ``v1``,
  ``ExtractPeopleV1`` (schema 1), batchable, ``max_tokens`` 4,000 capped at 16,000;
- ``signal_inbox_parse``: ``claude-haiku-5-5``, effort ``medium``, prompt ``v1``,
  ``SignalInboxParseV1`` (schema 1), not batchable, ``max_tokens`` 2,000 capped at 8,000.

``extract_signals`` and ``extract_people`` are enrichment, ``signal_inbox_parse`` discovery;
all three are ``blockable``, and none uses the Playbook.

**A shipped candidate never changes or goes away** (Story 2.8): a stored activation may name its
route, model, effort, prompt version and schema version, and the gateway refuses a tuple that no
candidate matches (``llm_unknown_route``), so every call on that route would fail. To change a
route, add a new candidate (a new default goes first). ``SHIPPED_CANDIDATES`` in
``tests/contracts/test_prompt_manifest.py`` is the add-only list a contract test holds them to.

**Lookups.** ``candidates(route)`` lists a route's candidates (empty for an unknown route),
``default_candidate(route)`` is its first (``None`` for an unknown route), and
``find(route_tuple)`` is the candidate whose name, model, effort, prompt version and schema
version match the tuple's and whose ``uses_playbook`` matches whether the tuple carries a
Playbook snapshot, or ``None`` (so a Playbook tuple never runs a non-Playbook candidate, nor the
other way round: the gateway answers ``llm_unknown_route``).

**Prompts (Story 2.8).** ``PROMPTS_DIR`` is this package's ``prompts`` folder: each route's
system prompts as ``<route>/<version>.md``, pinned by ``manifest.lock``
(``nextron.domain.prompts``). ``default_specs()`` is each route's default candidate as a
``nextron.domain.ai.RouteSpec`` (route, model, effort, prompt version, schema version and
``uses_playbook``), in ``CANDIDATES`` order: what ``Playbook.sync_prompts`` records as each
route's candidate tuple and activates for a non-Playbook route that has no active tuple. Only
``nextron.main.load_prompts`` reads them, inside ``serve`` and the AI content view's check.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from nextron.adapters.ai.schemas import (
    EXTRACT_PEOPLE_V1,
    EXTRACT_SIGNALS_V1,
    SIGNAL_INBOX_PARSE_V1,
    OutputSchema,
)
from nextron.domain.ai import (
    Effort,
    RouteSpec,
    RouteTuple,
    check_route_fields,
    check_token_limits,
    check_uses_playbook,
)
from nextron.domain.budget import BudgetClass

MAX_TOKENS_CEILING: Final = 16_000  # above it, the SDK wants a streaming request
HAIKU: Final = "claude-haiku-5-5"
PROMPTS_DIR: Final = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One version of a route; see the module docstring."""

    name: str
    model: str
    effort: Effort
    prompt_version: str
    output_schema: OutputSchema
    batchable: bool
    max_tokens: int
    max_tokens_cap: int
    budget_class: BudgetClass
    uses_playbook: bool = False

    def __post_init__(self) -> None:
        version = getattr(self.output_schema, "version", None)
        check_route_fields(self.name, self.model, self.effort, self.prompt_version, version)
        check_token_limits(self.max_tokens, self.max_tokens_cap, MAX_TOKENS_CEILING)
        check_uses_playbook(self.uses_playbook)


CANDIDATES: Final[Mapping[str, tuple[Candidate, ...]]] = MappingProxyType(
    {
        "extract_signals": (
            Candidate(
                name="extract_signals",
                model=HAIKU,
                effort=Effort.MEDIUM,
                prompt_version="v1",
                output_schema=EXTRACT_SIGNALS_V1,
                batchable=True,
                max_tokens=8_000,
                max_tokens_cap=16_000,
                budget_class=BudgetClass.BLOCKABLE,
            ),
        ),
        "extract_people": (
            Candidate(
                name="extract_people",
                model=HAIKU,
                effort=Effort.MEDIUM,
                prompt_version="v1",
                output_schema=EXTRACT_PEOPLE_V1,
                batchable=True,
                max_tokens=4_000,
                max_tokens_cap=16_000,
                budget_class=BudgetClass.BLOCKABLE,
            ),
        ),
        "signal_inbox_parse": (
            Candidate(
                name="signal_inbox_parse",
                model=HAIKU,
                effort=Effort.MEDIUM,
                prompt_version="v1",
                output_schema=SIGNAL_INBOX_PARSE_V1,
                batchable=False,
                max_tokens=2_000,
                max_tokens_cap=8_000,
                budget_class=BudgetClass.BLOCKABLE,
            ),
        ),
    }
)


def candidates(route: str) -> tuple[Candidate, ...]:
    """The route's candidates, the default first; empty for an unknown route."""
    return CANDIDATES.get(route, ())


def default_candidate(route: str) -> Candidate | None:
    """The route's first candidate, or ``None`` for an unknown route."""
    found = candidates(route)
    return found[0] if found else None


def find(route_tuple: RouteTuple) -> Candidate | None:
    """The candidate ``route_tuple`` names, or ``None``."""
    if not isinstance(route_tuple, RouteTuple):
        return None
    for candidate in candidates(route_tuple.route):
        if (
            candidate.model == route_tuple.model
            and candidate.effort == route_tuple.effort
            and candidate.prompt_version == route_tuple.prompt_version
            and candidate.output_schema.version == route_tuple.schema_version
            and (route_tuple.playbook is not None) == candidate.uses_playbook
        ):
            return candidate
    return None


def default_specs() -> tuple[RouteSpec, ...]:
    """Each route's default candidate as a ``RouteSpec``, in ``CANDIDATES`` order."""
    return tuple(
        RouteSpec(
            route=default.name,
            model=default.model,
            effort=default.effort,
            prompt_version=default.prompt_version,
            schema_version=default.output_schema.version,
            uses_playbook=default.uses_playbook,
        )
        for default in (found[0] for found in CANDIDATES.values() if found)
    )
