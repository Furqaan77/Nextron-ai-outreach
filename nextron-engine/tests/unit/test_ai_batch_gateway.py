"""``ClaudeGateway``'s Message Batches on ``httpx2.MockTransport`` (Story 4.17, AD-8): the
request body of each item (no ``fallbacks``, no beta header, a 1-hour cached prefix, the schema
in ``output_config.format``, one subject), the caller checks, the outcomes of an ended batch and
their metering at the batch rate, a 404 delete, the error mapping, and the scripted fake.
Nothing here reaches Claude."""

import json
from typing import Any, Final

import httpx2
import pytest

from nextron.adapters.ai import (
    LLM_BATCH_ERRORED,
    LLM_INVALID,
    LLM_REJECTED,
    LLM_TRUNCATED,
    LLM_UNAVAILABLE,
    LLM_UNEXPECTED,
    LLM_UNKNOWN_ROUTE,
    ClaudeGateway,
    pricing,
)
from nextron.adapters.ai.prompting import FALLBACK_BETA
from nextron.adapters.ai.routes import CANDIDATES
from nextron.domain.ai import Effort, RouteTuple, UntrustedBlock, Urgency
from nextron.domain.ai_batch import custom_id
from nextron.domain.budget import AiCall
from nextron.domain.playbook import PlaybookSnapshot
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    BatchEnded,
    BatchInProgress,
    BatchLost,
    BatchLostKind,
    BatchRequest,
    BatchSucceeded,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmTruncated,
)
from tests.fakes.llm import ScriptedLlm
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter

KEY: Final = "Kq4Zx8Wn2Lr6Tb9Hm3Vc"
SECRET_NAME: Final = "anthropic/api_key"
PROMPT: Final = "Pr0mptMarker: extract the named decision-makers, quoting each page exactly."
PAGE: Final = "Bl0ckMarker: Ann Smith is the Managing Partner of Smith & Jones LLP."
ANSWER_MARK: Final = "Answ3rMarker"
HAIKU: Final = "claude-haiku-5-5"
LEAD_A: Final = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
LEAD_B: Final = "01JD3QZ8X5V9K2M7N4P6R8T0WZ"
BATCH: Final = "msgbatch_01HkcTjaV5uDC8jWR4ZsDV8d"
BATCHES_URL: Final = "https://api.anthropic.com/v1/messages/batches"
RESULTS_URL: Final = f"{BATCHES_URL}/{BATCH}/results"
MESSAGES_URL: Final = "https://api.anthropic.com/v1/messages"
PEOPLE: Final = {
    "people": [
        {"name": "Ann Smith", "role": "Managing Partner", "block_id": "p1", "quote": "Ann Smith"}
    ]
}
PLAYBOOK: Final = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Playbook.\n")


# Answers ----------------------------------------------------------------------------------------


def batch_json(status: str = "in_progress") -> dict[str, Any]:
    return {
        "id": BATCH,
        "type": "message_batch",
        "processing_status": status,
        "request_counts": {
            "processing": 0,
            "succeeded": 0,
            "errored": 0,
            "canceled": 0,
            "expired": 0,
        },
        "created_at": "2026-11-19T14:00:00Z",
        "expires_at": "2026-11-20T14:00:00Z",
        "ended_at": "2026-11-19T15:00:00Z" if status == "ended" else None,
        "archived_at": None,
        "cancel_initiated_at": None,
        "results_url": RESULTS_URL if status == "ended" else None,
    }


def message_json(
    stop_reason: str = "end_turn", document: object = None, **usage: object
) -> dict[str, Any]:
    text = json.dumps(PEOPLE if document is None else document)
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": HAIKU,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_details": {"category": "cyber"} if stop_reason == "refusal" else None,
        "usage": {"input_tokens": 1_000, "output_tokens": 200, **usage},
    }


def succeeded(item: str, **message: Any) -> dict[str, Any]:
    return {"custom_id": item, "result": {"type": "succeeded", "message": message_json(**message)}}


def lost(item: str, kind: str) -> dict[str, Any]:
    result: dict[str, Any] = {"type": kind}
    if kind == "errored":
        result["error"] = {
            "type": "error",
            "error": {"type": "api_error", "message": f"{ANSWER_MARK} {KEY}"},
        }
    return {"custom_id": item, "result": result}


class Api:
    """A ``MockTransport`` handler for the Batches API: records each request and answers by
    method and path."""

    def __init__(
        self,
        *,
        status: str = "ended",
        results: list[dict[str, Any]] | None = None,
        create: httpx2.Response | BaseException | None = None,
        retrieve: httpx2.Response | BaseException | None = None,
        delete: httpx2.Response | BaseException | None = None,
    ) -> None:
        self.status = status
        self.results = results or []
        self.create = create
        self.retrieve = retrieve
        self.delete = delete
        self.requests: list[httpx2.Request] = []

    @staticmethod
    def _answer(answer: httpx2.Response | BaseException) -> httpx2.Response:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if request.method == "POST" and url == BATCHES_URL:
            if self.create is not None:
                return self._answer(self.create)
            return httpx2.Response(200, json=batch_json())
        if request.method == "GET" and url == f"{BATCHES_URL}/{BATCH}":
            if self.retrieve is not None:
                return self._answer(self.retrieve)
            return httpx2.Response(200, json=batch_json(self.status))
        if request.method == "GET" and url == RESULTS_URL:
            lines = "\n".join(json.dumps(line) for line in self.results)
            return httpx2.Response(200, content=lines.encode("utf-8"))
        if request.method == "DELETE" and url == f"{BATCHES_URL}/{BATCH}":
            if self.delete is not None:
                return self._answer(self.delete)
            return httpx2.Response(200, json={"id": BATCH, "type": "message_batch_deleted"})
        if request.method == "POST" and url == MESSAGES_URL:
            return httpx2.Response(200, json=message_json())
        return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found"}})

    def body(self, index: int = 0) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(self.requests[index].content)
        return document


def status(code: int) -> httpx2.Response:
    return httpx2.Response(
        code,
        json={"type": "error", "error": {"type": "probe", "message": f"{ANSWER_MARK} {KEY}"}},
    )


def gateway_on(api: Api) -> tuple[ClaudeGateway, InMemoryUsageMeter]:
    secrets = InMemorySecrets()
    secrets.write(SECRET_NAME, KEY)
    meter = InMemoryUsageMeter()
    http = httpx2.Client(transport=httpx2.MockTransport(api))
    return ClaudeGateway(secrets, meter=meter, http=http), meter


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
    if candidate.uses_playbook:
        fields["playbook"] = PLAYBOOK
    return RouteTuple(**(fields | changes))


def block(lead: str = LEAD_A, block_id: str = "p1") -> UntrustedBlock:
    return UntrustedBlock(f"lead:{lead}", "website", block_id, PAGE)


def item(lead: str = LEAD_A, name: str = "extract_people", rev: int = 1) -> BatchRequest:
    return BatchRequest(custom_id(name, lead, rev), (block(lead),))


def ai_calls(meter: InMemoryUsageMeter) -> list[AiCall]:
    calls = meter.calls
    assert all(isinstance(call, AiCall) for call in calls)
    return calls  # type: ignore[return-value]


# Submit: the request body -----------------------------------------------------------------------


def test_submit_sends_each_item_without_fallbacks_with_a_one_hour_prefix_and_the_schema() -> None:
    api = Api()
    gateway, meter = gateway_on(api)

    batch_id = gateway.submit_batch(route(), [item(LEAD_A), item(LEAD_B)])

    assert batch_id == BATCH
    [request] = api.requests
    assert (request.method, str(request.url)) == ("POST", BATCHES_URL)
    assert "anthropic-beta" not in request.headers
    body = api.body()
    assert set(body) == {"requests"}
    assert [entry["custom_id"] for entry in body["requests"]] == [
        f"extract_people_{LEAD_A}_1",
        f"extract_people_{LEAD_B}_1",
    ]
    for entry, lead in zip(body["requests"], (LEAD_A, LEAD_B), strict=True):
        params = entry["params"]
        assert set(params) == {"model", "max_tokens", "system", "messages", "output_config"}
        assert "fallbacks" not in json.dumps(params)
        assert params["max_tokens"] == CANDIDATES["extract_people"][0].max_tokens
        assert params["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert params["output_config"]["format"]["type"] == "json_schema"
        assert params["output_config"]["effort"] == "medium"
        [message] = params["messages"]
        assert message["content"].count("<untrusted ") == 1
        assert f"lead:{lead}" not in message["content"]  # the subject never goes in the text
    assert meter.calls == []  # a submit records nothing


def test_a_batch_of_the_opus_draft_route_never_carries_the_fallbacks_a_direct_call_has() -> None:
    api = Api()
    gateway, _ = gateway_on(api)
    tuple_ = route("draft_sequence")

    gateway.submit_batch(tuple_, [item(name="draft_sequence")])
    gateway.run(tuple_, [block()], Urgency.SOON)

    batch_request, direct = api.requests
    assert "anthropic-beta" not in batch_request.headers
    assert "fallbacks" not in batch_request.content.decode("utf-8")
    params = api.body(0)["requests"][0]["params"]
    assert params["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert set(params) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert direct.headers["anthropic-beta"] == FALLBACK_BETA
    assert api.body(1)["fallbacks"] == "default"


@pytest.mark.parametrize(
    ("items", "error"),
    [
        ([], ValueError),
        ([item(LEAD_A), item(LEAD_A)], ValueError),  # a repeated custom_id
        ("not items", TypeError),
        ([object()], TypeError),
    ],
)
def test_submit_refuses_a_callers_bad_items_before_any_request(
    items: Any, error: type[Exception]
) -> None:
    api = Api()
    gateway, _ = gateway_on(api)

    with pytest.raises(error):
        gateway.submit_batch(route(), items)
    assert api.requests == []


def test_an_item_naming_two_subjects_is_refused_when_it_is_built() -> None:
    with pytest.raises(ValueError, match="one subject"):
        BatchRequest(custom_id("extract_people", LEAD_A, 1), (block(LEAD_A), block(LEAD_B, "p2")))


def test_submit_refuses_an_unbatchable_or_unknown_route() -> None:
    api = Api()
    gateway, _ = gateway_on(api)

    with pytest.raises(ValueError, match="not batchable"):
        gateway.submit_batch(route("signal_inbox_parse"), [item(name="signal_inbox_parse")])
    with pytest.raises(AdapterError) as raised:
        gateway.submit_batch(route(effort=Effort.MAX), [item()])
    assert (raised.value.retryable, raised.value.code) == (False, LLM_UNKNOWN_ROUTE)
    assert api.requests == []


@pytest.mark.parametrize(
    ("answer", "retryable", "code"),
    [
        (status(429), True, LLM_UNAVAILABLE),
        (status(529), True, LLM_UNAVAILABLE),
        (httpx2.ConnectError("down"), True, LLM_UNAVAILABLE),
        (status(400), False, LLM_REJECTED),
        (httpx2.Response(200, json={"type": "message_batch"}), False, LLM_UNEXPECTED),
    ],
)
def test_a_failed_submit_raises_without_any_answer_text(
    answer: httpx2.Response | BaseException, retryable: bool, code: str
) -> None:
    gateway, meter = gateway_on(Api(create=answer))

    with pytest.raises(AdapterError) as raised:
        gateway.submit_batch(route(), [item()])

    assert (raised.value.retryable, raised.value.code) == (retryable, code)
    assert ANSWER_MARK not in str(raised.value) and KEY not in str(raised.value)
    assert raised.value.__suppress_context__
    assert meter.calls == []


# Fetch ------------------------------------------------------------------------------------------


def test_a_batch_in_progress_is_one_request_and_records_nothing() -> None:
    api = Api(status="in_progress")
    gateway, meter = gateway_on(api)

    assert gateway.fetch_batch(BATCH, route()) == BatchInProgress()
    assert len(api.requests) == 1
    assert meter.calls == []


def test_an_ended_batch_reads_each_result_and_meters_it_once_at_the_batch_rate() -> None:
    ids = [custom_id("extract_people", LEAD_A, n) for n in range(7)]
    api = Api(
        results=[
            succeeded(ids[0], cache_read_input_tokens=400),
            succeeded(ids[1], stop_reason="refusal"),
            succeeded(ids[2], stop_reason="max_tokens"),
            succeeded(ids[3], document={"people": "not a list"}),
            lost(ids[4], "errored"),
            lost(ids[5], "canceled"),
            lost(ids[6], "expired"),
            {"custom_id": "bad id!", "result": {"type": "succeeded"}},
            {"custom_id": "other_" + LEAD_A + "_1", "result": {"type": "mystery"}},
        ]
    )
    gateway, meter = gateway_on(api)

    found = gateway.fetch_batch(BATCH, route())

    assert isinstance(found, BatchEnded)
    assert set(found.results) == set(ids)
    first = found.results[ids[0]]
    assert isinstance(first, BatchSucceeded) and isinstance(first.result, LlmOk)
    assert first.result.parsed == PEOPLE
    refused = found.results[ids[1]]
    assert isinstance(refused, BatchSucceeded) and isinstance(refused.result, LlmRefused)
    assert refused.result.category == "cyber"
    truncated = found.results[ids[2]]
    assert isinstance(truncated, BatchSucceeded) and isinstance(truncated.result, LlmTruncated)
    invalid = found.results[ids[3]]
    assert isinstance(invalid, BatchSucceeded) and isinstance(invalid.result, LlmInvalid)
    assert [found.results[i] for i in ids[4:]] == [
        BatchLost(BatchLostKind.ERRORED),
        BatchLost(BatchLostKind.CANCELED),
        BatchLost(BatchLostKind.EXPIRED),
    ]
    # metering: four answered items and the errored one, every row batch-flagged
    calls = ai_calls(meter)
    assert [(c.route, c.refused, c.error_code, c.batch) for c in calls] == [
        ("extract_people", False, None, True),
        ("extract_people", True, None, True),
        ("extract_people", False, LLM_TRUNCATED, True),
        ("extract_people", False, LLM_INVALID, True),
        ("extract_people", False, LLM_BATCH_ERRORED, True),
    ]
    direct = pricing.cost_micros(
        HAIKU,
        input_tokens=1_000,
        output_tokens=200,
        cache_read_tokens=400,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
    )
    assert calls[0].cost_micros == -(-direct // 2)  # every rate halved, rounded up
    assert calls[0].cache_read_tokens == 400
    assert (calls[4].input_tokens, calls[4].cost_micros, calls[4].iterations) == (0, 0, 0)
    assert all(call.route_tuple == route().key for call in calls)
    assert ANSWER_MARK not in repr(found)


def test_the_batch_rate_halves_every_rate_and_rounds_up() -> None:
    counts = {
        "input_tokens": 3,
        "output_tokens": 1,
        "cache_read_tokens": 7,
        "cache_write_5m_tokens": 5,
        "cache_write_1h_tokens": 1,
    }
    full = pricing.cost_micros(HAIKU, **counts)  # type: ignore[arg-type]
    half = pricing.cost_micros(HAIKU, **counts, batch=True)  # type: ignore[arg-type]

    total = 3 * 100_000 + 500_000 + 7 * 10_000 + 5 * 125_000 + 200_000
    assert full == -(-total // 1_000_000)
    assert half == -(-total // 2_000_000)


@pytest.mark.parametrize(
    ("answer", "retryable", "code"),
    [
        (status(503), True, LLM_UNAVAILABLE),
        (httpx2.ReadTimeout("slow"), True, LLM_UNAVAILABLE),
        (status(400), False, LLM_REJECTED),
    ],
)
def test_a_failed_fetch_raises(
    answer: httpx2.Response | BaseException, retryable: bool, code: str
) -> None:
    gateway, meter = gateway_on(Api(retrieve=answer))

    with pytest.raises(AdapterError) as raised:
        gateway.fetch_batch(BATCH, route())

    assert (raised.value.retryable, raised.value.code) == (retryable, code)
    assert meter.calls == []


def test_a_batch_gone_from_retrieve_reads_as_ended_with_no_results() -> None:
    """HTTP 404 on retrieve (the batch or its results expired): every item reads as "no
    result", so the owner re-runs it rather than polling a dead batch forever."""
    api = Api(retrieve=status(404))
    gateway, meter = gateway_on(api)

    assert gateway.fetch_batch(BATCH, route()) == BatchEnded({})
    assert len(api.requests) == 1  # no results request
    assert meter.calls == []


def test_an_unmetered_fetch_reads_the_same_results_and_records_nothing() -> None:
    ids = [custom_id("extract_people", LEAD_A, n) for n in range(2)]
    api = Api(results=[succeeded(ids[0]), lost(ids[1], "errored")])
    gateway, meter = gateway_on(api)

    metered = gateway.fetch_batch(BATCH, route())
    assert len(meter.calls) == 2
    again = gateway.fetch_batch(BATCH, route(), metered=False)

    assert again == metered
    assert len(meter.calls) == 2  # the re-fetch recorded nothing


@pytest.mark.parametrize("batch_id", ["", "msgbatch/../x", "a" * 129])
def test_a_bad_batch_id_is_refused_before_any_request(batch_id: str) -> None:
    api = Api()
    gateway, _ = gateway_on(api)

    with pytest.raises(ValueError):
        gateway.fetch_batch(batch_id, route())
    with pytest.raises(ValueError):
        gateway.delete_batch(batch_id)
    assert api.requests == []


# Delete -----------------------------------------------------------------------------------------


def test_delete_is_one_request_and_a_404_counts_as_deleted() -> None:
    api = Api()
    gateway, meter = gateway_on(api)
    gateway.delete_batch(BATCH)
    gone, _ = gateway_on(Api(delete=status(404)))
    gone.delete_batch(BATCH)

    [request] = api.requests
    assert (request.method, str(request.url)) == ("DELETE", f"{BATCHES_URL}/{BATCH}")
    assert meter.calls == []


@pytest.mark.parametrize(
    ("answer", "retryable", "code"),
    [(status(500), True, LLM_UNAVAILABLE), (status(409), False, LLM_REJECTED)],
)
def test_a_failed_delete_raises(
    answer: httpx2.Response, retryable: bool, code: str
) -> None:
    gateway, _ = gateway_on(Api(delete=answer))

    with pytest.raises(AdapterError) as raised:
        gateway.delete_batch(BATCH)
    assert (raised.value.retryable, raised.value.code) == (retryable, code)


# The scripted fake ------------------------------------------------------------------------------


def test_the_fake_makes_the_same_caller_checks_and_ends_a_batch_on_request() -> None:
    llm = ScriptedLlm()
    with pytest.raises(ValueError, match="not batchable"):
        llm.submit_batch(route("signal_inbox_parse"), [item(name="signal_inbox_parse")])
    with pytest.raises(ValueError):
        llm.submit_batch(route(), [])
    with pytest.raises(AdapterError) as raised:
        llm.submit_batch(route(effort=Effort.MAX), [item()])
    assert raised.value.code == LLM_UNKNOWN_ROUTE

    first = llm.submit_batch(route(), [item()])
    assert first == "msgbatch_fake_1"
    assert llm.batches_submitted == [(route(), (item(),))]
    assert llm.fetch_batch(first, route()) == BatchInProgress()
    ended = {item().custom_id: BatchLost(BatchLostKind.EXPIRED)}
    llm.end_batch(first, ended)
    assert llm.fetch_batch(first, route()) == BatchEnded(ended)
    llm.delete_batch(first)
    assert llm.deleted == [first]
    with pytest.raises(AdapterError) as unknown:
        llm.fetch_batch("msgbatch_fake_9", route())
    assert (unknown.value.retryable, unknown.value.code) == (False, LLM_REJECTED)
    assert llm.calls == []
