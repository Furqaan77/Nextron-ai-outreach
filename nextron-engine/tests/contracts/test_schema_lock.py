"""The schema lock (FR-71, AD-8, AD-12, Story 4.5, decision 9): a used output schema version
never changes.

A route tuple names its ``schema_version`` and Story 4.5's gate tests a tuple against it, so the
schema a version names must stay what was tested. ``SCHEMA_SHA256`` pins the SHA-256 of each
shipped output schema's ``json_schema`` (canonical JSON: sorted keys, compact, UTF-8), by
``(route, schema_version)``. Editing a shipped schema in place fails this test with "a used
schema version never changes: add a new version"; a new version adds its pin here (add-only),
and a shipped candidate whose schema has no pin fails too.

The risk the lock leaves (decision 9): someone could edit a schema and its pin together. Storing
``schema_sha256`` on tuples and activations would need an AD-8 amendment only Mac can approve.
"""

import hashlib
import json
from typing import Final

from nextron.adapters.ai import routes
from nextron.adapters.ai.schemas import OutputSchema, json_schema

NEVER_CHANGES: Final = "a used schema version never changes: add a new version"
SCHEMA_SHA256: Final[dict[tuple[str, int], str]] = {
    ("extract_signals", 1): "ad078b705ddc91e884d9f2ad707e00f8d878f64aae50814d25bfb83066fa1f99",
    ("extract_people", 1): "2ec227d8365c189eb87a9f14a022e7b5fea4ff9eed662fcf5d35a3793f81b1cc",
    ("signal_inbox_parse", 1): "e1eaa2b871a11c212621d69d7bc6bc3cbfe031f7ff4f07b4e9e1b852fb50fd91",
}


def schema_sha256(schema: OutputSchema) -> str:
    """The SHA-256 of ``schema``'s ``json_schema`` as canonical JSON."""
    text = json.dumps(
        json_schema(schema), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shipped() -> dict[tuple[str, int], str]:
    """Each shipped candidate's ``(route, schema_version)`` and its schema's SHA-256."""
    found: dict[tuple[str, int], str] = {}
    for route, candidates in routes.CANDIDATES.items():
        for candidate in candidates:
            key = (route, candidate.output_schema.version)
            sha = schema_sha256(candidate.output_schema)
            assert found.setdefault(key, sha) == sha, f"{key}: {NEVER_CHANGES}"
    return found


def lock_problems(found: dict[tuple[str, int], str]) -> list[str]:
    """What breaks the lock: an unpinned schema, or one whose SHA-256 changed."""
    problems = []
    for key, sha in sorted(found.items()):
        pinned = SCHEMA_SHA256.get(key)
        if pinned is None:
            problems.append(f"{key[0]} schema {key[1]} has no pin in SCHEMA_SHA256")
        elif pinned != sha:
            problems.append(f"{key[0]} schema {key[1]}: {NEVER_CHANGES}")
    return problems


def test_every_shipped_schema_version_is_pinned_and_unchanged() -> None:
    assert lock_problems(shipped()) == []


def test_the_lock_catches_an_edited_schema_and_an_unpinned_one() -> None:
    found = shipped()
    edited = {**found, ("extract_people", 1): "0" * 64, ("draft_sequence", 1): "1" * 64}

    assert lock_problems(edited) == [
        "draft_sequence schema 1 has no pin in SCHEMA_SHA256",
        f"extract_people schema 1: {NEVER_CHANGES}",
    ]


def test_the_pins_are_add_only() -> None:
    assert set(SCHEMA_SHA256) <= set(shipped())  # a pin never outlives its shipped candidate
