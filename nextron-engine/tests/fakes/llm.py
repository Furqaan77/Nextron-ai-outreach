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
"""

from collections import deque
from collections.abc import Sequence

from nextron.adapters.ai import LLM_UNKNOWN_ROUTE, routes
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
from nextron.ports.llm import LlmResult


class ScriptedLlm:
    def __init__(self, *script: LlmResult | AdapterError) -> None:
        self.script: deque[LlmResult | AdapterError] = deque(script)
        self.calls: list[tuple[RouteTuple, tuple[UntrustedBlock, ...], Urgency]] = []
        self.framings: list[Framing | None] = []

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
