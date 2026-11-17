"""The AI routes as data (AD-8, AD-18): each route's candidate versions.

Import rule: ``nextron.domain``, this package and the standard library.

**``Candidate(name, model, effort, prompt_version, output_schema, batchable, max_tokens,
max_tokens_cap, budget_class)``** is one version of a route. It is frozen, and refused at
construction (``TypeError`` or ``ValueError`` from ``nextron.domain.ai``) unless its route
fields are valid and ``0 < max_tokens <= max_tokens_cap <= MAX_TOKENS_CEILING`` (16,000, which
keeps every request under the SDK's non-streaming limit). ``budget_class`` is a
``nextron.domain.budget.BudgetClass``: the calling service passes it to
``domain.budget.allow`` before the call; the gateway never checks the budget.

**``CANDIDATES``**, by route. The first candidate of each route is its default. No candidate,
and no request, carries ``fallbacks``: Haiku has no server-side fallback.

- ``extract_signals``: ``claude-haiku-5-5``, effort ``medium``, prompt ``v1``,
  ``ExtractSignalsV1`` (schema 1), batchable, ``max_tokens`` 8,000 capped at 16,000;
- ``extract_people``: ``claude-haiku-5-5``, effort ``medium``, prompt ``v1``,
  ``ExtractPeopleV1`` (schema 1), batchable, ``max_tokens`` 4,000 capped at 16,000;
- ``signal_inbox_parse``: ``claude-haiku-5-5``, effort ``medium``, prompt ``v1``,
  ``SignalInboxParseV1`` (schema 1), not batchable, ``max_tokens`` 2,000 capped at 8,000.

``extract_signals`` and ``extract_people`` are enrichment, ``signal_inbox_parse`` discovery;
all three are ``blockable``.

**Lookups.** ``candidates(route)`` lists a route's candidates (empty for an unknown route),
``default_candidate(route)`` is its first (``None`` for an unknown route), and
``find(route_tuple)`` is the candidate whose name, model, effort, prompt version and schema
version match the tuple's, or ``None``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from nextron.adapters.ai.schemas import (
    EXTRACT_PEOPLE_V1,
    EXTRACT_SIGNALS_V1,
    SIGNAL_INBOX_PARSE_V1,
    OutputSchema,
)
from nextron.domain.ai import Effort, RouteTuple, check_route_fields, check_token_limits
from nextron.domain.budget import BudgetClass

MAX_TOKENS_CEILING: Final = 16_000  # above it, the SDK wants a streaming request
HAIKU: Final = "claude-haiku-5-5"


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

    def __post_init__(self) -> None:
        version = getattr(self.output_schema, "version", None)
        check_route_fields(self.name, self.model, self.effort, self.prompt_version, version)
        check_token_limits(self.max_tokens, self.max_tokens_cap, MAX_TOKENS_CEILING)


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
        ):
            return candidate
    return None
