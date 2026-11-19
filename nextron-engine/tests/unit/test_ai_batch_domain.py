"""``nextron.domain.ai_batch`` (Story 4.17): the statuses, ``custom_id`` and its bounds, and the
canonical ``Fingerprint``; and ``chain_urgency``."""

import pytest

from nextron.adapters.ai.routes import CANDIDATES
from nextron.domain.ai import Urgency, chain_urgency
from nextron.domain.ai_batch import (
    CUSTOM_ID_PATTERN,
    DEFAULT_BULK_AI_SUBMIT_AT,
    TERMINAL,
    BatchOwner,
    BatchStatus,
    Fingerprint,
    ItemStatus,
    custom_id,
    parse_custom_id,
)
from nextron.domain.job import Lane

LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
KEY = "claude-haiku-5-5:medium:v1:s1:0123456789ab"


def test_the_statuses_and_owners() -> None:
    assert [owner.value for owner in BatchOwner] == ["drafting", "research"]
    assert [status.value for status in BatchStatus] == ["submitted", "ended", "deleted"]
    assert [status.value for status in ItemStatus] == [
        "pending",
        "submitted",
        "applied",
        "stale",
        "rerun",
        "failed",
    ]
    assert TERMINAL == {ItemStatus.APPLIED, ItemStatus.STALE, ItemStatus.RERUN, ItemStatus.FAILED}
    assert DEFAULT_BULK_AI_SUBMIT_AT.isoformat() == "14:00:00"


def test_a_custom_id_is_route_ulid_rev_and_parses_back() -> None:
    made = custom_id("extract_signals", LEAD, 3)

    assert made == f"extract_signals_{LEAD}_3"
    assert parse_custom_id(made) == ("extract_signals", LEAD, 3)


def test_the_longest_route_with_the_largest_rev_fits_the_api_limit() -> None:
    longest = max(CANDIDATES, key=len)
    made = custom_id(longest, LEAD, 10**12 - 1)

    assert len(made) <= 64
    assert CUSTOM_ID_PATTERN.fullmatch(made) is not None
    assert parse_custom_id(made) == (longest, LEAD, 10**12 - 1)


@pytest.mark.parametrize(
    ("route", "target", "rev"),
    [
        ("Extract", LEAD, 1),
        ("extract signals", LEAD, 1),
        ("extract_signals", "not-a-ulid", 1),
        ("extract_signals", LEAD, -1),
        ("extract_signals", LEAD, True),
        ("extract_signals", LEAD, "1"),
        ("x" * 40, LEAD, 1),  # a valid code, but the id would exceed 64 characters
    ],
)
def test_a_custom_id_refuses_bad_parts(route: str, target: str, rev: object) -> None:
    with pytest.raises(ValueError):
        custom_id(route, target, rev)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "extract_signals",
        f"extract_signals_{LEAD}",
        f"extract_signals_{LEAD}_01",
        f"extract_signals_{LEAD}_x",
        f"_{LEAD}_1",
        f"extract_signals_{LEAD.lower()}_1",
        f"extract_signals_{LEAD}_1 ",
        "a" * 65,
    ],
)
def test_parse_custom_id_refuses_anything_custom_id_would_not_make(text: str) -> None:
    with pytest.raises(ValueError):
        parse_custom_id(text)


def test_a_fingerprint_is_sorted_and_canonical() -> None:
    found = Fingerprint(KEY, (("p2", "bbb"), ("p1", "aaa"), ("p1", "aaa"), ("f1", 3)))

    assert found.inputs == (("f1", 3), ("p1", "aaa"), ("p2", "bbb"))
    assert found.encode() == (
        '{"inputs":[["f1",3],["p1","aaa"],["p2","bbb"]],'
        '"route_tuple":"claude-haiku-5-5:medium:v1:s1:0123456789ab"}'
    )
    assert Fingerprint.decode(found.encode()) == found
    assert found.prompt_version == "v1"
    assert Fingerprint(KEY, [("p1", "aaa")]) == Fingerprint(KEY, (("p1", "aaa"),))


@pytest.mark.parametrize(
    "inputs",
    [
        (("p1",),),
        (("", "aaa"),),
        (("p1", ""),),
        (("p1", -1),),
        (("p1", True),),
        (("p1", None),),
        "p1",
    ],
)
def test_a_fingerprint_refuses_bad_inputs(inputs: object) -> None:
    with pytest.raises(ValueError):
        Fingerprint(KEY, inputs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"inputs":[]}',
        '{"inputs":[],"route_tuple":"' + KEY + '","extra":1}',
        '{"inputs":[["p2","b"],["p1","a"]],"route_tuple":"' + KEY + '"}',  # not sorted
        '{"route_tuple": "' + KEY + '", "inputs": []}',  # not compact
        '{"inputs":[["p1","a"],"x"],"route_tuple":"' + KEY + '"}',
    ],
)
def test_decode_refuses_any_other_text(text: str) -> None:
    with pytest.raises(ValueError):
        Fingerprint.decode(text)


def test_a_fingerprint_needs_a_tuple_key() -> None:
    with pytest.raises(ValueError, match="route_tuple_key"):
        Fingerprint("", ())


@pytest.mark.parametrize(
    ("lane", "urgency"),
    [
        (Lane.INTERACTIVE, Urgency.SOON),
        (Lane.BULK, Urgency.BULK),
        (Lane.REALTIME, Urgency.BULK),
        (None, Urgency.BULK),
    ],
)
def test_chain_urgency_is_soon_only_on_the_interactive_lane(
    lane: Lane | None, urgency: Urgency
) -> None:
    assert chain_urgency(lane) is urgency
