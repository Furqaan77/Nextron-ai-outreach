"""The AI gateway adapter (AD-8, AD-9, AD-17, AD-18, INT-7, FR-57): every Claude call goes
through ``ClaudeGateway``, with a typed result and metered usage.

Import rule: ``nextron.ports``, ``nextron.domain`` and third-party SDKs. This is the only
package that may import ``anthropic`` (contract 5). Its modules: ``routes`` (the route
candidates as data), ``schemas`` (the output schemas), ``prompting`` (the request) and
``pricing`` (the cost). Nothing here logs.

**``ClaudeGateway(secrets, *, meter, http=None)``** implements ``LlmPort``. Without ``http`` it
builds ``default_http_client()`` (the live constructor: an ``httpx2.Client`` with 10 s to
connect and 300 s for each other phase, that never follows redirects) on its first ``run``,
looked up when it is called (so a test can refuse it) and under a lock. Constructing a gateway
builds no client.

**``run(route_tuple, inputs, urgency)``:**

1. A tuple that ``routes.find`` doesn't know raises ``AdapterError(False,
   "llm_unknown_route")``: a deployment mismatch.
2. ``urgency`` must be an ``Urgency`` (``TypeError``), and ``prompting.user_turn`` checks the
   blocks (``TypeError`` or ``ValueError``): a caller's bug.
3. ``read_credential(secrets, CredentialKey.CLAUDE)`` reads the key (``anthropic/api_key``) on
   every call and never keeps it; its errors (``credential_not_set``, ``keyring_failed``)
   propagate.

   Steps 1 to 3 make no request and record nothing.
4. Each call builds ``anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com",
   http_client=..., max_retries=0, timeout=...)``: the SDK never retries, so the job runner owns
   every retry. The explicit key and base URL stop the SDK reading credentials or a base URL
   from the environment.
5. One ``messages.create`` request (``prompting.build_request``), then the outcome below.

In Epic 2 ``urgency`` changes nothing: every ``run`` is one direct Messages call (a
``max_tokens`` retry makes a second). The gateway never reads usage or the budget: the calling
service asks ``nextron.domain.budget.allow`` first.

**Outcomes**, by the answer's ``stop_reason`` before any schema validation (the answer, then the
result, then the ``AiCall``'s error code):

- ``end_turn`` with one text block that parses as a JSON object and validates after case
  normalisation: ``LlmOk(parsed)``; no error code.
- Any other stop reason, no text block (or more than one), another kind of block
  (``tool_use``), text that isn't JSON, or a schema failure: ``LlmInvalid``; ``llm_invalid``.
- ``refusal``: ``LlmRefused(category)``, and never a second request; ``refused=True``.
- ``max_tokens``, when ``min(2 × max_tokens, cap) > max_tokens`` and there has been no retry
  yet: one retry at that ``max_tokens``; ``llm_truncated``.
- ``max_tokens`` otherwise: ``LlmTruncated``; ``llm_truncated``.
- HTTP 429, any 5xx, or an ``APIConnectionError`` (timeouts included): raises
  ``AdapterError(True, "llm_unavailable")``; ``llm_unavailable``.
- Any other 4xx (408 and 409 included): ``LlmFailed(False, "llm_rejected", status)``;
  ``llm_rejected``.
- Any other exception from the SDK call (a 3xx included): ``LlmFailed(False, "llm_unexpected",
  None)``; ``llm_unexpected``.

``thinking`` and ``redacted_thinking`` blocks (Haiku 5.5 thinks adaptively by default) carry no
answer and are skipped. **Case normalisation** (``schemas.validate``): before validation, every
string whose schema has an ``enum`` becomes ``strip().lower()``. ``parsed`` is the validated
object as JSON-ready data.

**Metering (FR-57).** Every HTTP request attempted records exactly one ``AiCall`` through
``meter`` before ``run`` returns or raises, and the result's ``LlmUsage`` sums them. The record
holds the route and ``route_tuple.key``; the served model (the response's ``model`` when it
fullmatches ``MODEL_PATTERN``, else the tuple's); the input, output, cache-read and cache-write
(``cache_creation_input_tokens``) tokens, where a count that isn't a non-negative ``int`` reads
0 (and one above ``MAX_AI_NUMBER`` reads ``MAX_AI_NUMBER``); ``iterations`` (``len`` of
``usage.iterations`` when the response reports a non-empty list, else 1, and 0 when no response
came back); ``batch=False``; ``cost_micros`` from ``pricing`` at the served model, splitting
5-minute and 1-hour writes by ``usage.cache_creation`` when the response has it (any write it
doesn't account for is priced at the 5-minute rate), else pricing every write at the 5-minute
rate; and ``refused`` and the error code from the table. A request with no response (a failed
status or no connection) records zero tokens and cost 0.

**Errors and text.** Every ``AdapterError`` message is this module's own text, raised ``from
None``. No API key, system prompt, block text, response text or SDK message appears in an
``AdapterError``, an ``AiCall``, a result's ``repr`` or a log record. The SDK logs whole request
bodies at DEBUG, so every ``run`` keeps the ``anthropic`` logger at WARNING or above (as
``nextron.logconfig`` does), whatever the root logger's level.
"""

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import anthropic
import httpx2

from nextron.adapters.ai import pricing, prompting, routes, schemas
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency, check_urgency
from nextron.domain.budget import CODE_PATTERN, MAX_AI_NUMBER, MODEL_PATTERN, AiCall
from nextron.domain.credentials import CredentialKey
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    LlmFailed,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmResult,
    LlmTruncated,
    LlmUsage,
)
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.usage_meter import UsageMeterPort

API_BASE: Final = "https://api.anthropic.com"
TIMEOUT_S: Final = 300.0
CONNECT_TIMEOUT_S: Final = 10.0
SDK_LOGGER: Final = "anthropic"
SDK_LEVEL: Final = logging.WARNING

LLM_UNKNOWN_ROUTE: Final = "llm_unknown_route"
LLM_UNAVAILABLE: Final = "llm_unavailable"
LLM_REJECTED: Final = "llm_rejected"
LLM_UNEXPECTED: Final = "llm_unexpected"
LLM_INVALID: Final = "llm_invalid"
LLM_TRUNCATED: Final = "llm_truncated"

END_TURN: Final = "end_turn"
REFUSAL: Final = "refusal"
MAX_TOKENS: Final = "max_tokens"
TEXT: Final = "text"
SKIPPED_BLOCKS: Final = ("thinking", "redacted_thinking")  # no answer in them


def _timeout() -> httpx2.Timeout:
    return httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S)


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 300 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(timeout=_timeout(), follow_redirects=False)


def _quiet_sdk_logger() -> None:
    """Keep the SDK's logger at WARNING or above: at DEBUG it logs whole request bodies."""
    logger = logging.getLogger(SDK_LOGGER)
    if logger.level < SDK_LEVEL:
        logger.setLevel(SDK_LEVEL)


# Reading a response -----------------------------------------------------------------------------


def _count(value: object) -> int:
    """A token count: 0 unless it is a non-negative ``int``, at most ``MAX_AI_NUMBER``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return min(value, MAX_AI_NUMBER)


def _iterations(usage: object) -> int:
    """``len(usage.iterations)`` when the response reports a non-empty list, else 1."""
    reported = getattr(usage, "iterations", None)
    if isinstance(reported, list) and reported:
        return min(len(reported), MAX_AI_NUMBER)
    return 1


def _served_model(response: object, requested: str) -> str:
    model = getattr(response, "model", None)
    if isinstance(model, str) and MODEL_PATTERN.fullmatch(model) is not None:
        return model
    return requested


def _category(response: object) -> str | None:
    category = getattr(getattr(response, "stop_details", None), "category", None)
    if isinstance(category, str) and CODE_PATTERN.fullmatch(category) is not None:
        return category
    return None


def _answer_text(response: object) -> str | None:
    """The one text block's text, or ``None`` when there isn't exactly one, or another kind of
    block is there."""
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return None
    texts = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind in SKIPPED_BLOCKS:
            continue
        text = getattr(block, "text", None)
        if kind != TEXT or not isinstance(text, str):
            return None
        texts.append(text)
    return texts[0] if len(texts) == 1 else None


def _parsed(response: object, schema: schemas.OutputSchema) -> dict[str, Any] | None:
    """The validated answer of an ``end_turn`` response, or ``None``."""
    text = _answer_text(response)
    if text is None:
        return None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return schemas.validate(schema, document)


@dataclass(frozen=True, slots=True)
class _Failure:
    """A request that got no usable response: its error code and HTTP status, if any."""

    code: str
    status: int | None


def _status_failure(status: object) -> _Failure:
    if isinstance(status, bool) or not isinstance(status, int):
        return _Failure(LLM_UNEXPECTED, None)
    if status == 429 or 500 <= status <= 599:
        return _Failure(LLM_UNAVAILABLE, status)
    if 400 <= status <= 499:
        return _Failure(LLM_REJECTED, status)
    return _Failure(LLM_UNEXPECTED, None)


class _Tally:
    """The ``AiCall`` of each request one ``run`` made, and their sum."""

    def __init__(self, route_tuple: RouteTuple, meter: UsageMeterPort) -> None:
        self._route_tuple = route_tuple
        self._meter = meter
        self.model = route_tuple.model  # the last response's served model
        self._calls: list[AiCall] = []

    def _record(self, call: AiCall) -> None:
        self._calls.append(call)
        self._meter.record(call)

    def failed(self, code: str) -> None:
        """A request with no response: zero tokens, zero iterations, cost 0."""
        tuple_ = self._route_tuple
        self._record(
            AiCall(
                tuple_.route,
                tuple_.key,
                tuple_.model,
                input_tokens=0,
                output_tokens=0,
                iterations=0,
                cost_micros=0,
                error_code=code,
            )
        )

    def answered(
        self, response: object, *, refused: bool = False, error_code: str | None = None
    ) -> str:
        """Record a request that got ``response``; returns its served model."""
        tuple_ = self._route_tuple
        served = _served_model(response, tuple_.model)
        self.model = served
        usage = getattr(response, "usage", None)
        input_tokens = _count(getattr(usage, "input_tokens", None))
        output_tokens = _count(getattr(usage, "output_tokens", None))
        cache_read = _count(getattr(usage, "cache_read_input_tokens", None))
        cache_write = _count(getattr(usage, "cache_creation_input_tokens", None))
        split = getattr(usage, "cache_creation", None)
        if split is None:
            five_minute, one_hour = cache_write, 0
        else:
            one_hour = _count(getattr(split, "ephemeral_1h_input_tokens", None))
            five_minute = max(
                _count(getattr(split, "ephemeral_5m_input_tokens", None)), cache_write - one_hour
            )
        cost = pricing.cost_micros(
            served,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_5m_tokens=five_minute,
            cache_write_1h_tokens=one_hour,
        )
        self._record(
            AiCall(
                tuple_.route,
                tuple_.key,
                served,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                iterations=_iterations(usage),
                cost_micros=min(cost, MAX_AI_NUMBER),
                refused=refused,
                error_code=error_code,
            )
        )
        return served

    def usage(self) -> LlmUsage:
        calls = self._calls
        return LlmUsage(
            input_tokens=sum(call.input_tokens for call in calls),
            output_tokens=sum(call.output_tokens for call in calls),
            cache_read_tokens=sum(call.cache_read_tokens for call in calls),
            cache_write_tokens=sum(call.cache_write_tokens for call in calls),
            iterations=sum(call.iterations for call in calls),
            requests=len(calls),
            cost_micros=sum(call.cost_micros for call in calls),
        )


# The gateway ------------------------------------------------------------------------------------


class ClaudeGateway:
    """``LlmPort`` over the Claude Messages API; see the module docstring."""

    def __init__(
        self,
        secrets: SecretsPort,
        *,
        meter: UsageMeterPort,
        http: httpx2.Client | None = None,
    ) -> None:
        self._secrets = secrets
        self._meter = meter
        self._http = http
        self._lock = threading.Lock()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def run(
        self, route_tuple: RouteTuple, inputs: Sequence[UntrustedBlock], urgency: Urgency
    ) -> LlmResult:
        candidate = routes.find(route_tuple)
        if candidate is None:
            raise AdapterError(
                False, LLM_UNKNOWN_ROUTE, "the route tuple matches no route candidate"
            ) from None
        check_urgency(urgency)
        prompting.user_turn(inputs)  # a caller's bad inputs raise here, before any request
        key = read_credential(self._secrets, CredentialKey.CLAUDE)
        _quiet_sdk_logger()
        client = anthropic.Anthropic(
            api_key=key,
            base_url=API_BASE,
            http_client=self._client(),
            max_retries=0,
            timeout=_timeout(),
        )
        tally = _Tally(route_tuple, self._meter)
        max_tokens, retried = candidate.max_tokens, False
        while True:
            request = prompting.build_request(route_tuple, candidate, inputs, max_tokens)
            response = self._send(client, request, tally)
            if isinstance(response, _Failure):
                return LlmFailed(
                    False, response.code, response.status, model=tally.model, usage=tally.usage()
                )
            stop = getattr(response, "stop_reason", None)
            if stop == REFUSAL:
                served = tally.answered(response, refused=True)
                return LlmRefused(_category(response), model=served, usage=tally.usage())
            if stop == MAX_TOKENS:
                served = tally.answered(response, error_code=LLM_TRUNCATED)
                doubled = min(2 * max_tokens, candidate.max_tokens_cap)
                if not retried and doubled > max_tokens:
                    max_tokens, retried = doubled, True
                    continue
                return LlmTruncated(model=served, usage=tally.usage())
            parsed = _parsed(response, candidate.output_schema) if stop == END_TURN else None
            if parsed is None:
                served = tally.answered(response, error_code=LLM_INVALID)
                return LlmInvalid(model=served, usage=tally.usage())
            served = tally.answered(response)
            return LlmOk(parsed, model=served, usage=tally.usage())

    @staticmethod
    def _send(client: anthropic.Anthropic, request: dict[str, Any], tally: _Tally) -> object:
        """One ``messages.create`` request: the response, or the recorded ``_Failure`` of a
        request that got none; ``llm_unavailable`` is recorded, then raised."""
        try:
            return client.messages.create(**request)
        except anthropic.APIStatusError as error:
            failure = _status_failure(error.status_code)
        except anthropic.APIConnectionError:  # timeouts included
            failure = _Failure(LLM_UNAVAILABLE, None)
        except Exception:  # noqa: BLE001 - whatever else the SDK raised
            failure = _Failure(LLM_UNEXPECTED, None)
        tally.failed(failure.code)
        if failure.code == LLM_UNAVAILABLE:
            if failure.status is None:
                message = "the Claude API could not be reached"
            else:
                message = f"the Claude API answered HTTP {failure.status}"
            raise AdapterError(True, LLM_UNAVAILABLE, message) from None
        return failure
