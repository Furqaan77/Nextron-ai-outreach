"""The ``LlmPort`` (AD-8, AD-9, INT-7, FR-57): one Claude call, with a typed result and its
metered usage.

``nextron.adapters.ai.ClaudeGateway`` implements it; tests use ``tests.fakes.llm.ScriptedLlm``.
The values it takes (``RouteTuple``, ``UntrustedBlock``, ``Urgency``) live in
``nextron.domain.ai``.

**``run(route_tuple, inputs, urgency, *, framing=None)``** makes the call that ``route_tuple``
names on the untrusted ``inputs`` of one subject, and returns exactly one ``LlmResult``.
``framing`` (Story 4.15, ``nextron.domain.ai.Framing``) adds a drafting call's trusted brief,
facts and Campaign note; without it the request is unchanged. It never checks the
budget: the calling service asks ``nextron.domain.budget.allow`` first. A failure the job runner
may retry (the API unavailable) raises ``AdapterError(True, ...)``; a caller's bad inputs raise
``TypeError`` or ``ValueError`` before any request.

**``LlmUsage(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, iterations,
requests, cost_micros)``** sums every HTTP request the call made (a ``max_tokens`` retry makes
two). Every field is a non-negative ``int`` (not a ``bool``); ``cost_micros`` is in
micro-dollars.

**The results.** Each is frozen and carries ``model`` (the last response's served model, or the
tuple's model when no response came back) and ``usage``, both keyword-only:

- ``LlmOk(parsed)``: ``parsed`` is the validated, case-normalised JSON object, a ``dict`` that
  never shows in the ``repr``;
- ``LlmRefused(category)``: the API's ``stop_details.category`` when it is a code, else
  ``None``;
- ``LlmTruncated()`` and ``LlmInvalid()``: no output text at all;
- ``LlmFailed(retryable, code, status)``: a failed request. Either the API rejected it
  (``status`` is the HTTP status), or something unexpected happened (``status`` is ``None``).
  The Claude gateway returns it only with ``retryable=False``: a retryable failure raises
  ``AdapterError(True, ...)`` instead. The field exists for later adapters.

``LlmResult`` is their union. No result, and no ``repr``, carries prompt or response text.
"""

from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass, field, fields
from typing import Any, Final, Protocol, runtime_checkable

from nextron.domain.ai import Framing, RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import CODE_PATTERN, MODEL_PATTERN

MIN_STATUS: Final = 100
MAX_STATUS: Final = 599


def _count(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """The usage of every HTTP request one call made, summed."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    iterations: int = 0
    requests: int = 0
    cost_micros: int = 0

    def __post_init__(self) -> None:
        for each in fields(self):
            _count(each.name, getattr(self, each.name))


def _served(model: object, usage: object) -> None:
    if not isinstance(model, str):
        raise TypeError("model must be a str")
    if MODEL_PATTERN.fullmatch(model) is None:
        raise ValueError(f"model must match {MODEL_PATTERN.pattern}")
    if not isinstance(usage, LlmUsage):
        raise TypeError("usage must be an LlmUsage")


@dataclass(frozen=True, slots=True)
class LlmOk:
    """The answer validated against the route's output schema."""

    parsed: dict[str, Any] = field(repr=False)
    _: KW_ONLY
    model: str
    usage: LlmUsage

    def __post_init__(self) -> None:
        if not isinstance(self.parsed, dict):
            raise TypeError("parsed must be a dict")
        _served(self.model, self.usage)


@dataclass(frozen=True, slots=True)
class LlmRefused:
    """The model declined (``stop_reason`` ``refusal``)."""

    category: str | None
    _: KW_ONLY
    model: str
    usage: LlmUsage

    def __post_init__(self) -> None:
        if self.category is not None and (
            not isinstance(self.category, str) or CODE_PATTERN.fullmatch(self.category) is None
        ):
            raise ValueError(f"category must be None or match {CODE_PATTERN.pattern}")
        _served(self.model, self.usage)


@dataclass(frozen=True, slots=True)
class LlmTruncated:
    """The answer hit ``max_tokens`` on its last request: after the one retry at double
    ``max_tokens`` (up to the route's cap), or at once when ``max_tokens`` already is the
    cap."""

    _: KW_ONLY
    model: str
    usage: LlmUsage

    def __post_init__(self) -> None:
        _served(self.model, self.usage)


@dataclass(frozen=True, slots=True)
class LlmInvalid:
    """The answer wasn't a JSON object that validates against the route's output schema."""

    _: KW_ONLY
    model: str
    usage: LlmUsage

    def __post_init__(self) -> None:
        _served(self.model, self.usage)


@dataclass(frozen=True, slots=True)
class LlmFailed:
    """A failed request: the API rejected it (``status``), or something unexpected happened
    (``status`` is ``None``). The Claude gateway returns it only with ``retryable=False``; a
    retryable failure raises ``AdapterError(True, ...)``. The field is for later adapters."""

    retryable: bool
    code: str
    status: int | None
    _: KW_ONLY
    model: str
    usage: LlmUsage

    def __post_init__(self) -> None:
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a bool")
        if not isinstance(self.code, str) or CODE_PATTERN.fullmatch(self.code) is None:
            raise ValueError(f"code must match {CODE_PATTERN.pattern}")
        if self.status is not None:
            if isinstance(self.status, bool) or not isinstance(self.status, int):
                raise TypeError("status must be None or an int")
            if not MIN_STATUS <= self.status <= MAX_STATUS:
                raise ValueError(f"status must be from {MIN_STATUS} to {MAX_STATUS}")
        _served(self.model, self.usage)


LlmResult = LlmOk | LlmRefused | LlmTruncated | LlmInvalid | LlmFailed


@runtime_checkable
class LlmPort(Protocol):
    """One Claude call through the route a tuple names."""

    def run(
        self,
        route_tuple: RouteTuple,
        inputs: Sequence[UntrustedBlock],
        urgency: Urgency,
        *,
        framing: Framing | None = None,
    ) -> LlmResult:
        """Make the call and return its one result; see the module docstring."""
        ...
