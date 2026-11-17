"""Building a Claude request (AD-8, INT-7, NFR-17): the cached system prompt, the untrusted user
turn and the output schema.

Import rule: ``nextron.domain``, this package and the standard library.

**``user_turn(blocks)``** is the whole user turn. It first runs
``nextron.domain.ai.check_blocks``, so it raises ``TypeError`` or ``ValueError`` before any
request when there are no blocks, when they name more than one subject, or when a ``block_id``
repeats. Each block renders as::

    <untrusted source="<source>" id="<block_id>">
    <text, escaped>
    </untrusted>

and blocks are separated by a blank line. The user turn holds nothing else: the instructions
live only in the system prompt.

**Escaping** (``escape(text)``). A delimiter look-alike is ``<`` or one of its look-alikes
(U+FF1C, U+FE64, U+2039, U+2329, U+27E8, U+3008), then optional whitespace and an optional
``/`` (with optional whitespace after it), then ``untrusted`` in any case, each letter ASCII or
fullwidth. Its bracket becomes ``&lt;``; nothing else in the text changes, so ``&``, quotation
marks and any other ``<`` stay verbatim. ``</untrusted>``, ``< / UnTrUsted >`` and
``＜ｕｎｔｒｕｓｔｅｄ`` therefore all start with ``&lt;``, and ``Smith & Jones <b>`` stays as it
is.

**``build_request(route_tuple, candidate, blocks, max_tokens)``** returns the keyword arguments
for ``messages.create``, and nothing else:

- ``model``: the tuple's model;
- ``max_tokens``;
- ``system``: one text block holding the system prompt, with ``cache_control`` ``ephemeral``
  (the stable, cached prefix). When the tuple carries a pinned Sales Playbook snapshot
  (``RouteTuple.playbook``, Story 4.4) it is two text blocks: the system prompt, then
  ``nextron.domain.playbook.playbook_section(snapshot)`` (the order of authority, the version
  and the snapshot's text); only the last carries ``cache_control``, so the cached prefix holds
  both;
- ``messages``: one user message, ``user_turn(blocks)``;
- ``output_config``: the tuple's ``effort``, and ``format`` ``json_schema`` with the
  candidate's wire schema (``schemas.wire_schema``), never in the system text.

It never sets ``thinking``, ``temperature``, ``top_p``, ``top_k``, ``tools``, ``tool_choice``,
``stop_sequences``, ``fallbacks`` or ``betas``, and never adds an assistant prefill.
"""

import re
from collections.abc import Sequence
from typing import Any, Final

from nextron.adapters.ai.routes import Candidate
from nextron.adapters.ai.schemas import wire_schema
from nextron.domain.ai import RouteTuple, UntrustedBlock, check_blocks
from nextron.domain.playbook import playbook_section

# "<" and the characters that render like it.
BRACKETS: Final = "<＜﹤‹〈⟨〈"
WORD: Final = "untrusted"
FULLWIDTH_SMALL_A: Final = 0xFF41
FULLWIDTH_CAPITAL_A: Final = 0xFF21


def _letter(letter: str) -> str:
    """A character class for the ASCII lowercase ``letter``: it in ASCII and fullwidth, lower
    and upper case."""
    offset = ord(letter) - ord("a")
    variants = (
        letter,
        letter.upper(),
        chr(FULLWIDTH_SMALL_A + offset),
        chr(FULLWIDTH_CAPITAL_A + offset),
    )
    return "[" + "".join(variants) + "]"


LOOKALIKE: Final = re.compile(
    "[" + re.escape(BRACKETS) + r"](?=\s*/?\s*" + "".join(_letter(c) for c in WORD) + ")"
)
ESCAPED_BRACKET: Final = "&lt;"


def escape(text: str) -> str:
    """``text`` with the bracket of each delimiter look-alike turned into ``&lt;``."""
    return LOOKALIKE.sub(ESCAPED_BRACKET, text)


def _render(block: UntrustedBlock) -> str:
    return (
        f'<untrusted source="{block.source}" id="{block.block_id}">\n'
        f"{escape(block.text)}\n"
        "</untrusted>"
    )


def user_turn(blocks: Sequence[UntrustedBlock]) -> str:
    """The user turn: every block, escaped and delimited, separated by a blank line."""
    check_blocks(blocks)
    return "\n\n".join(_render(block) for block in blocks)


def build_request(
    route_tuple: RouteTuple,
    candidate: Candidate,
    blocks: Sequence[UntrustedBlock],
    max_tokens: int,
) -> dict[str, Any]:
    """The keyword arguments for ``messages.create``; see the module docstring."""
    texts = [route_tuple.system_prompt]
    if route_tuple.playbook is not None:
        texts.append(playbook_section(route_tuple.playbook))
    system: list[dict[str, Any]] = [{"type": "text", "text": text} for text in texts]
    system[-1]["cache_control"] = {"type": "ephemeral"}  # the cached prefix ends here
    return {
        "model": route_tuple.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_turn(blocks)}],
        "output_config": {
            "effort": route_tuple.effort.value,
            "format": {"type": "json_schema", "schema": wire_schema(candidate.output_schema)},
        },
    }
