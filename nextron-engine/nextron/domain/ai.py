"""The values of a Claude call (AD-8, AD-18, INT-7, FR-57). Standard library only.

Services, Story 2.8's queries and the AI gateway (``nextron.adapters.ai``) share these. Every
construction check raises ``TypeError`` or ``ValueError`` that names the field, never its value.

**Enums.** ``Urgency`` is ``interactive``, ``soon`` or ``bulk``: how soon the caller needs the
answer. Only a ``bulk`` call on a batchable route goes through a Message Batch (Story 4.17); the
service decides that, and every ``run`` is one direct Messages request. ``chain_urgency(lane)``
(Story 2.16, Story 4.17) is the urgency of a research chain's AI call made by a job on ``lane``:
``soon`` on the interactive lane, ``bulk`` on any other lane or none.
``Effort`` is ``low``, ``medium``, ``high``, ``xhigh`` or ``max``, the API's
``output_config.effort``.

**``RouteTuple(route, model, effort, prompt_version, schema_version, system_prompt,
playbook=None)``** names exactly what one call runs. It is frozen, and ``system_prompt`` never
shows in its ``repr``.

- ``route`` fullmatches ``nextron.domain.budget.CODE_PATTERN``;
- ``model`` fullmatches ``[a-z0-9][a-z0-9.-]{0,63}``;
- ``effort`` is an ``Effort``;
- ``prompt_version`` fullmatches ``v[1-9][0-9]{0,3}``;
- ``schema_version`` is an ``int`` (not a ``bool``) from 1 to 9,999;
- ``system_prompt`` is a non-blank ``str`` (not only whitespace) that encodes as UTF-8;
- ``playbook`` (Story 4.4) is the pinned ``nextron.domain.playbook.PlaybookSnapshot`` a Playbook
  route runs under, or ``None`` (any other type is a ``TypeError``). The gateway frames its text
  after the system prompt (``playbook_section``); the tuple never holds the live file.

``prompt_sha256`` is the hex SHA-256 of the system prompt's UTF-8 (``text_sha256``), so the
text and its hash can never disagree; ``playbook_sha256`` is the Playbook snapshot's, or
``None`` without one. ``key`` is
``<model>:<effort>:<prompt_version>:s<schema_version>:<the first 12 hex of prompt_sha256>``,
followed by ``:p<the first 12 hex of playbook_sha256>`` only when there is a Playbook (so
``ai_usage`` tells Playbook versions apart, and extraction keys are unchanged): it fits
``budget.MODEL_PATTERN`` and is what ``ai_usage.route_tuple`` stores. ``actor`` is
``ai:<route>@<prompt_version>``: it
fullmatches ``nextron.db.eventlog.ACTOR_PATTERN``, and it is what every stored result of the
call records as its ``extracted_by`` or actor (Stories 2.9 onwards).

**``RouteSpec(route, model, effort, prompt_version, schema_version, uses_playbook=False)``**
(Story 2.8) is a route candidate as data, without the prompt's text: what
``Playbook.sync_prompts`` activates or records as a candidate tuple. It is frozen and checked by
``check_route_fields``; ``uses_playbook`` (Story 4.4) must be a ``bool``, and a route that uses
the Playbook runs under a pinned snapshot and is never bootstrap-activated.

**``UntrustedBlock(subject, source, block_id, text)``** is one piece of untrusted text (a
crawled page, a pasted Signal Inbox item) for the user turn. It is frozen, and ``text`` never
shows in its ``repr``.

- ``subject`` fullmatches ``[a-z][a-z_]{0,31}:[A-Za-z0-9]{1,64}``: the Lead (``lead:<id>``), or
  the Signal Inbox item that has no Lead yet (``signal_item:<id>``);
- ``source`` fullmatches ``[a-z][a-z0-9_]{0,31}``;
- ``block_id`` fullmatches ``[A-Za-z0-9][A-Za-z0-9_.-]{0,63}``;
- ``text`` is a ``str`` that encodes as UTF-8.

**``Framing(brief, facts, note)``** (Story 4.15, AD-18) is the trusted text a drafting call
adds around the route's prompt: ``brief`` (the Step brief section) and ``facts`` (the allowed
Approved Company Facts section) join the cached system prompt, and ``note`` (the Campaign note:
the Variant's angle and the Market's English variant) opens the user turn. It is frozen; each
field is a ``str`` that encodes as UTF-8 and never shows in its ``repr``. A call without one is
built exactly as before. ``instruction`` (Story 4.20, default ``""``) is the Operator's
regenerate instruction: the request appends it after the untrusted blocks, in an
``<operator_instruction>`` block, and only a candidate that ``accepts_instruction`` may carry a
non-blank one.

**Checks the gateway shares** (an adapter raises only ``AdapterError``, so a caller's bug is
raised here):

- ``check_blocks(blocks)`` returns the one subject of a call's blocks. It raises ``TypeError``
  unless ``blocks`` is a sequence of ``UntrustedBlock``, and ``ValueError`` when there is no
  block, when the blocks name more than one subject, or when a ``block_id`` repeats.
- ``check_urgency(urgency)`` returns ``urgency``, or raises ``TypeError`` unless it is an
  ``Urgency``; ``check_framing(framing)`` (Story 4.15) returns ``framing``, or raises
  ``TypeError`` unless it is a ``Framing`` or ``None``; ``check_accepts_instruction(value)``
  (Story 4.20) raises ``TypeError`` unless ``value`` is a ``bool``, and
  ``check_instruction_allowed(accepts_instruction, framing, *, batched=False)`` raises
  ``ValueError`` for a non-blank ``Framing.instruction`` on a candidate that doesn't accept one,
  or on any batch request.
- ``check_route_fields(route, model, effort, prompt_version, schema_version)`` checks the
  fields a ``RouteTuple`` and a route candidate share, and ``check_uses_playbook(value)`` that a
  candidate's ``uses_playbook`` is a ``bool`` (Story 4.4).
- ``check_server_fallbacks(value, model, batchable, fallback_model)`` (Story 4.14) that a
  candidate's ``server_fallbacks`` is a ``bool`` and ``True`` only on ``fallback_model``. Since
  Story 4.15 a batchable Opus candidate may carry them too (``draft_sequence``): only the direct
  request (``prompting.build_request``) adds them, and a batch request (Story 4.17) never does.
- ``check_token_limits(max_tokens, max_tokens_cap, ceiling)`` raises unless both are ints (not
  bools) and ``0 < max_tokens <= max_tokens_cap <= ceiling``.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from nextron.domain.budget import CODE_PATTERN
from nextron.domain.job import Lane
from nextron.domain.playbook import SHORT_SHA_LENGTH, PlaybookSnapshot

MODEL_NAME_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}")
PROMPT_VERSION_PATTERN: Final = re.compile(r"v[1-9][0-9]{0,3}")
MAX_SCHEMA_VERSION: Final = 9_999
SUBJECT_PATTERN: Final = re.compile(r"[a-z][a-z_]{0,31}:[A-Za-z0-9]{1,64}")
SOURCE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
BLOCK_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
PROMPT_HASH_PREFIX: Final = 12  # hex digits of prompt_sha256 in a RouteTuple's key
PLAYBOOK_HASH_PREFIX: Final = SHORT_SHA_LENGTH  # hex digits of playbook_sha256 in the key


class Urgency(StrEnum):
    """How soon the caller needs the answer."""

    INTERACTIVE = "interactive"
    SOON = "soon"
    BULK = "bulk"


def chain_urgency(lane: str | None) -> Urgency:
    """The urgency of a research chain's AI call made by a job on ``lane`` (Story 2.16): ``soon``
    on the interactive lane (a Signal Inbox chain), ``bulk`` on any other lane or none."""
    return Urgency.SOON if lane == Lane.INTERACTIVE else Urgency.BULK


class Effort(StrEnum):
    """The API's ``output_config.effort``."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# Checks ---------------------------------------------------------------------------------------


def _text(name: str, value: object, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{name} must match {pattern.pattern}")


def _int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _utf8(name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must encode as UTF-8") from None


def check_route_fields(
    route: object, model: object, effort: object, prompt_version: object, schema_version: object
) -> None:
    """Raise unless the fields a route tuple and a route candidate share are valid."""
    _text("route", route, CODE_PATTERN)
    _text("model", model, MODEL_NAME_PATTERN)
    if not isinstance(effort, Effort):
        raise TypeError("effort must be an Effort")
    _text("prompt_version", prompt_version, PROMPT_VERSION_PATTERN)
    if not 1 <= _int("schema_version", schema_version) <= MAX_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be from 1 to {MAX_SCHEMA_VERSION:,}")


def check_uses_playbook(uses_playbook: object) -> None:
    """Raise ``TypeError`` unless a route candidate's ``uses_playbook`` is a ``bool``."""
    if not isinstance(uses_playbook, bool):
        raise TypeError("uses_playbook must be a bool")


def check_server_fallbacks(
    server_fallbacks: object, model: str, batchable: bool, fallback_model: str
) -> None:
    """Raise unless a route candidate's ``server_fallbacks`` (Story 4.14) is a ``bool`` that is
    ``True`` only on ``fallback_model`` (the one model with server-side refusal fallbacks). A
    batchable candidate may carry them (Story 4.15): only its direct request adds them."""
    if not isinstance(server_fallbacks, bool):
        raise TypeError("server_fallbacks must be a bool")
    if server_fallbacks and model != fallback_model:
        raise ValueError("only an Opus candidate carries server-side fallbacks")
    if not isinstance(batchable, bool):
        raise TypeError("batchable must be a bool")
    # Story 4.15: a batchable Opus candidate may carry them; only its direct request does.


def check_token_limits(max_tokens: object, max_tokens_cap: object, ceiling: int) -> None:
    """Raise unless ``0 < max_tokens <= max_tokens_cap <= ceiling``."""
    tokens = _int("max_tokens", max_tokens)
    cap = _int("max_tokens_cap", max_tokens_cap)
    if tokens <= 0:
        raise ValueError("max_tokens must be above 0")
    if tokens > cap:
        raise ValueError("max_tokens must be at most max_tokens_cap")
    if cap > ceiling:
        raise ValueError(f"max_tokens_cap must be at most {ceiling:,}")


def text_sha256(text: str) -> str:
    """The hex SHA-256 of ``text``'s UTF-8: a ``RouteTuple``'s ``prompt_sha256``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_urgency(urgency: object) -> Urgency:
    """``urgency``, or ``TypeError`` unless it is an ``Urgency``."""
    if not isinstance(urgency, Urgency):
        raise TypeError("urgency must be an Urgency")
    return urgency


def check_accepts_instruction(value: object) -> bool:
    """``value``, or ``TypeError`` unless it is a ``bool`` (Story 4.20)."""
    if not isinstance(value, bool):
        raise TypeError("accepts_instruction must be a bool")
    return value


def check_instruction_allowed(
    accepts_instruction: bool, framing: object, *, batched: bool = False
) -> None:
    """``ValueError`` when ``framing`` carries a non-blank Operator instruction that a request
    may not: a candidate without ``accepts_instruction``, or any batch request (Story 4.20)."""
    instruction = getattr(framing, "instruction", "")
    if not isinstance(instruction, str) or not instruction.strip():
        return
    if batched:
        raise ValueError("an Operator instruction is never batched")
    if not accepts_instruction:
        raise ValueError("this route candidate takes no Operator instruction")


def check_framing(framing: object) -> "Framing | None":
    """``framing``, or ``TypeError`` unless it is a ``Framing`` or ``None``."""
    if framing is not None and not isinstance(framing, Framing):
        raise TypeError("framing must be a Framing or None")
    return framing


# Values ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouteTuple:
    """Exactly what one call runs: route, model, effort, prompt and schema versions, and the
    system prompt's text."""

    route: str
    model: str
    effort: Effort
    prompt_version: str
    schema_version: int
    system_prompt: str = field(repr=False)
    playbook: PlaybookSnapshot | None = None

    def __post_init__(self) -> None:
        check_route_fields(
            self.route, self.model, self.effort, self.prompt_version, self.schema_version
        )
        _utf8("system_prompt", self.system_prompt)
        if not self.system_prompt.strip():
            raise ValueError("system_prompt must not be blank")
        if self.playbook is not None and not isinstance(self.playbook, PlaybookSnapshot):
            raise TypeError("playbook must be a PlaybookSnapshot or None")

    @property
    def prompt_sha256(self) -> str:
        """The hex SHA-256 of the system prompt's UTF-8."""
        return text_sha256(self.system_prompt)

    @property
    def playbook_sha256(self) -> str | None:
        """The pinned Playbook snapshot's SHA-256, or ``None`` without one."""
        return None if self.playbook is None else self.playbook.sha256

    @property
    def key(self) -> str:
        """What ``ai_usage.route_tuple`` stores; see the module docstring."""
        key = (
            f"{self.model}:{self.effort.value}:{self.prompt_version}:s{self.schema_version}:"
            f"{self.prompt_sha256[:PROMPT_HASH_PREFIX]}"
        )
        if self.playbook is not None:
            key += f":p{self.playbook.sha256[:PLAYBOOK_HASH_PREFIX]}"
        return key

    @property
    def actor(self) -> str:
        """``ai:<route>@<prompt_version>``: what a stored result of the call records."""
        return f"ai:{self.route}@{self.prompt_version}"


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """A route candidate as data: a ``RouteTuple`` without the system prompt's text."""

    route: str
    model: str
    effort: Effort
    prompt_version: str
    schema_version: int
    uses_playbook: bool = False

    def __post_init__(self) -> None:
        check_route_fields(
            self.route, self.model, self.effort, self.prompt_version, self.schema_version
        )
        check_uses_playbook(self.uses_playbook)


@dataclass(frozen=True, slots=True)
class UntrustedBlock:
    """One piece of untrusted text for the user turn, and the subject it belongs to."""

    subject: str
    source: str
    block_id: str
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _text("subject", self.subject, SUBJECT_PATTERN)
        _text("source", self.source, SOURCE_PATTERN)
        _text("block_id", self.block_id, BLOCK_ID_PATTERN)
        _utf8("text", self.text)


def check_blocks(blocks: Sequence[UntrustedBlock]) -> str:
    """The one subject of ``blocks``; see the module docstring."""
    if isinstance(blocks, str | bytes) or not isinstance(blocks, Sequence):
        raise TypeError("blocks must be a sequence of UntrustedBlock")
    if not all(isinstance(block, UntrustedBlock) for block in blocks):
        raise TypeError("blocks must be a sequence of UntrustedBlock")
    if not blocks:
        raise ValueError("blocks must hold at least one block")
    subjects = {block.subject for block in blocks}
    if len(subjects) != 1:
        raise ValueError("blocks must all belong to one subject")
    ids = [block.block_id for block in blocks]
    if len(set(ids)) != len(ids):
        raise ValueError("blocks must not repeat a block_id")
    return subjects.pop()


@dataclass(frozen=True, slots=True)
class Framing:
    """The trusted text a drafting call adds around the route's prompt (Story 4.15); see the
    module docstring."""

    brief: str = field(repr=False)
    facts: str = field(repr=False)
    note: str = field(repr=False)
    instruction: str = field(default="", repr=False)  # Story 4.20: the Operator's instruction

    def __post_init__(self) -> None:
        _utf8("brief", self.brief)
        _utf8("facts", self.facts)
        _utf8("note", self.note)
        _utf8("instruction", self.instruction)
