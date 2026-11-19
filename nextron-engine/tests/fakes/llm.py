"""``ScriptedLlm``: the scripted fake of ``nextron.ports.llm.LlmPort``.

``ScriptedLlm(*script)`` answers each ``run`` with the next item of ``script``, in order: an
``LlmResult`` is returned as it is, an ``AdapterError`` is raised.

Before it touches the script, ``run`` makes the real gateway's caller checks, in the same order
as ``ClaudeGateway.run``, so a service test can't pass with inputs the gateway would refuse:

1. a tuple ``routes.find`` doesn't know raises ``AdapterError(False, "llm_unknown_route")``;
2. ``check_urgency(urgency)`` (``TypeError``);
3. ``check_blocks(inputs)`` (``TypeError`` or ``ValueError``).

A refused call consumes nothing and isn't recorded. ``calls`` records every
``(route_tuple, inputs, urgency)`` that passed the checks, answered or not (``inputs`` as a
tuple), and ``framings`` the ``framing`` of each (``None`` when the call had none; Story
4.15); a framing that isn't a ``Framing`` raises ``TypeError`` as in the gateway. A ``run``
after the script is exhausted raises ``AssertionError``, so a test can't make more calls than
it planned. Unlike the real gateway it meters nothing; like it, it checks no
budget.

**Message Batches (Story 4.17).** ``submit_batch`` makes the gateway's caller checks (an unknown
tuple raises ``AdapterError(False, "llm_unknown_route")``, a route that isn't batchable
``ValueError``, then ``check_batch_items``), records ``(route_tuple, items)`` in
``batches_submitted`` and returns ``<batch_prefix><n>`` (``n`` counting from 1; the keyword
``batch_prefix`` defaults to ``msgbatch_fake_``).
``fetch_batch(batch_id, route_tuple)`` (an unknown tuple is ``llm_unknown_route``) returns
``BatchInProgress()`` until a test calls ``end_batch(batch_id, {custom_id: BatchItemResult})``,
then that ``BatchEnded``; each fetch is recorded in ``fetched`` (and its ``metered`` keyword in
``fetched_metered``). ``delete_batch`` records the id
in ``deleted``. An unknown id raises ``AdapterError(False, "llm_rejected")``.
``submit_errors``, ``fetch_errors`` and ``delete_errors`` are queues of ``AdapterError`` the
next such call raises instead (a failed submit records nothing). Unlike the real gateway it
meters nothing for a batch either.
"""

from collections import deque
from collections.abc import Mapping, Sequence

from nextron.adapters.ai import LLM_REJECTED, LLM_UNKNOWN_ROUTE, routes
from nextron.domain.ai import (
    Framing,
    RouteTuple,
    UntrustedBlock,
    Urgency,
    check_blocks,
    check_framing,
    check_urgency,
)
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    BatchEnded,
    BatchInProgress,
    BatchItemResult,
    BatchRequest,
    BatchState,
    LlmResult,
    check_batch_id,
    check_batch_items,
    check_batchable,
)


class ScriptedLlm:
    def __init__(
        self, *script: LlmResult | AdapterError, batch_prefix: str = "msgbatch_fake_"
    ) -> None:
        self.batch_prefix = batch_prefix
        self.script: deque[LlmResult | AdapterError] = deque(script)
        self.calls: list[tuple[RouteTuple, tuple[UntrustedBlock, ...], Urgency]] = []
        self.framings: list[Framing | None] = []
        self.batches_submitted: list[tuple[RouteTuple, tuple[BatchRequest, ...]]] = []
        self.fetched: list[str] = []
        self.fetched_metered: list[bool] = []
        self.deleted: list[str] = []
        self.submit_errors: deque[AdapterError] = deque()
        self.fetch_errors: deque[AdapterError] = deque()
        self.delete_errors: deque[AdapterError] = deque()
        self._batches: dict[str, BatchState] = {}

    def run(
        self,
        route_tuple: RouteTuple,
        inputs: Sequence[UntrustedBlock],
        urgency: Urgency,
        *,
        framing: Framing | None = None,
    ) -> LlmResult:
        if routes.find(route_tuple) is None:
            raise AdapterError(
                False, LLM_UNKNOWN_ROUTE, "the route tuple matches no route candidate"
            )
        check_urgency(urgency)
        check_framing(framing)
        check_blocks(inputs)
        self.calls.append((route_tuple, tuple(inputs), urgency))
        self.framings.append(framing)
        if not self.script:
            raise AssertionError("ScriptedLlm: the script is exhausted")
        answer = self.script.popleft()
        if isinstance(answer, AdapterError):
            raise answer
        return answer

    # Message Batches (Story 4.17) -----------------------------------------------------------

    def submit_batch(self, route_tuple: RouteTuple, items: Sequence[BatchRequest]) -> str:
        candidate = routes.find(route_tuple)
        if candidate is None:
            raise AdapterError(
                False, LLM_UNKNOWN_ROUTE, "the route tuple matches no route candidate"
            )
        check_batchable(candidate.batchable)
        found = check_batch_items(items)
        if self.submit_errors:
            raise self.submit_errors.popleft()
        self.batches_submitted.append((route_tuple, found))
        batch_id = f"{self.batch_prefix}{len(self.batches_submitted)}"
        self._batches[batch_id] = BatchInProgress()
        return batch_id

    def fetch_batch(
        self, batch_id: str, route_tuple: RouteTuple, *, metered: bool = True
    ) -> BatchState:
        check_batch_id(batch_id)
        if routes.find(route_tuple) is None:
            raise AdapterError(
                False, LLM_UNKNOWN_ROUTE, "the route tuple matches no route candidate"
            )
        self.fetched.append(batch_id)
        self.fetched_metered.append(metered)
        if self.fetch_errors:
            raise self.fetch_errors.popleft()
        if batch_id not in self._batches:
            raise AdapterError(False, LLM_REJECTED, "no such batch")
        return self._batches[batch_id]

    def delete_batch(self, batch_id: str) -> None:
        check_batch_id(batch_id)
        if self.delete_errors:
            raise self.delete_errors.popleft()
        if batch_id not in self._batches:
            raise AdapterError(False, LLM_REJECTED, "no such batch")
        self.deleted.append(batch_id)

    def end_batch(self, batch_id: str, results: Mapping[str, BatchItemResult]) -> None:
        """End the batch with these results: ``fetch_batch`` returns them from now on."""
        if batch_id not in self._batches:
            raise AssertionError(f"ScriptedLlm: no batch {batch_id}")
        self._batches[batch_id] = BatchEnded(results)
