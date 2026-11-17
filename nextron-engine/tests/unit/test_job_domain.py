"""The Job machine and the rules of durable jobs (``nextron/domain/job.py``, AD-4, AD-10)."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.job import (
    CREATION,
    DONE_RETENTION,
    FIRST_RETRY_DELAY,
    LEASE_MARGIN,
    LIVE_STATUSES,
    MAX_BULK_THREADS,
    MAX_ERROR_LENGTH,
    MAX_RETRY_DELAY,
    RENEW_INTERVAL,
    TRANSITIONS,
    InvalidJob,
    Job,
    JobCause,
    JobEvent,
    JobStatus,
    JobType,
    JobTypes,
    Lane,
    UnknownJobType,
    after_failure,
    after_interruption,
    decode_payload,
    encode_payload,
    idempotency_key,
    is_concurrency_key,
    lease_until,
    retry_delay,
    transition,
)
from nextron.domain.machines import IllegalTransition, Transition

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
PLANTED = "mac@example.com"


def handler(job: Job) -> None:
    del job


def job_type(**overrides: Any) -> JobType:
    fields: dict[str, Any] = {
        "name": "enrich_lead",
        "default_lane": Lane.BULK,
        "lanes": frozenset({Lane.BULK}),
        "priority": 50,
        "max_runtime": timedelta(minutes=5),
        "retry_limit": 3,
        "handler": handler,
    }
    return JobType(**{**fields, **overrides})


# The machine ------------------------------------------------------------------------------------

EXPECTED_TABLE = {
    ("queued", "claim"): ("running", "claimed"),
    ("running", "succeed"): ("done", "succeeded"),
    ("running", "retry"): ("queued", "retry_scheduled"),
    ("running", "fail"): ("dead", "failed"),
    ("running", "exhaust"): ("dead", "retries_exhausted"),
    ("running", "expire"): ("queued", "lease_expired"),
    ("running", "expire_exhausted"): ("dead", "lease_expired"),
    ("running", "release"): ("queued", "released"),
    ("running", "release_exhausted"): ("dead", "released"),
}


def test_the_enums_are_exactly_the_specified_values() -> None:
    assert [lane.value for lane in Lane] == ["realtime", "interactive", "bulk"]
    assert [status.value for status in JobStatus] == ["queued", "running", "done", "dead"]
    assert LIVE_STATUSES == (JobStatus.QUEUED, JobStatus.RUNNING)
    assert {event.value for event in JobEvent} == {event for _, event in EXPECTED_TABLE}
    assert [cause.value for cause in JobCause] == [
        "enqueued",
        "claimed",
        "succeeded",
        "retry_scheduled",
        "failed",
        "retries_exhausted",
        "lease_expired",
        "released",
    ]


def test_the_transition_table_is_exactly_the_specified_one() -> None:
    table = {
        (str(status), str(event)): (str(found.to), str(found.cause))
        for (status, event), found in TRANSITIONS.items()
    }

    assert table == EXPECTED_TABLE
    assert isinstance(TRANSITIONS, MappingProxyType)
    assert CREATION == Transition(JobStatus.QUEUED, JobCause.ENQUEUED)
    for (status, event), found in TRANSITIONS.items():
        assert transition(status, event) is found


def test_the_transition_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        TRANSITIONS[(JobStatus.DONE, JobEvent.CLAIM)] = CREATION  # type: ignore[index]


def test_a_transition_is_frozen() -> None:
    with pytest.raises(AttributeError):
        CREATION.to = JobStatus.DEAD  # type: ignore[misc]


def illegal_pairs() -> list[tuple[JobStatus, JobEvent]]:
    return [
        (status, event)
        for status in JobStatus
        for event in JobEvent
        if (str(status), str(event)) not in EXPECTED_TABLE
    ]


@pytest.mark.parametrize(("status", "event"), illegal_pairs())
def test_every_other_pair_is_an_illegal_transition(status: JobStatus, event: JobEvent) -> None:
    with pytest.raises(IllegalTransition) as raised:
        transition(status, event)

    assert raised.value.code == "illegal_transition"
    assert str(raised.value) == f"job: {event} is not allowed from {status}"
    assert (raised.value.machine, raised.value.event, raised.value.status) == (
        "job",
        str(event),
        str(status),
    )


def test_done_and_dead_are_final() -> None:
    for status in (JobStatus.DONE, JobStatus.DEAD):
        for event in JobEvent:
            with pytest.raises(IllegalTransition):
                transition(status, event)


def test_an_unknown_or_unhashable_argument_is_an_illegal_transition() -> None:
    with pytest.raises(IllegalTransition):
        transition("paused", JobEvent.CLAIM)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        transition([], JobEvent.CLAIM)  # type: ignore[arg-type]


def test_illegal_transition_is_a_domain_error() -> None:
    assert issubclass(IllegalTransition, DomainError)


# Constants, leases and backoff ------------------------------------------------------------------


def test_the_constants() -> None:
    assert LEASE_MARGIN == timedelta(seconds=60)
    assert RENEW_INTERVAL == timedelta(seconds=30)
    assert FIRST_RETRY_DELAY == timedelta(seconds=30)
    assert MAX_RETRY_DELAY == timedelta(minutes=30)
    assert DONE_RETENTION == timedelta(days=30)
    assert (MAX_BULK_THREADS, MAX_ERROR_LENGTH) == (2, 500)


def test_a_lease_lasts_the_runtime_plus_60_seconds() -> None:
    assert lease_until(NOW, timedelta(minutes=5)) == NOW + timedelta(minutes=6)


@pytest.mark.parametrize(
    ("attempts", "seconds"),
    [(1, 30), (2, 60), (3, 120), (4, 240), (5, 480), (6, 960), (7, 1800), (8, 1800), (500, 1800)],
)
def test_the_retry_delay_doubles_from_30_seconds_to_a_30_minute_cap(
    attempts: int, seconds: int
) -> None:
    assert retry_delay(attempts) == timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("attempts", "error"), [(0, ValueError), (True, TypeError), ("1", TypeError)]
)
def test_the_retry_delay_needs_a_claim_count(attempts: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        retry_delay(attempts)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("attempts", "limit", "retryable", "event"),
    [
        (1, 3, False, JobEvent.FAIL),
        (4, 3, False, JobEvent.FAIL),
        (1, 3, True, JobEvent.RETRY),
        (3, 3, True, JobEvent.RETRY),
        (4, 3, True, JobEvent.EXHAUST),
        (1, 0, True, JobEvent.EXHAUST),
        (1, 0, False, JobEvent.FAIL),
    ],
)
def test_after_failure(attempts: int, limit: int, retryable: bool, event: JobEvent) -> None:
    assert after_failure(attempts, limit, retryable=retryable) is event


@pytest.mark.parametrize(
    ("attempts", "limit", "released", "event"),
    [
        (1, 3, True, JobEvent.RELEASE),
        (3, 3, True, JobEvent.RELEASE),
        (4, 3, True, JobEvent.RELEASE_EXHAUSTED),
        (1, 0, True, JobEvent.RELEASE_EXHAUSTED),
        (1, 3, False, JobEvent.EXPIRE),
        (3, 3, False, JobEvent.EXPIRE),
        (4, 3, False, JobEvent.EXPIRE_EXHAUSTED),
        (1, 0, False, JobEvent.EXPIRE_EXHAUSTED),
    ],
)
def test_after_interruption(attempts: int, limit: int, released: bool, event: JobEvent) -> None:
    assert after_interruption(attempts, limit, released=released) is event


# Idempotency keys -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "discriminator"),
    [
        (ULID, "3"),
        (ULID, "0"),
        (ULID, "999999999"),
        (f"inbox:{ULID}", "tick"),
        ("global", "2026-11-17"),
        ("global", "2028-02-29"),
    ],
)
def test_a_key_is_type_scope_and_discriminator(scope: str, discriminator: str) -> None:
    assert idempotency_key("enrich_lead", scope, discriminator) == (
        f"enrich_lead:{scope}:{discriminator}"
    )


@pytest.mark.parametrize(
    ("job_type_name", "scope", "discriminator", "part"),
    [
        ("enrich", ULID, "3", "job type"),
        ("Enrich_lead", ULID, "3", "job type"),
        ("enrich_lead_" + "x" * 37, ULID, "3", "job type"),
        (None, ULID, "3", "job type"),
        ("enrich_lead", "x", "3", "scope"),
        ("enrich_lead", ULID.lower(), "3", "scope"),
        ("enrich_lead", f"inbox:{ULID.lower()}", "3", "scope"),
        ("enrich_lead", "inbox:global", "3", "scope"),
        ("enrich_lead", f"lead:{ULID}", "3", "scope"),
        ("enrich_lead", PLANTED, "3", "scope"),
        ("enrich_lead", 7, "3", "scope"),
        ("enrich_lead", ULID, "v2", "discriminator"),
        ("enrich_lead", ULID, "03", "discriminator"),
        ("enrich_lead", ULID, "1000000000", "discriminator"),
        ("enrich_lead", ULID, "-1", "discriminator"),
        ("enrich_lead", ULID, "2026-02-30", "discriminator"),
        ("enrich_lead", ULID, "2026-11-17T09:00:00Z", "discriminator"),
        ("enrich_lead", ULID, "Tick", "discriminator"),
        ("enrich_lead", ULID, "", "discriminator"),
        ("enrich_lead", ULID, 3, "discriminator"),
        ("enrich_lead", ULID, PLANTED, "discriminator"),
    ],
)
def test_a_key_of_the_wrong_shape_is_refused(
    job_type_name: object, scope: object, discriminator: object, part: str
) -> None:
    with pytest.raises(InvalidJob) as raised:
        idempotency_key(job_type_name, scope, discriminator)  # type: ignore[arg-type]

    assert raised.value.code == "invalid_job"
    assert part in raised.value.reason
    assert PLANTED not in str(raised.value)


# Concurrency keys -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        f"inbox:{ULID}",
        "domain:example.com",
        "domain:a.b",
        "domain:mail-1.example.co.uk",
        "domain:" + "a" * 249 + ".com",  # a 253-character name
    ],
)
def test_a_concurrency_key_is_an_inbox_or_a_domain(value: str) -> None:
    assert is_concurrency_key(value)


@pytest.mark.parametrize(
    "value",
    [
        "inbox:",
        f"inbox:{ULID.lower()}",
        "domain:example",
        "domain:Example.com",
        "domain:example..com",
        "domain:.example.com",
        "domain:example.com.",
        "domain:exa_mple.com",
        "domain:" + "a" * 250 + ".com",  # a 254-character name
        f"lead:{ULID}",
        "example.com",
        PLANTED,
        "",
        None,
        7,
    ],
)
def test_anything_else_is_not_a_concurrency_key(value: object) -> None:
    assert not is_concurrency_key(value)


# Payloads ---------------------------------------------------------------------------------------

GOOD_PAYLOAD: dict[str, object] = {
    "v": 1,
    "lead_id": ULID,
    "count": -(2**63),
    "big": 2**63 - 1,
    "zero": 0,
    "flag": True,
    "off": False,
    "day": "2026-11-17",
    "at": "2026-11-17T09:00:00Z",
    "at_micro": "2026-11-17T09:00:00.123456Z",
    "kind": "places_text:v2.1-a",
}


def test_a_payload_encodes_as_compact_json_with_sorted_keys() -> None:
    text = encode_payload(GOOD_PAYLOAD)

    assert text == json.dumps(GOOD_PAYLOAD, separators=(",", ":"), sort_keys=True)
    assert " " not in text
    assert list(json.loads(text)) == sorted(GOOD_PAYLOAD)


def test_a_payload_decodes_to_a_read_only_mapping() -> None:
    decoded = decode_payload(encode_payload(GOOD_PAYLOAD))

    assert dict(decoded) == GOOD_PAYLOAD
    assert isinstance(decoded, MappingProxyType)
    with pytest.raises(TypeError):
        decoded["v"] = 2  # type: ignore[index]


def test_the_smallest_payload_is_its_version() -> None:
    assert encode_payload({"v": 1}) == '{"v":1}'
    assert encode_payload(MappingProxyType({"v": 999})) == '{"v":999}'


def test_thirty_two_fields_are_allowed() -> None:
    payload: dict[str, object] = {"v": 1, **{f"f{n}": n for n in range(31)}}

    assert len(decode_payload(encode_payload(payload))) == 32


REFUSED_PAYLOADS: list[tuple[object, str]] = [
    ({"v": 1, "who": "a@b.co"}, "who"),
    ({"v": 1, "who": PLANTED}, "who"),
    ({"v": 1, "note": "a b"}, "note"),
    ({"v": 1, "note": " a"}, "note"),
    ({"v": 1, "ratio": 1.5}, "ratio"),
    ({"v": 1, "ratio": float("nan")}, "ratio"),
    ({"v": 1, "missing": None}, "missing"),
    ({"v": 1, "items": [1]}, "items"),
    ({"v": 1, "nested": {"v": 1}}, "nested"),
    ({"v": 1, "huge": 2**63}, "huge"),
    ({"v": 1, "tiny": -(2**63) - 1}, "tiny"),
    ({"v": 1, "empty": ""}, "empty"),
    ({"v": 1, "token": "x" * 65}, "token"),
    ({"v": 1, "upper": "Places"}, "upper"),
    ({"v": 1, "day": "2026-02-30T00:00:00Z"}, "day"),
    ({"v": 1, "at": "2026-11-17T09:00:00+00:00"}, "at"),
    ({"v": 1, "at": "2026-11-17T25:00:00Z"}, "at"),
    ({"v": 1, "at": "2026-11-17T09:00:00.12Z"}, "at"),
    ({"v": 1, "raw": b"x"}, "raw"),
    ({"lead_id": ULID}, "version field v"),
    ({"v": 0}, "field v"),
    ({"v": 1000}, "field v"),
    ({"v": True}, "field v"),
    ({"v": "1"}, "field v"),
    ({"v": 1.0}, "field v"),
    ({"v": 1, "Lead": 1}, "field name"),
    ({"v": 1, "_lead": 1}, "field name"),
    ({"v": 1, "x" * 33: 1}, "field name"),
    ({"v": 1, PLANTED: 1}, "field name"),
    ({"v": 1, 7: 1}, "field name"),
    ({"v": 1, **{f"f{n}": n for n in range(32)}}, "32 fields"),
    ([("v", 1)], "JSON object"),
    ("v=1", "JSON object"),
]


@pytest.mark.parametrize(("payload", "named"), REFUSED_PAYLOADS)
def test_a_payload_outside_the_rules_is_refused_by_name(payload: object, named: str) -> None:
    with pytest.raises(InvalidJob) as raised:
        encode_payload(payload)  # type: ignore[arg-type]

    assert named in raised.value.reason
    assert raised.value.code == "invalid_job"
    for value in payload.values() if isinstance(payload, Mapping) else ():
        if isinstance(value, str) and len(value) > 2:
            assert value not in str(raised.value)
    assert PLANTED not in str(raised.value)


def json_texts() -> list[tuple[str, str]]:
    """Each refused payload that JSON can hold, as the text a garbled row might store."""
    texts = []
    for payload, named in REFUSED_PAYLOADS:
        if not isinstance(payload, dict):
            continue
        try:
            texts.append((json.dumps({str(key): value for key, value in payload.items()}), named))
        except TypeError:  # bytes
            continue
    return texts


@pytest.mark.parametrize(("text", "named"), json_texts())
def test_decoding_applies_the_same_rules(text: str, named: str) -> None:

    with pytest.raises(InvalidJob) as raised:
        decode_payload(text)

    assert named in raised.value.reason
    assert PLANTED not in str(raised.value)


def test_the_largest_payload_fits_and_a_longer_text_is_refused() -> None:
    payload: dict[str, object] = {"v": 1, **{f"f{'x' * 29}{n:02}": "y" * 64 for n in range(31)}}

    assert len(encode_payload(payload).encode()) <= 4096
    assert decode_payload('{"v":1' + " " * 4089 + "}") == {"v": 1}  # 4,096 bytes
    with pytest.raises(InvalidJob, match="exceeds 4,096 bytes"):
        decode_payload('{"v":1' + " " * 4090 + "}")


def test_a_deeply_nested_stored_payload_is_refused() -> None:
    with pytest.raises(InvalidJob):
        decode_payload('{"v":1,"x":' + "[" * 2000 + "]" * 2000 + "}")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "not valid JSON"),
        ("{", "not valid JSON"),
        ('{"v":1,"v":2}', "repeats a field"),
        ('{"v":1} x', "not valid JSON"),
        ('{"v":NaN}', "field v"),
        ('{"v":1e2}', "field v"),
        ("[1]", "JSON object"),
        ("null", "JSON object"),
        ('"\\ud800"', "JSON object"),
        ("\ud800", "not valid JSON"),
    ],
)
def test_a_garbled_stored_payload_is_refused(text: str, reason: str) -> None:
    with pytest.raises(InvalidJob) as raised:
        decode_payload(text)

    assert reason in raised.value.reason


def test_a_stored_payload_must_be_text() -> None:
    with pytest.raises(InvalidJob):
        decode_payload(b'{"v":1}')  # type: ignore[arg-type]


# Job types and the registry ---------------------------------------------------------------------


def test_a_job_type_holds_its_declaration() -> None:
    declared = job_type(lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}), priority=0, retry_limit=0)

    assert declared.name == "enrich_lead"
    assert declared.lanes == frozenset({Lane.BULK, Lane.INTERACTIVE})
    assert (declared.priority, declared.retry_limit) == (0, 0)
    with pytest.raises(AttributeError):
        declared.priority = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "send_message_" + "x" * 35},  # 48 characters
        {"name": "a_1"},
        {"priority": 100},
        {"max_runtime": timedelta(seconds=1)},
        {"max_runtime": timedelta(hours=24)},
        {"retry_limit": 20},
        {"default_lane": Lane.REALTIME, "lanes": frozenset(Lane)},
    ],
)
def test_job_type_edges_are_allowed(overrides: dict[str, Any]) -> None:
    job_type(**overrides)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"name": 7}, TypeError),
        ({"name": "enrich"}, ValueError),
        ({"name": "enrich-lead"}, ValueError),
        ({"name": "Enrich_lead"}, ValueError),
        ({"name": "enrich__lead"}, ValueError),
        ({"name": "enrich_lead_"}, ValueError),
        ({"name": "send_message_" + "x" * 36}, ValueError),
        ({"default_lane": "bulk"}, TypeError),
        ({"lanes": {Lane.BULK}}, TypeError),
        ({"lanes": frozenset({"bulk"})}, TypeError),
        ({"lanes": frozenset()}, ValueError),
        ({"lanes": frozenset({Lane.REALTIME})}, ValueError),
        ({"priority": True}, TypeError),
        ({"priority": 50.0}, TypeError),
        ({"priority": -1}, ValueError),
        ({"priority": 101}, ValueError),
        ({"max_runtime": 60}, TypeError),
        ({"max_runtime": timedelta(0)}, ValueError),
        ({"max_runtime": timedelta(seconds=1, microseconds=1)}, ValueError),
        ({"max_runtime": timedelta(milliseconds=500)}, ValueError),
        ({"max_runtime": timedelta(hours=24, seconds=1)}, ValueError),
        ({"max_runtime": timedelta(seconds=-5)}, ValueError),
        ({"retry_limit": False}, TypeError),
        ({"retry_limit": -1}, ValueError),
        ({"retry_limit": 21}, ValueError),
        ({"handler": None}, TypeError),
        ({"handler": "run"}, TypeError),
    ],
)
def test_a_bad_job_type_is_refused_when_built(
    overrides: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        job_type(**overrides)


def test_the_registry_registers_freezes_and_gets() -> None:
    registry = JobTypes()
    first, second = job_type(), job_type(name="score_lead")

    registry.register_job_type(first)
    registry.register_job_type(second)
    assert not registry.frozen
    registry.freeze()

    assert registry.frozen
    assert registry.get("enrich_lead") is first
    assert registry.names() == ("enrich_lead", "score_lead")


def test_the_registry_refuses_a_duplicate_a_non_job_type_and_a_late_registration() -> None:
    registry = JobTypes()
    registry.register_job_type(job_type())

    with pytest.raises(ValueError, match="already registered"):
        registry.register_job_type(job_type(priority=1))
    with pytest.raises(TypeError):
        registry.register_job_type("enrich_lead")  # type: ignore[arg-type]
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register_job_type(job_type(name="score_lead"))
    assert registry.names() == ("enrich_lead",)


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("score_lead", "job type score_lead is not registered"),
        (PLANTED, "job type (not a job type name) is not registered"),
        (None, "job type (not a job type name) is not registered"),
    ],
)
def test_getting_an_unregistered_type_raises_unknown_job_type(name: object, shown: str) -> None:
    registry = JobTypes()
    registry.freeze()

    with pytest.raises(UnknownJobType) as raised:
        registry.get(name)  # type: ignore[arg-type]

    assert raised.value.code == "unknown_job_type"
    assert str(raised.value) == shown


def test_a_job_is_frozen() -> None:
    job = Job(ULID, "enrich_lead", Lane.BULK, MappingProxyType({"v": 1}), 1)

    with pytest.raises(AttributeError):
        job.attempts = 2  # type: ignore[misc]
    assert (job.id, job.type, job.lane, dict(job.payload), job.attempts) == (
        ULID,
        "enrich_lead",
        Lane.BULK,
        {"v": 1},
        1,
    )


def test_the_error_codes() -> None:
    assert (UnknownJobType.code, InvalidJob.code, IllegalTransition.code) == (
        "unknown_job_type",
        "invalid_job",
        "illegal_transition",
    )
    refused = InvalidJob("the payload has no version field v")
    assert (refused.reason, refused.message) == ("the payload has no version field v",) * 2
