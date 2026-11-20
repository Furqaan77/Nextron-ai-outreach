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

**Message Batches (Story 4.17, AD-8).** None of these waits: each is one request (plus the
results stream for an ended batch). A retryable failure (HTTP 429, a 5xx, no connection) raises
``AdapterError(True, "llm_unavailable")``, any other 4xx ``AdapterError(False,
"llm_rejected")``.

- ``BatchRequest(custom_id, inputs, framing=None)``: one item, frozen. ``custom_id`` must
  fullmatch ``nextron.domain.ai_batch.CUSTOM_ID_PATTERN`` (the domain's ``custom_id`` builds it),
  ``inputs`` is stored as a tuple of ``UntrustedBlock`` (never in the ``repr``) and ``framing``
  is a ``Framing`` or ``None``; else ``TypeError`` or ``ValueError``.
- ``submit_batch(route_tuple, items) -> str``: the provider's batch id. The caller checks are
  ``run``'s (an unknown tuple raises ``AdapterError(False, "llm_unknown_route")``), plus: the
  route must be batchable, ``items`` a non-empty sequence of ``BatchRequest`` whose
  ``custom_id`` never repeats, each item's blocks of exactly one subject (``ValueError``).
- ``fetch_batch(batch_id, route_tuple) -> BatchInProgress | BatchEnded``: ``route_tuple`` is the
  tuple the batch was submitted with (its results are validated against its route's schema and
  metered as it; an unknown tuple is ``llm_unknown_route``). ``BatchEnded.results`` maps each
  ``custom_id`` the provider answered to a ``BatchItemResult``: ``BatchSucceeded(result)``,
  ``result`` an ``LlmOk``, ``LlmRefused``, ``LlmTruncated`` or ``LlmInvalid`` (read by ``run``'s
  outcome rules, never retried), or ``BatchLost(kind)``, ``kind`` a ``BatchLostKind``
  (``errored``, ``canceled`` or ``expired``). An item the provider didn't answer is absent.
- ``delete_batch(batch_id) -> None``: a batch already gone (HTTP 404) counts as deleted.

``fetch_batch`` and ``delete_batch`` take an id that fullmatches ``BATCH_ID_PATTERN``
(``check_batch_id``: ``TypeError`` or ``ValueError`` before any request).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field, fields
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol, runtime_checkable

from nextron.domain.ai import (
    Framing,
    RouteTuple,
    UntrustedBlock,
    Urgency,
    check_blocks,
    check_framing,
)
from nextron.domain.ai_batch import CUSTOM_ID_PATTERN
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
BatchAnswer = LlmOk | LlmRefused | LlmTruncated | LlmInvalid  # a succeeded item's result


# Message Batches (Story 4.17) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BatchRequest:
    """One item of a Message Batch: its ``custom_id``, its one subject's blocks and framing."""

    custom_id: str
    inputs: tuple[UntrustedBlock, ...] = field(repr=False)
    framing: Framing | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.custom_id, str):
            raise TypeError("custom_id must be a str")
        if CUSTOM_ID_PATTERN.fullmatch(self.custom_id) is None:
            raise ValueError(f"custom_id must match {CUSTOM_ID_PATTERN.pattern}")
        if isinstance(self.inputs, str | bytes) or not isinstance(self.inputs, Sequence):
            raise TypeError("inputs must be a sequence of UntrustedBlock")
        object.__setattr__(self, "inputs", tuple(self.inputs))
        check_blocks(self.inputs)
        check_framing(self.framing)


class BatchLostKind(StrEnum):
    """Why a batch item has no answer."""

    ERRORED = "errored"
    CANCELED = "canceled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class BatchSucceeded:
    """A batch item the model answered."""

    result: BatchAnswer

    def __post_init__(self) -> None:
        if not isinstance(self.result, LlmOk | LlmRefused | LlmTruncated | LlmInvalid):
            raise TypeError("result must be an LlmOk, LlmRefused, LlmTruncated or LlmInvalid")


@dataclass(frozen=True, slots=True)
class BatchLost:
    """A batch item with no answer: it errored, was canceled or expired."""

    kind: BatchLostKind

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BatchLostKind):
            raise TypeError("kind must be a BatchLostKind")


BatchItemResult = BatchSucceeded | BatchLost


@dataclass(frozen=True, slots=True)
class BatchInProgress:
    """A batch still being processed."""


@dataclass(frozen=True, slots=True)
class BatchEnded:
    """An ended batch: each answered item's result, by ``custom_id``."""

    results: Mapping[str, BatchItemResult]

    def __post_init__(self) -> None:
        if not isinstance(self.results, Mapping):
            raise TypeError("results must be a mapping")
        for key, value in self.results.items():
            if not isinstance(key, str) or CUSTOM_ID_PATTERN.fullmatch(key) is None:
                raise ValueError(f"each custom_id must match {CUSTOM_ID_PATTERN.pattern}")
            if not isinstance(value, BatchSucceeded | BatchLost):
                raise TypeError("each result must be a BatchSucceeded or a BatchLost")
        object.__setattr__(self, "results", MappingProxyType(dict(self.results)))


BatchState = BatchInProgress | BatchEnded
BATCH_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")


def check_batch_id(batch_id: object) -> str:
    """``batch_id``, or ``TypeError`` / ``ValueError`` unless it fullmatches
    ``BATCH_ID_PATTERN`` (the provider's ids, such as ``msgbatch_...``)."""
    if not isinstance(batch_id, str):
        raise TypeError("batch_id must be a str")
    if BATCH_ID_PATTERN.fullmatch(batch_id) is None:
        raise ValueError(f"batch_id must match {BATCH_ID_PATTERN.pattern}")
    return batch_id


def check_batchable(batchable: bool) -> None:
    """``ValueError`` unless the route candidate is batchable (a caller's bug, Story 4.17)."""
    if batchable is not True:
        raise ValueError("the route is not batchable")


def check_batch_items(items: Sequence[BatchRequest]) -> tuple[BatchRequest, ...]:
    """``items`` as a tuple, or ``TypeError`` / ``ValueError``: a non-empty sequence of
    ``BatchRequest`` whose ``custom_id`` never repeats (each already holds one subject)."""
    if isinstance(items, str | bytes) or not isinstance(items, Sequence):
        raise TypeError("items must be a sequence of BatchRequest")
    found = tuple(items)
    if not all(isinstance(item, BatchRequest) for item in found):
        raise TypeError("items must be a sequence of BatchRequest")
    if not found:
        raise ValueError("a batch must hold at least one item")
    ids = [item.custom_id for item in found]
    if len(set(ids)) != len(ids):
        raise ValueError("a batch must not repeat a custom_id")
    return found


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

    def submit_batch(self, route_tuple: RouteTuple, items: Sequence[BatchRequest]) -> str:
        """Submit one Message Batch; the provider's batch id (Story 4.17)."""
        ...

    def fetch_batch(
        self, batch_id: str, route_tuple: RouteTuple, *, metered: bool = True
    ) -> BatchState:
        """The batch's state, and every item's result once it ended (Story 4.17); with
        ``metered=False`` (a re-fetch of a batch already seen ended) no usage is recorded."""
        ...

    def delete_batch(self, batch_id: str) -> None:
        """Delete the batch; one already gone counts as deleted (Story 4.17)."""
        ...
