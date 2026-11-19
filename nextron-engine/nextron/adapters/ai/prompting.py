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

**``build_request(route_tuple, candidate, blocks, max_tokens, *, framing=None)``** returns the
keyword arguments for ``messages.create``, and nothing else:

- ``model``: the tuple's model;
- ``max_tokens``;
- ``system``: one text block holding the system prompt, with ``cache_control`` ``ephemeral``
  (the stable, cached prefix). When the tuple carries a pinned Sales Playbook snapshot
  (``RouteTuple.playbook``, Story 4.4) it is two text blocks: the system prompt, then
  ``nextron.domain.playbook.playbook_section(snapshot)`` (the order of authority, the version
  and the snapshot's text); only the last carries ``cache_control``, so the cached prefix holds
  both;
- with a ``framing`` (``nextron.domain.ai.Framing``, Story 4.15, AD-18): ``system`` is the
  system prompt, then ``framing.brief``, then the Playbook section (when the tuple pins one),
  then ``framing.facts``, each its own text block (a blank one left out), ``cache_control``
  only on the last; and the user turn starts with ``<campaign_note>``, the escaped
  ``framing.note``, ``</campaign_note>`` and a blank line (left out when the note is blank),
  followed by ``user_turn(blocks)``. Without one, the request is exactly as before;
- ``messages``: one user message, ``user_turn(blocks)``;
- ``output_config``: the tuple's ``effort``, and ``format`` ``json_schema`` with the
  candidate's wire schema (``schemas.wire_schema``), never in the system text.

- only for a candidate with ``server_fallbacks`` (Story 4.14, AD-8: the Opus
  ``grounding_check_fallback`` and, since Story 4.15, the batchable ``draft_sequence``):
  ``extra_headers`` ``{"anthropic-beta": "server-side-fallback-2026-07-01"}`` and
  ``extra_body`` ``{"fallbacks": "default"}``, so a refusal is retried server-side on the
  provider's fallback model. Every other request carries neither.

It never sets ``thinking``, ``temperature``, ``top_p``, ``top_k``, ``tools``, ``tool_choice``,
``stop_sequences`` or ``betas``, never sets ``fallbacks`` except as above, and never adds an
assistant prefill. A batch request (Story 4.17) never goes through the fallback keys.
"""

import re
from collections.abc import Sequence
from typing import Any, Final

from nextron.adapters.ai.routes import Candidate
from nextron.adapters.ai.schemas import wire_schema
from nextron.domain.ai import Framing, RouteTuple, UntrustedBlock, check_blocks, check_framing
from nextron.domain.playbook import playbook_section

# "<" and the characters that render like it.
BRACKETS: Final = "<＜﹤‹〈⟨〈"
WORD: Final = "untrusted"
FULLWIDTH_SMALL_A: Final = 0xFF41
FULLWIDTH_CAPITAL_A: Final = 0xFF21
FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"
FALLBACK_HEADERS: Final = {"anthropic-beta": FALLBACK_BETA}
FALLBACK_BODY: Final = {"fallbacks": "default"}


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


def _note(note: str) -> str:
    """The Campaign note that opens a framed user turn, or ``""`` for a blank note."""
    return f"<campaign_note>\n{escape(note)}\n</campaign_note>\n\n" if note.strip() else ""


def build_request(
    route_tuple: RouteTuple,
    candidate: Candidate,
    blocks: Sequence[UntrustedBlock],
    max_tokens: int,
    *,
    framing: Framing | None = None,
) -> dict[str, Any]:
    """The keyword arguments for ``messages.create``; see the module docstring."""
    check_framing(framing)
    texts = [route_tuple.system_prompt]
    if framing is not None and framing.brief.strip():
        texts.append(framing.brief)
    if route_tuple.playbook is not None:
        texts.append(playbook_section(route_tuple.playbook))
    if framing is not None and framing.facts.strip():
        texts.append(framing.facts)
    system: list[dict[str, Any]] = [{"type": "text", "text": text} for text in texts]
    system[-1]["cache_control"] = {"type": "ephemeral"}  # the cached prefix ends here
    content = user_turn(blocks)
    if framing is not None:
        content = _note(framing.note) + content
    request: dict[str, Any] = {
        "model": route_tuple.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_config": {
            "effort": route_tuple.effort.value,
            "format": {"type": "json_schema", "schema": wire_schema(candidate.output_schema)},
        },
    }
    if candidate.server_fallbacks:  # AD-8: an Opus direct call carries the refusal fallbacks
        request["extra_headers"] = dict(FALLBACK_HEADERS)
        request["extra_body"] = dict(FALLBACK_BODY)
    return request
