"""``ClaudeGateway`` on ``httpx2.MockTransport`` (``nextron.adapters.ai``, Story 2.7): the
request on the wire, every outcome row of the matrix, the metering of each request, the key read
on every call, no budget check, no prompt, block or response text anywhere, the lazy default
client (refused in tests), and the scripted fake. Nothing here reaches Claude."""

import json
import logging
import subprocess
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

import httpx2
import pytest

import nextron.adapters.ai as ai
from nextron.adapters.ai import (
    LLM_INVALID,
    LLM_REJECTED,
    LLM_TRUNCATED,
    LLM_UNAVAILABLE,
    LLM_UNEXPECTED,
    LLM_UNKNOWN_ROUTE,
    ClaudeGateway,
    routes,
)
from nextron.adapters.ai.prompting import user_turn
from nextron.adapters.ai.routes import CANDIDATES
from nextron.domain.ai import Effort, RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import AiCall, Usage
from nextron.domain.playbook import PlaybookSnapshot
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    LlmFailed,
    LlmInvalid,
    LlmOk,
    LlmPort,
    LlmRefused,
    LlmTruncated,
    LlmUsage,
)
from nextron.ports.secrets import CREDENTIAL_NOT_SET, KEYRING_FAILED
from tests.fakes.llm import ScriptedLlm
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

KEY: Final = "Kq4Zx8Wn2Lr6Tb9Hm3Vc"  # 20 letters and digits: no redaction rule masks it
OTHER_KEY: Final = "Pz7Ld3Xw9Mq5Nr1Kt8Jb"
SECRET_NAME: Final = "anthropic/api_key"
LEAD: Final = "lead:01JD3QZ8X5V9K2M7N4P6R8T0WY"
PROMPT: Final = "Pr0mptMarker: extract the named decision-makers, quoting each page exactly."
PAGE: Final = "Bl0ckMarker: Ann Smith is the Managing Partner of Smith & Jones LLP."
ANSWER_MARK: Final = "Answ3rMarker"
HAIKU: Final = "claude-haiku-5-5"
MESSAGES_URL: Final = "https://api.anthropic.com/v1/messages"
PEOPLE: Final = {
    "people": [
        {"name": "Ann Smith", "role": "Managing Partner", "block_id": "p1", "quote": "Ann Smith"}
    ]
}
SIGNALS: Final = {
    "signals": [],
    "profile_facts": [],
    "size_clues": [{"kind": " Staff", "number": 40, "block_id": "p1", "quote": "40 staff"}],
    "job_postings": [],
}


# Answers ----------------------------------------------------------------------------------------


def usage(
    input_tokens: object = 1_000,
    output_tokens: object = 200,
    **extra: object,
) -> dict[str, object]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, **extra}


def message(
    stop_reason: str | None = "end_turn",
    content: list[dict[str, Any]] | None = None,
    *,
    text: str | None = None,
    document: object = None,
    model: object = HAIKU,
    stop_details: dict[str, Any] | None = None,
    usage_: dict[str, object] | None = None,
) -> httpx2.Response:
    if content is None:
        body = text if text is not None else json.dumps(document if document else PEOPLE)
        content = [{"type": "thinking", "thinking": "", "signature": "s"}]
        content.append({"type": "text", "text": body})
    return httpx2.Response(
        200,
        json={
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_details": stop_details,
            "usage": usage() if usage_ is None else usage_,
        },
    )


def status(code: int) -> httpx2.Response:
    return httpx2.Response(
        code,
        json={"type": "error", "error": {"type": "probe", "message": f"{ANSWER_MARK} {KEY}"}},
    )


class Recorder:
    """A ``MockTransport`` handler: records each request and answers the next of ``answers``
    (the last one repeats); an exception is raised."""

    def __init__(self, *answers: httpx2.Response | BaseException) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def body(self, index: int = 0) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(self.requests[index].content)
        return document


def secrets_with(key: str | None = KEY) -> InMemorySecrets:
    secrets = InMemorySecrets()
    if key is not None:
        secrets.write(SECRET_NAME, key)
    return secrets


def gateway_on(
    recorder: Recorder,
    secrets: InMemorySecrets | None = None,
    meter: Any = None,
) -> tuple[ClaudeGateway, InMemoryUsageMeter]:
    meter = InMemoryUsageMeter() if meter is None else meter
    http = httpx2.Client(transport=httpx2.MockTransport(recorder))
    return ClaudeGateway(secrets or secrets_with(), meter=meter, http=http), meter


def route(name: str = "extract_people", **changes: Any) -> RouteTuple:
    candidate = CANDIDATES[name][0]
    fields: dict[str, Any] = {
        "route": name,
        "model": candidate.model,
        "effort": candidate.effort,
        "prompt_version": candidate.prompt_version,
        "schema_version": candidate.output_schema.version,
        "system_prompt": PROMPT,
    }
    return RouteTuple(**(fields | changes))


def block(block_id: str = "p1", subject: str = LEAD) -> UntrustedBlock:
    return UntrustedBlock(subject, "page", block_id, PAGE)


def run(
    recorder: Recorder, name: str = "extract_people", **kwargs: Any
) -> tuple[object, InMemoryUsageMeter]:
    gateway, meter = gateway_on(recorder, **kwargs)
    return gateway.run(route(name), [block()], Urgency.BULK), meter


def ai_calls(meter: InMemoryUsageMeter) -> list[AiCall]:
    calls = meter.calls
    assert all(isinstance(call, AiCall) for call in calls)
    return calls  # type: ignore[return-value]


# The port and the request -----------------------------------------------------------------------


def test_it_implements_the_llm_port() -> None:
    gateway, _ = gateway_on(Recorder(message()))

    assert isinstance(gateway, LlmPort)
    assert isinstance(ScriptedLlm(), LlmPort)


def test_the_request_goes_to_the_messages_api_with_the_key_and_no_beta() -> None:
    recorder = Recorder(message())

    run(recorder)

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("POST", MESSAGES_URL)
    # anthropic 2.8.0 sends an API key as a bearer credential, not as x-api-key
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "anthropic-beta" not in request.headers
    assert request.headers["anthropic-version"]
    assert request.headers["x-stainless-retry-count"] == "0"


def test_the_body_is_exactly_the_built_request() -> None:
    recorder = Recorder(message())

    run(recorder)

    body = recorder.body()
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert (body["model"], body["max_tokens"]) == (HAIKU, 4_000)
    assert body["system"] == [
        {"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert body["messages"] == [{"role": "user", "content": user_turn([block()])}]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "people" in body["output_config"]["format"]["schema"]["properties"]


def test_the_key_is_read_on_every_call() -> None:
    recorder = Recorder(message())
    secrets = secrets_with()
    gateway, _ = gateway_on(recorder, secrets)

    gateway.run(route(), [block()], Urgency.BULK)
    secrets.write(SECRET_NAME, OTHER_KEY)
    gateway.run(route(), [block()], Urgency.INTERACTIVE)

    assert [request.headers["authorization"] for request in recorder.requests] == [
        f"Bearer {KEY}",
        f"Bearer {OTHER_KEY}",
    ]


def test_the_environment_cant_redirect_the_request_or_supply_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", OTHER_KEY)
    recorder = Recorder(message())

    run(recorder)

    [request] = recorder.requests
    assert str(request.url) == MESSAGES_URL
    assert request.headers["authorization"] == f"Bearer {KEY}"


# Ok -------------------------------------------------------------------------------------------


def test_a_valid_answer_is_ok_and_metered() -> None:
    recorder = Recorder(message(usage_=usage(10_000, 1_000)))

    result, meter = run(recorder)

    assert isinstance(result, LlmOk)
    assert result.parsed == PEOPLE
    assert result.model == HAIKU
    assert result.usage == LlmUsage(
        input_tokens=10_000, output_tokens=1_000, iterations=1, requests=1, cost_micros=1_500
    )
    assert len(recorder.requests) == 1
    assert ai_calls(meter) == [
        AiCall(
            "extract_people",
            route().key,
            HAIKU,
            input_tokens=10_000,
            output_tokens=1_000,
            iterations=1,
            cost_micros=1_500,
        )
    ]


def test_an_enum_string_is_case_normalised_before_validation() -> None:
    recorder = Recorder(message(document=SIGNALS))

    result, meter = run(recorder, "extract_signals")

    assert isinstance(result, LlmOk)
    assert result.parsed["size_clues"][0]["kind"] == "staff"
    assert recorder.body()["max_tokens"] == 8_000
    assert ai_calls(meter)[0].error_code is None


def test_thinking_blocks_of_either_kind_are_skipped() -> None:
    content = [
        {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix"},
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": json.dumps(PEOPLE)},
    ]

    result, meter = run(Recorder(message(content=content)))

    assert isinstance(result, LlmOk)
    assert result.parsed == PEOPLE
    assert [call.error_code for call in ai_calls(meter)] == [None]


def test_the_signal_inbox_route_parses_nulls_and_a_date() -> None:
    document = dict.fromkeys(
        ("segment", "signal_type", "website", "author_name", "author_role", "quote")
    ) | {"event_date": "2026-11-03"}

    result, _ = run(Recorder(message(document=document)), "signal_inbox_parse")

    assert isinstance(result, LlmOk)
    assert result.parsed["event_date"] == "2026-11-03"


# Refusal ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("details", "category"),
    [
        ({"type": "refusal", "category": "cyber", "explanation": ANSWER_MARK}, "cyber"),
        ({"type": "refusal", "category": "general_harms"}, "general_harms"),
        ({"type": "refusal", "category": "Cyber Harm"}, None),
        ({"type": "refusal", "category": None}, None),
        (None, None),
    ],
)
def test_a_refusal_is_refused_with_its_category_and_no_second_request(
    details: dict[str, Any] | None, category: str | None
) -> None:
    recorder = Recorder(message("refusal", [], stop_details=details, usage_=usage(500, 0)))

    result, meter = run(recorder)

    assert result == LlmRefused(
        category,
        model=HAIKU,
        usage=LlmUsage(input_tokens=500, iterations=1, requests=1, cost_micros=50),
    )
    assert len(recorder.requests) == 1
    assert "fallbacks" not in recorder.body()
    [call] = ai_calls(meter)
    assert (call.refused, call.error_code) == (True, None)


# Truncation -------------------------------------------------------------------------------------


def test_a_truncated_answer_is_retried_once_at_double_max_tokens() -> None:
    recorder = Recorder(
        message(
            "max_tokens",
            text='{"signals": [',
            usage_=usage(1_000, 8_000, cache_creation_input_tokens=4_000),
        ),
        message(document=SIGNALS, usage_=usage(1_000, 9_000, cache_read_input_tokens=4_000)),
    )

    result, meter = run(recorder, "extract_signals")

    assert isinstance(result, LlmOk)
    assert [recorder.body(index)["max_tokens"] for index in (0, 1)] == [8_000, 16_000]
    assert recorder.body(0) | {"max_tokens": 16_000} == recorder.body(1)
    assert result.usage.requests == 2
    # first: 1,000 in, 8,000 out, 4,000 written (5-minute rate); second: 1,000 in, 9,000 out,
    # 4,000 read
    assert result.usage == LlmUsage(
        input_tokens=2_000,
        output_tokens=17_000,
        cache_read_tokens=4_000,
        cache_write_tokens=4_000,
        iterations=2,
        requests=2,
        cost_micros=(100 + 4_000 + 500) + (100 + 4_500 + 40),
    )
    assert [call.error_code for call in ai_calls(meter)] == [LLM_TRUNCATED, None]


def test_a_second_truncation_is_truncated_after_two_requests() -> None:
    recorder = Recorder(message("max_tokens", text="{"))

    result, meter = run(recorder, "extract_signals")

    assert isinstance(result, LlmTruncated)
    assert result.usage.requests == 2
    assert len(recorder.requests) == 2
    assert [call.error_code for call in ai_calls(meter)] == [LLM_TRUNCATED, LLM_TRUNCATED]


@pytest.mark.parametrize(
    ("name", "sizes"),
    [("extract_people", [4_000, 8_000]), ("signal_inbox_parse", [2_000, 4_000])],
)
def test_the_retry_doubles_within_the_cap(name: str, sizes: list[int]) -> None:
    recorder = Recorder(message("max_tokens", text="{"))

    run(recorder, name)

    assert [recorder.body(index)["max_tokens"] for index in range(2)] == sizes


def test_a_candidate_already_at_its_cap_is_truncated_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    probe = replace(CANDIDATES["extract_people"][0], max_tokens=16_000, max_tokens_cap=16_000)
    monkeypatch.setattr(
        routes, "CANDIDATES", MappingProxyType({**CANDIDATES, "extract_people": (probe,)})
    )
    recorder = Recorder(message("max_tokens", text="{"))

    result, meter = run(recorder)

    assert isinstance(result, LlmTruncated)
    assert len(recorder.requests) == 1
    assert recorder.body()["max_tokens"] == 16_000
    assert [call.error_code for call in ai_calls(meter)] == [LLM_TRUNCATED]


def test_a_retry_that_fails_keeps_the_first_requests_usage() -> None:
    recorder = Recorder(message("max_tokens", text="{"), status(400))

    result, meter = run(recorder)

    assert result == LlmFailed(
        False,
        LLM_REJECTED,
        400,
        model=HAIKU,
        usage=LlmUsage(
            input_tokens=1_000, output_tokens=200, iterations=1, requests=2, cost_micros=200
        ),
    )
    assert [call.error_code for call in ai_calls(meter)] == [LLM_TRUNCATED, LLM_REJECTED]


# Bad output -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        message(text=f"{ANSWER_MARK} is not JSON"),
        message(text='["people"]'),
        message(text=json.dumps({"people": [{**PEOPLE["people"][0], "name": 7}]})),
        message(text=json.dumps({"people": [], "notes": ANSWER_MARK})),
        message(text=json.dumps({"people": [{**PEOPLE["people"][0], "extra": "x"}]})),
        message(content=[]),
        message(content=[{"type": "thinking", "thinking": "", "signature": "s"}]),
        message(content=[{"type": "tool_use", "id": "t", "name": "x", "input": PEOPLE}]),
        message(
            content=[
                {"type": "text", "text": json.dumps(PEOPLE)},
                {"type": "tool_use", "id": "t", "name": "x", "input": {}},
            ]
        ),
        message(content=[{"type": "text", "text": json.dumps(PEOPLE)}] * 2),
        message(content=[{"type": "text", "text": None}]),
        message("stop_sequence"),
        message("pause_turn"),
        message("tool_use"),
        message(None),
        httpx2.Response(200, content=b"\x00\xff<html>" + ANSWER_MARK.encode() + b"</html>"),
        httpx2.Response(200, json=[1, 2]),
        httpx2.Response(200, json={"content": "text", "stop_reason": "end_turn"}),
        message(text="[" * 100_000 + "]" * 100_000),
    ],
    ids=[
        "not_json",
        "json_array",
        "wrong_type",
        "extra_key",
        "extra_nested_key",
        "no_block",
        "only_thinking",
        "tool_use_block",
        "text_and_tool_use",
        "two_text_blocks",
        "text_not_a_string",
        "stop_sequence",
        "pause_turn",
        "tool_use_stop",
        "no_stop_reason",
        "garbage_body",
        "array_body",
        "content_not_a_list",
        "json_too_deep",
    ],
)
def test_bad_output_is_invalid(answer: httpx2.Response) -> None:
    recorder = Recorder(answer)

    result, meter = run(recorder)

    assert isinstance(result, LlmInvalid)
    assert len(recorder.requests) == 1
    [call] = ai_calls(meter)
    assert (call.error_code, call.refused, call.iterations) == (LLM_INVALID, False, 1)
    assert result.usage.requests == 1


def test_invalid_output_is_still_metered_with_its_tokens() -> None:
    result, meter = run(Recorder(message(text="nope", usage_=usage(3_000, 40))))

    assert isinstance(result, LlmInvalid)
    [call] = ai_calls(meter)
    assert (call.input_tokens, call.output_tokens, call.cost_micros) == (3_000, 40, 320)


# Failures ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        status(429),
        status(500),
        status(503),
        status(529),
        httpx2.ConnectError(f"refused {KEY}"),
        httpx2.ReadTimeout(f"timed out {ANSWER_MARK}"),
        httpx2.ConnectTimeout("slow"),
        httpx2.RemoteProtocolError("dropped"),
    ],
    ids=lambda answer: answer.__class__.__name__
    + (f"_{answer.status_code}" if isinstance(answer, httpx2.Response) else ""),
)
def test_an_unavailable_api_raises_a_retryable_error_after_one_request(
    answer: httpx2.Response | BaseException,
) -> None:
    recorder = Recorder(answer)
    gateway, meter = gateway_on(recorder)

    with pytest.raises(AdapterError) as raised:
        gateway.run(route(), [block()], Urgency.SOON)

    assert (raised.value.retryable, raised.value.code) == (True, LLM_UNAVAILABLE)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert len(recorder.requests) == 1
    assert ai_calls(meter) == [
        AiCall(
            "extract_people",
            route().key,
            HAIKU,
            input_tokens=0,
            output_tokens=0,
            iterations=0,
            cost_micros=0,
            error_code=LLM_UNAVAILABLE,
        )
    ]


@pytest.mark.parametrize("code", [400, 401, 403, 404, 408, 409, 413, 422])
def test_any_other_4xx_is_rejected_with_its_status(code: int) -> None:
    recorder = Recorder(status(code))

    result, meter = run(recorder)

    assert result == LlmFailed(
        False, LLM_REJECTED, code, model=HAIKU, usage=LlmUsage(requests=1)
    )
    assert len(recorder.requests) == 1
    [call] = ai_calls(meter)
    assert (call.error_code, call.input_tokens, call.iterations, call.cost_micros) == (
        LLM_REJECTED,
        0,
        0,
        0,
    )


def test_a_redirect_is_unexpected() -> None:
    recorder = Recorder(httpx2.Response(302, headers={"location": "https://evil.example.com/"}))

    result, meter = run(recorder)

    assert result == LlmFailed(
        False, LLM_UNEXPECTED, None, model=HAIKU, usage=LlmUsage(requests=1)
    )
    assert len(recorder.requests) == 1  # never followed
    assert [call.error_code for call in ai_calls(meter)] == [LLM_UNEXPECTED]


def test_any_other_exception_from_the_sdk_call_is_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(self: object, **kwargs: object) -> object:
        raise RuntimeError(f"{ANSWER_MARK} {KEY}")

    monkeypatch.setattr(ai.anthropic.resources.messages.Messages, "create", explode)

    result, meter = run(Recorder(message()))

    assert result == LlmFailed(
        False, LLM_UNEXPECTED, None, model=HAIKU, usage=LlmUsage(requests=1)
    )
    assert [call.error_code for call in ai_calls(meter)] == [LLM_UNEXPECTED]


# Refused before any request ---------------------------------------------------------------------


def test_a_missing_key_is_not_set_and_nothing_is_sent() -> None:
    recorder = Recorder(message())

    with pytest.raises(AdapterError) as raised:
        run(recorder, secrets=secrets_with(None))

    assert (raised.value.retryable, raised.value.code) == (False, CREDENTIAL_NOT_SET)
    assert recorder.requests == []


def test_an_unreadable_credential_manager_propagates_and_nothing_is_sent() -> None:
    secrets = secrets_with()
    secrets.failing.add("read")
    recorder = Recorder(message())

    with pytest.raises(AdapterError) as raised:
        _, meter = run(recorder, secrets=secrets)

    assert raised.value.code == KEYRING_FAILED
    assert recorder.requests == []


@pytest.mark.parametrize(
    "changes",
    [
        {"effort": Effort.HIGH},
        {"model": "claude-opus-5-5"},
        {"prompt_version": "v2"},
        {"schema_version": 2},
        {"route": "draft_email"},
        # Row: Mismatch (Story 4.4): a Playbook tuple on a candidate that doesn't use it
        {"playbook": PlaybookSnapshot("0.2.0", "---\nversion: 0.2.0\n---\nRules.\n")},
    ],
)
def test_an_unknown_tuple_is_refused_before_any_request(changes: dict[str, Any]) -> None:
    recorder = Recorder(message())
    gateway, meter = gateway_on(recorder, secrets_with(None))  # the tuple is checked first

    with pytest.raises(AdapterError) as raised:
        gateway.run(route("extract_signals", **changes), [block()], Urgency.BULK)

    assert (raised.value.retryable, raised.value.code) == (False, LLM_UNKNOWN_ROUTE)
    assert recorder.requests == [] and meter.records == []


def test_something_that_isnt_a_route_tuple_is_an_unknown_route() -> None:
    gateway, meter = gateway_on(Recorder(message()))

    with pytest.raises(AdapterError) as raised:
        gateway.run("extract_people", [block()], Urgency.BULK)  # type: ignore[arg-type]

    assert raised.value.code == LLM_UNKNOWN_ROUTE


@pytest.mark.parametrize(
    ("blocks", "message_text"),
    [
        ([block("p1"), block("p2", "lead:01JD3QZ8X5V9K2M7N4P6R8T0WZ")], "one subject"),
        ([], "at least one block"),
        ([block("p1"), block("p1")], "repeat a block_id"),
    ],
)
def test_bad_inputs_raise_before_any_request(
    blocks: list[UntrustedBlock], message_text: str
) -> None:
    recorder = Recorder(message())
    gateway, meter = gateway_on(recorder, secrets_with(None))  # before the key is read

    with pytest.raises(ValueError, match=message_text):
        gateway.run(route(), blocks, Urgency.BULK)

    assert recorder.requests == [] and meter.records == []


@pytest.mark.parametrize("urgency", ["bulk", None])
def test_an_urgency_that_isnt_one_is_refused_before_any_request(urgency: object) -> None:
    recorder = Recorder(message())
    gateway, meter = gateway_on(recorder)

    with pytest.raises(TypeError, match="urgency must be an Urgency"):
        gateway.run(route(), [block()], urgency)  # type: ignore[arg-type]

    assert recorder.requests == [] and meter.records == []


# Metering details -------------------------------------------------------------------------------


def test_iterations_count_the_reported_list() -> None:
    answer = message(usage_=usage(iterations=[{"type": "message"}, {"type": "compaction"}]))

    result, meter = run(Recorder(answer))

    assert ai_calls(meter)[0].iterations == 2
    assert isinstance(result, LlmOk) and result.usage.iterations == 2


@pytest.mark.parametrize("reported", [[], None, 3, "two", {"a": 1}])
def test_iterations_are_one_unless_a_non_empty_list_is_reported(reported: object) -> None:
    _, meter = run(Recorder(message(usage_=usage(iterations=reported))))

    assert ai_calls(meter)[0].iterations == 1


@pytest.mark.parametrize("count", ["seven", -5, None, 1.5, [1], {"n": 1}])
def test_a_count_that_isnt_a_non_negative_int_reads_zero(count: object) -> None:
    answer = message(
        usage_=usage(
            count, count, cache_read_input_tokens=count, cache_creation_input_tokens=count
        )
    )

    result, meter = run(Recorder(answer))

    [call] = ai_calls(meter)
    assert (call.input_tokens, call.output_tokens, call.cache_read_tokens) == (0, 0, 0)
    assert (call.cache_write_tokens, call.cost_micros) == (0, 0)
    assert isinstance(result, LlmOk)


@pytest.mark.parametrize(
    ("value", "count"),
    [
        (7, 7),
        (0, 0),
        (-1, 0),
        (True, 0),
        (False, 0),
        ("7", 0),
        (7.0, 0),
        (None, 0),
        (10**13, 10**12),
    ],
)
def test_a_count_is_read_only_from_a_non_negative_int(value: object, count: int) -> None:
    assert ai._count(value) == count


def test_a_huge_count_is_capped_rather_than_breaking_the_record() -> None:
    _, meter = run(Recorder(message(usage_=usage(10**15, 10**15))))

    [call] = ai_calls(meter)
    assert (call.input_tokens, call.output_tokens) == (10**12, 10**12)
    assert call.cost_micros == 10**12  # 10^12 tokens each on the long card is far more: capped


def test_cache_tokens_are_recorded_and_priced_by_their_ttl() -> None:
    answer = message(
        usage_=usage(
            1_000,
            100,
            cache_read_input_tokens=20_000,
            cache_creation_input_tokens=3_000,
            cache_creation={"ephemeral_5m_input_tokens": 1_000, "ephemeral_1h_input_tokens": 2_000},
        )
    )

    result, meter = run(Recorder(answer))

    [call] = ai_calls(meter)
    assert (call.cache_read_tokens, call.cache_write_tokens) == (20_000, 3_000)
    # 1,000 x 0.10 + 100 x 0.50 + 20,000 x 0.01 + 1,000 x 0.125 + 2,000 x 0.20, in micro-dollars
    assert call.cost_micros == 100 + 50 + 200 + 125 + 400
    assert isinstance(result, LlmOk)
    assert result.usage == LlmUsage(
        input_tokens=1_000,
        output_tokens=100,
        cache_read_tokens=20_000,
        cache_write_tokens=3_000,
        iterations=1,
        requests=1,
        cost_micros=875,
    )


def test_cache_writes_without_a_breakdown_are_priced_at_the_5_minute_rate() -> None:
    answer = message(usage_=usage(0, 0, cache_creation_input_tokens=8_000))

    _, meter = run(Recorder(answer))

    assert ai_calls(meter)[0].cost_micros == 1_000  # 8,000 x $0.125/M


def test_writes_the_breakdown_misses_are_priced_at_the_5_minute_rate() -> None:
    answer = message(
        usage_=usage(
            0,
            0,
            cache_creation_input_tokens=8_000,
            cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        )
    )

    _, meter = run(Recorder(answer))

    assert ai_calls(meter)[0].cost_micros == 1_000


@pytest.mark.parametrize("served", ["weird model!", None, 7, "", "-x"])
def test_an_unusable_served_model_is_recorded_as_the_tuples(served: object) -> None:
    result, meter = run(Recorder(message(model=served)))

    assert ai_calls(meter)[0].served_model == HAIKU
    assert isinstance(result, LlmOk) and result.model == HAIKU


def test_the_served_model_is_recorded_and_priced_as_served() -> None:
    result, meter = run(Recorder(message(model="claude-haiku-5-5-20261101")))

    [call] = ai_calls(meter)
    assert call.served_model == "claude-haiku-5-5-20261101"
    assert call.cost_micros == 8_000  # unlisted: the dearest card (1,000 x $4 + 200 x $20)
    assert isinstance(result, LlmOk) and result.model == "claude-haiku-5-5-20261101"


class SnapshotRefusingMeter(InMemoryUsageMeter):
    def snapshot(self, now: datetime) -> Usage:
        raise AssertionError("the gateway must never read usage or the budget")


def test_the_gateway_never_reads_usage_or_the_budget() -> None:
    meter = SnapshotRefusingMeter()

    result, _ = run(Recorder(message()), meter=meter)

    assert isinstance(result, LlmOk)
    assert len(meter.records) == 1


# No text anywhere -------------------------------------------------------------------------------

TEXTS: Final = (PROMPT, PAGE, ANSWER_MARK, KEY, "Bl0ckMarker", "Pr0mptMarker", "Ann Smith")
SCENARIOS: Final[list[tuple[str, Callable[[], httpx2.Response | BaseException]]]] = [
    ("ok", lambda: message()),
    ("invalid", lambda: message(text=f"{ANSWER_MARK} {PAGE}")),
    ("refused", lambda: message("refusal", [], stop_details={"category": ANSWER_MARK})),
    ("truncated", lambda: message("max_tokens", text=ANSWER_MARK)),
    ("rejected", lambda: status(400)),
    ("unavailable", lambda: status(503)),
    ("unreachable", lambda: httpx2.ConnectError(f"{ANSWER_MARK} {PROMPT}")),
]


def shown(value: object) -> str:
    if isinstance(value, BaseException):
        return "\n".join([str(value), repr(value), *traceback.format_exception(value)])
    return repr(value)


@pytest.mark.parametrize(("label", "answer"), SCENARIOS, ids=[label for label, _ in SCENARIOS])
def test_no_prompt_block_or_response_text_reaches_a_log_record_a_record_or_an_error(
    label: str,
    answer: Callable[[], httpx2.Response | BaseException],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)  # the root logger at DEBUG
    recorder = Recorder(answer())
    gateway, meter = gateway_on(recorder)
    try:
        outcome: object = gateway.run(route(), [block()], Urgency.BULK)
    except AdapterError as error:
        outcome = error

    everything = "\n".join(
        [
            *(record.getMessage() for record in caplog.records),
            *(repr(record.args) for record in caplog.records),
            *(repr(call) for call in meter.calls),
            shown(outcome),
        ]
    )
    assert recorder.requests  # the text was really sent
    for text in TEXTS:
        assert text not in everything, f"{label}: {text!r} leaked"
    assert logging.getLogger("anthropic").getEffectiveLevel() >= logging.WARNING


def test_an_ok_result_shows_nothing_of_the_answer_in_its_repr() -> None:
    result, _ = run(Recorder(message()))

    assert isinstance(result, LlmOk)
    assert "Ann Smith" in json.dumps(result.parsed)
    assert "Ann Smith" not in repr(result) and "parsed" not in repr(result)


# The live client --------------------------------------------------------------------------------


def test_the_live_client_is_built_on_the_first_run_and_refused_in_tests() -> None:
    meter = InMemoryUsageMeter()
    gateway = ClaudeGateway(secrets_with(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        gateway.run(route(), [block()], Urgency.BULK)
    assert meter.records == []  # no request was attempted


def test_no_client_is_built_when_the_run_is_refused_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[object] = []
    monkeypatch.setattr(ai, "default_http_client", lambda: built.append(1))
    gateway = ClaudeGateway(secrets_with(None), meter=InMemoryUsageMeter())

    with pytest.raises(AdapterError):
        gateway.run(route(), [block()], Urgency.BULK)

    assert built == []


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.ai", "default_http_client")

    client = ai.default_http_client()
    try:
        assert client.follow_redirects is False
        timeout = client.timeout
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
            10.0,
            300.0,
            300.0,
            300.0,
        )
    finally:
        client.close()


def test_the_client_is_built_once_and_kept_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(message())
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(ai, "default_http_client", build)
    gateway = ClaudeGateway(secrets_with(), meter=InMemoryUsageMeter())

    gateway.run(route(), [block()], Urgency.BULK)
    gateway.run(route(), [block()], Urgency.BULK)

    assert len(built) == 1 and len(recorder.requests) == 2
    assert not built[0].is_closed  # the SDK client never closes the shared one


# The scripted fake ------------------------------------------------------------------------------


def test_the_scripted_fake_answers_in_order_and_records_every_call() -> None:
    ok = LlmOk({"people": []}, model=HAIKU, usage=LlmUsage(requests=1))
    error = AdapterError(True, LLM_UNAVAILABLE, "the Claude API answered HTTP 503")
    fake = ScriptedLlm(ok, error)

    assert fake.run(route(), [block()], Urgency.BULK) is ok
    with pytest.raises(AdapterError) as raised:
        fake.run(route(), (block("p2"),), Urgency.SOON)
    assert raised.value is error
    with pytest.raises(AssertionError, match="exhausted"):
        fake.run(route(), [block()], Urgency.BULK)

    assert fake.calls == [
        (route(), (block(),), Urgency.BULK),
        (route(), (block("p2"),), Urgency.SOON),
        (route(), (block(),), Urgency.BULK),
    ]


@pytest.mark.parametrize(
    ("route_tuple", "inputs", "urgency", "error", "match"),
    [
        (route("extract_signals", effort=Effort.HIGH), [block()], "bulk", AdapterError, None),
        ("extract_people", [block()], Urgency.BULK, AdapterError, None),
        (route(), [block()], "bulk", TypeError, "urgency must be an Urgency"),
        (route(), [], Urgency.BULK, ValueError, "at least one block"),
        (
            route(),
            [block("p1"), block("p2", "lead:01JD3QZ8X5V9K2M7N4P6R8T0WZ")],
            Urgency.BULK,
            ValueError,
            "one subject",
        ),
        (route(), [block("p1"), block("p1")], Urgency.BULK, ValueError, "repeat a block_id"),
        (route(), "p1", Urgency.BULK, TypeError, "sequence of UntrustedBlock"),
    ],
    ids=[
        "unknown_tuple_first",
        "not_a_tuple",
        "urgency",
        "no_blocks",
        "two_subjects",
        "repeated_block_id",
        "not_blocks",
    ],
)
def test_the_scripted_fake_refuses_what_the_gateway_refuses_without_using_its_script(
    route_tuple: object, inputs: object, urgency: object, error: type[Exception], match: str | None
) -> None:
    ok = LlmOk({"people": []}, model=HAIKU, usage=LlmUsage(requests=1))
    fake = ScriptedLlm(ok)

    with pytest.raises(error, match=match) as raised:
        fake.run(route_tuple, inputs, urgency)  # type: ignore[arg-type]

    if error is AdapterError:
        assert (raised.value.retryable, raised.value.code) == (False, LLM_UNKNOWN_ROUTE)
    assert fake.calls == [] and list(fake.script) == [ok]


# Nothing builds it yet --------------------------------------------------------------------------


def test_the_composition_root_imports_neither_the_gateway_nor_the_sdk() -> None:
    """Importing ``nextron.main`` loads neither the AI package nor the SDK, so ``doctor``,
    ``open`` and ``seed sync`` stay quick. ``serve`` imports the routes table inside
    ``load_prompts`` (Story 2.8) and still builds no AI or HTTP client; Story 2.9 adds the first
    caller."""
    code = (
        "import sys, nextron.__main__, nextron.main\n"
        "print(sorted(name for name in sys.modules\n"
        "             if name == 'anthropic' or name.startswith('nextron.adapters.ai')))\n"
    )

    # Importing the composition root (FastAPI, Alembic) took over 60 s on a loaded PC.
    done = subprocess.run(  # noqa: S603 - this interpreter, a fixed snippet
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=300
    )

    assert done.stdout.strip() == "[]"
