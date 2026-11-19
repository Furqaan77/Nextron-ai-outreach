"""Message Batch bookkeeping (AD-8, AD-18, INT-7, FR-57; Story 4.17). Pure: the standard library
and ``nextron.domain`` only.

A bulk AI call on a batchable route is queued as an ``ai_batch_items`` row, sent with the others
of its owner and route in one Message Batch by the daily ``submit_bulk_ai`` job, and settled by
``poll_ai_batches``. The tables are shared: each row is written only for the owner its
``owner`` column names (``nextron.db.ai_batches``).

**Enums.** ``BatchOwner`` is ``drafting`` or ``research``. ``BatchStatus`` is ``submitted``,
``ended`` or ``deleted``. ``ItemStatus`` is ``pending`` (queued before the daily submit),
``submitted``, ``applied``, ``stale``, ``rerun`` or ``failed``; ``TERMINAL`` holds the last
four: a batch is deleted once every one of its items is terminal.

**``custom_id(route, target_id, rev)``** is ``<route>_<target ULID>_<rev>`` (AD-8). It raises
``ValueError`` unless ``route`` is a code (``budget.CODE_PATTERN``), ``target_id`` a ULID,
``rev`` a non-negative ``int`` (not a ``bool``) and the whole fullmatches
``CUSTOM_ID_PATTERN`` (``[A-Za-z0-9_-]{1,64}``, the API's limit). ``parse_custom_id(text)``
returns ``(route, target_id, rev)`` and raises ``ValueError`` for anything ``custom_id`` would
not have made.

**``Fingerprint(route_tuple_key, inputs)``** is what a batch item's request was built from: the
route tuple's key and the sorted ``(id, version-or-hash)`` pairs of its inputs (for research,
each page sent and its ``content_hash``). It is frozen; ``inputs`` is sorted and deduplicated at
construction, each id a non-empty ``str`` of at most 64 characters and each version a
non-empty ``str`` of at most 128 characters or a non-negative ``int``. ``encode()`` is its
canonical JSON (sorted keys, no spaces) and ``Fingerprint.decode(text)`` reads it back
(``ValueError`` for any other text). ``prompt_version`` is the tuple key's prompt version
(``RouteTuple.key`` is ``<model>:<effort>:<prompt_version>:...``). No fingerprint holds text:
only ids, hashes and versions.

``DEFAULT_BULK_AI_SUBMIT_AT`` (14:00) is the ``bulk_ai_submit_at`` setting's default and
``DEFAULT_DRAFT_DEADLINE`` (07:00) the ``draft_deadline`` setting's (Story 4.18);
``SUBMIT_BULK_AI_JOB`` and ``POLL_AI_BATCHES_JOB`` name the two job types, which share the
concurrency key ``CONCURRENCY_KEY``.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Final

from nextron.domain.budget import CODE_PATTERN, MODEL_PATTERN
from nextron.domain.ids import is_ulid

CUSTOM_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_INPUT_ID: Final = 64
MAX_INPUT_VERSION: Final = 128
KEY_PARTS: Final = 3  # model, effort, prompt version lead every RouteTuple.key
DEFAULT_BULK_AI_SUBMIT_AT: Final = time(14, 0)  # the bulk_ai_submit_at setting's default
DEFAULT_DRAFT_DEADLINE: Final = time(7, 0)  # the draft_deadline setting's default (Story 4.18)
SUBMIT_BULK_AI_JOB: Final = "submit_bulk_ai"
POLL_AI_BATCHES_JOB: Final = "poll_ai_batches"
CONCURRENCY_KEY: Final = "ai_batches"  # both jobs: one batch job at a time


@dataclass(frozen=True, slots=True)
class BulkAiSchedule:
    """When ``submit_bulk_ai`` runs (``at`` in ``timezone``), and whether a queued item of the
    owner missed the latest run (each batch owner's ``schedule()``; Story 4.18 moved it here
    from ``nextron.app.research.batches`` so both owners share it)."""

    at: time
    timezone: str
    missed: bool


class BatchOwner(StrEnum):
    """The service that owns a batch row and its items."""

    DRAFTING = "drafting"
    RESEARCH = "research"


class BatchStatus(StrEnum):
    """Where a Message Batch is."""

    SUBMITTED = "submitted"
    ENDED = "ended"
    DELETED = "deleted"


class ItemStatus(StrEnum):
    """Where one batch item is."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    APPLIED = "applied"
    STALE = "stale"
    RERUN = "rerun"
    FAILED = "failed"


TERMINAL: Final = frozenset(
    {ItemStatus.APPLIED, ItemStatus.STALE, ItemStatus.RERUN, ItemStatus.FAILED}
)


def _rev(rev: object) -> int:
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise ValueError("rev must be a non-negative int")
    return rev


def custom_id(route: str, target_id: str, rev: int) -> str:
    """``<route>_<target ULID>_<rev>``; see the module docstring."""
    if not isinstance(route, str) or CODE_PATTERN.fullmatch(route) is None:
        raise ValueError(f"route must match {CODE_PATTERN.pattern}")
    if not is_ulid(target_id):
        raise ValueError("target_id must be a ULID")
    made = f"{route}_{target_id}_{_rev(rev)}"
    if CUSTOM_ID_PATTERN.fullmatch(made) is None:
        raise ValueError(f"a custom_id must match {CUSTOM_ID_PATTERN.pattern}")
    return made


def parse_custom_id(text: str) -> tuple[str, str, int]:
    """``(route, target_id, rev)`` of a ``custom_id``; ``ValueError`` for anything else."""
    if not isinstance(text, str) or CUSTOM_ID_PATTERN.fullmatch(text) is None:
        raise ValueError(f"a custom_id must match {CUSTOM_ID_PATTERN.pattern}")
    parts = text.rsplit("_", 2)
    if len(parts) != 3 or not parts[2].isdigit() or not parts[2].isascii():  # noqa: PLR2004
        raise ValueError("a custom_id must be <route>_<ULID>_<rev>")
    route, target_id, rev = parts[0], parts[1], int(parts[2])
    if custom_id(route, target_id, rev) != text:  # leading zeros, a bad route or ULID
        raise ValueError("a custom_id must be <route>_<ULID>_<rev>")
    return route, target_id, rev


def _input(pair: object) -> tuple[str, str | int]:
    if not isinstance(pair, tuple | list) or len(pair) != 2:  # noqa: PLR2004
        raise ValueError("each fingerprint input must be an (id, version) pair")
    key, version = pair
    if not isinstance(key, str) or not 0 < len(key) <= MAX_INPUT_ID:
        raise ValueError(f"a fingerprint input's id must be 1 to {MAX_INPUT_ID} characters")
    if isinstance(version, bool):
        raise ValueError("a fingerprint input's version must be a str or an int")
    if isinstance(version, int):
        if version < 0:
            raise ValueError("a fingerprint input's version must not be negative")
    elif not isinstance(version, str) or not 0 < len(version) <= MAX_INPUT_VERSION:
        raise ValueError(
            f"a fingerprint input's version must be an int or 1 to {MAX_INPUT_VERSION} characters"
        )
    return key, version


def _order(pair: tuple[str, str | int]) -> tuple[str, int, str]:
    key, version = pair
    return (key, 0, str(version)) if isinstance(version, int) else (key, 1, version)


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What a batch item's request was built from; see the module docstring."""

    route_tuple_key: str
    inputs: tuple[tuple[str, str | int], ...] = ()

    def __post_init__(self) -> None:
        key = self.route_tuple_key
        if not isinstance(key, str) or MODEL_PATTERN.fullmatch(key) is None:
            raise ValueError(f"route_tuple_key must match {MODEL_PATTERN.pattern}")
        if isinstance(self.inputs, str | bytes) or not isinstance(self.inputs, Iterable):
            raise ValueError("inputs must be (id, version) pairs")
        pairs = sorted({_input(pair) for pair in self.inputs}, key=_order)
        object.__setattr__(self, "inputs", tuple(pairs))

    @property
    def prompt_version(self) -> str | None:
        """The tuple key's prompt version, or ``None`` when the key has no such part."""
        parts = self.route_tuple_key.split(":")
        return parts[KEY_PARTS - 1] if len(parts) >= KEY_PARTS else None

    def encode(self) -> str:
        """The canonical JSON: sorted keys, no spaces."""
        return json.dumps(
            {"inputs": [list(pair) for pair in self.inputs], "route_tuple": self.route_tuple_key},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @classmethod
    def decode(cls, text: str) -> "Fingerprint":
        """The fingerprint ``encode`` wrote; ``ValueError`` for any other text."""
        try:
            document = json.loads(text)
        except (TypeError, ValueError, RecursionError):
            raise ValueError("a fingerprint must be JSON") from None
        if not isinstance(document, dict) or set(document) != {"inputs", "route_tuple"}:
            raise ValueError("a fingerprint must hold exactly inputs and route_tuple")
        inputs = document["inputs"]
        if not isinstance(inputs, list):
            raise ValueError("a fingerprint's inputs must be a list")
        pairs = tuple(tuple(pair) for pair in inputs if isinstance(pair, list))
        found = cls(document["route_tuple"], pairs)  # type: ignore[arg-type]
        if len(found.inputs) != len(inputs) or found.encode() != text:
            raise ValueError("a fingerprint must be in its canonical encoding")
        return found
