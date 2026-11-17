---
title: 'Story 2.7: Call Claude through one gateway with typed results and metered usage'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '4668d306d762a6a3489bd174bfe9fca1443d40cc'
review_loop_iteration: 0
story_key: '2-7-call-claude-through-one-gateway-with-typed-results-and-meter'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing can call Claude yet.
- There is no `LlmPort`, no route table, no output schema and no price list.
- `ai_usage` and `AiCall` exist since Story 1.13, but no Claude call writes them.
- Epic 2's extraction stories (2.8–2.10, 2.16) need one gateway. It must return a typed outcome instead of crashing on a refusal, truncation or bad output, and it must meter every request.

Requirements: FR-57 · AD-8, AD-9, AD-17, AD-18, INT-7 · NFR-11, NFR-17.

**Approach:**
- **Value types** in `nextron/domain/ai.py`.
- **The port** `LlmPort.run(route_tuple, inputs, urgency) -> LlmResult`, with its five result types, in `nextron/ports/llm.py`.
- **The adapter** `nextron/adapters/ai`: `ClaudeGateway`, plus `routes.py`, `schemas.py`, `prompting.py` and `pricing.py`.
- **A scripted fake** in `tests/fakes/llm.py`.
- **Nothing else:** no migration (`ai_usage` and `AiCall` already exist), no wiring in `main` (Story 2.9 adds the first caller) and no new dependency (`anthropic==2.8.0` and `pydantic==2.14.3` are already pinned).

The acceptance criteria are Story 2.7 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise. This story is built out of order, on track D. It needs only Epic 1 and nothing from Stories 2.1–2.6.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/ai.py`, standard library only). Construction checks raise `TypeError` or `ValueError` that name the field, never its value.
  - **Enums.**
    - `Urgency(StrEnum)`: `interactive`, `soon` and `bulk`.
    - `Effort(StrEnum)`: `low`, `medium`, `high`, `xhigh` and `max`.
  - **`RouteTuple(route, model, effort, prompt_version, schema_version, system_prompt)`**, frozen, with `system_prompt` set to `repr=False`.
    - **Fields.**
      - `route` fullmatches `budget.CODE_PATTERN`.
      - `model` fullmatches `[a-z0-9][a-z0-9.-]{0,63}`.
      - `effort` is an `Effort`.
      - `prompt_version` fullmatches `v[1-9][0-9]{0,3}`.
      - `schema_version` is an `int` (not a `bool`) from 1 to 9999.
      - `system_prompt` is a non-empty `str`.
    - **`prompt_sha256`** is the hex SHA-256 of the system prompt's UTF-8.
    - **`key`** is `<model>:<effort>:<prompt_version>:s<schema_version>:<first 12 hex of prompt_sha256>`. It fits `budget.MODEL_PATTERN`, and it is what `ai_usage.route_tuple` stores.
  - **`UntrustedBlock(subject, source, block_id, text)`**, frozen, with `text` set to `repr=False`.
    - `subject` fullmatches `[a-z][a-z_]{0,31}:[A-Za-z0-9]{1,64}`. It is the Lead (`lead:<id>`), or the Signal Inbox item (`signal_item:<id>`) that has no Lead yet.
    - `source` fullmatches `[a-z][a-z0-9_]{0,31}`.
    - `block_id` fullmatches `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`.
    - `text` is a `str`.
- **The port** (`nextron/ports/llm.py`).
  - **`LlmPort`** is a `runtime_checkable` Protocol: `run(self, route_tuple: RouteTuple, inputs: Sequence[UntrustedBlock], urgency: Urgency) -> LlmResult`.
  - **`LlmUsage(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, iterations, requests, cost_micros)`** is frozen, and every field is a non-negative `int`. It sums every request the call made.
  - **The results.** Each is frozen and carries `model` and `usage`. `model` is the last response's served model, or the tuple's model when no response came back.
    - `LlmOk(parsed)`: `parsed` is the validated, case-normalised JSON object (a `dict`, `repr=False`).
    - `LlmRefused(category)`: `category` is `stop_details.category` when it fullmatches `CODE_PATTERN`, else `None`.
    - `LlmTruncated()` and `LlmInvalid()` carry no output text.
    - `LlmFailed(retryable, code, status)`: `status` is the HTTP status, or `None`.
  - **`LlmResult`** is `LlmOk | LlmRefused | LlmTruncated | LlmInvalid | LlmFailed`.
- **Routes** (`adapters/ai/routes.py`).
  - **`Candidate(name, model, effort, prompt_version, output_schema, batchable, max_tokens, max_tokens_cap, budget_class)`** is frozen, and `budget_class` is a `domain.budget.BudgetClass`. A candidate is refused at construction unless `0 < max_tokens ≤ max_tokens_cap ≤ 16,000`. 16,000 keeps every request under the SDK's non-streaming limit.
  - **`CANDIDATES`.** The first candidate of each route is its default. No candidate or request carries `fallbacks`, because Haiku has no server-side fallback.

    | name | model | effort | prompt | schema | batchable | max_tokens / cap | budget class |
    |---|---|---|---|---|---|---|---|
    | `extract_signals` | `claude-haiku-5-5` | `medium` | `v1` | `ExtractSignalsV1`, version 1 | yes | 8,000 / 16,000 | blockable (enrichment) |
    | `extract_people` | `claude-haiku-5-5` | `medium` | `v1` | `ExtractPeopleV1`, version 1 | yes | 4,000 / 16,000 | blockable (enrichment) |
    | `signal_inbox_parse` | `claude-haiku-5-5` | `medium` | `v1` | `SignalInboxParseV1`, version 1 | no | 2,000 / 8,000 | blockable (discovery) |

  - **Lookups.**
    - `candidates(route)` lists a route's candidates.
    - `default_candidate(route)` is its first candidate.
    - `find(route_tuple)` returns the candidate that matches the tuple's name, model, effort, prompt version and schema version, or `None`.
- **Schemas** (`adapters/ai/schemas.py`).
  - **`OutputSchema(version, model)`** pairs a version with a pydantic model. Every model is `extra="forbid"`.
  - **`ExtractSignalsV1`** has three lists:
    - `signals`: `{signal_type, block_id, quote, event_date: date | None}`;
    - `profile_facts`: `{kind ∈ what_we_do, location, technology; value; block_id; quote}`;
    - `size_clues`: `{kind ∈ lawyers_at_office, lawyers_firm_wide, staff, group_staff, locations; number: int; block_id; quote}`.
  - **`ExtractPeopleV1`**: `people: [{name, role, block_id, quote}]`.
  - **`SignalInboxParseV1`**: `segment`, `signal_type`, `website`, `author_name`, `author_role`, `event_date: date` and `quote`, each nullable.
  - **What a schema checks.** A schema checks shape only. The business rules (quotes of 300 characters or fewer, the substring check, Segment keys) belong to Stories 2.9, 2.10 and 2.16.
  - **`wire_schema(schema)`** is `anthropic.transform_schema(model)`, computed once per schema. Every object in it has `additionalProperties: false`. Constraints the API doesn't support move into descriptions, and pydantic still checks them locally.
- **Building the request** (`adapters/ai/prompting.py`).
  - **`user_turn(blocks)`** raises `ValueError` before any request when there are no blocks, when they name more than one subject, or when a `block_id` repeats.
    - Each block renders as `<untrusted source="<source>" id="<block_id>">\n<text>\n</untrusted>`.
    - Blocks are separated by a blank line. The user turn holds nothing else.
  - **Escaping.**
    - **What it catches:** a delimiter look-alike is `<` or one of its look-alikes (U+FF1C, U+FE64, U+2039, U+2329, U+27E8, U+3008), then optional whitespace and `/`, then `untrusted` in any case, in ASCII or fullwidth letters.
    - **What it changes:** the look-alike's bracket becomes `&lt;`. Nothing else in the text changes, so `&`, quotation marks and any other `<` stay verbatim.
  - **`build_request(route_tuple, candidate, blocks, max_tokens)`** returns the keyword arguments for `messages.create`:
    - `model`;
    - `max_tokens`;
    - `system=[{"type": "text", "text": <system_prompt>, "cache_control": {"type": "ephemeral"}}]`;
    - `messages=[{"role": "user", "content": <user_turn>}]`;
    - `output_config={"effort": ..., "format": {"type": "json_schema", "schema": <wire schema>}}`.

    It never sets `thinking`, `temperature`, `top_p`, `top_k`, `tools`, `tool_choice`, `stop_sequences`, `fallbacks` or `betas`, and never adds an assistant prefill. The system text never contains the schema.
- **Pricing** (`adapters/ai/pricing.py`).
  - **Rate cards** are in integer micro-dollars per million tokens: input / output / cache read / 5-minute cache write / 1-hour cache write.

    | Model | input | output | cache read | 5-minute write | 1-hour write |
    |---|---|---|---|---|---|
    | `claude-haiku-5-5`, prompt of 100,000 tokens or fewer | 100,000 | 500,000 | 10,000 | 125,000 | 200,000 |
    | `claude-haiku-5-5`, prompt over 100,000 tokens | 500,000 | 2,500,000 | 50,000 | 625,000 | 1,000,000 |
    | `claude-opus-5-5` | 4,000,000 | 20,000,000 | 200,000 | 5,000,000 | 8,000,000 |

  - **`cost_micros(model, *, input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens)`** is `ceil(Σ tokens × rate / 10^6)`.
    - The prompt is input + cache-read + cache-write tokens.
    - A model that isn't listed is priced at the most expensive card, field by field.
- **The gateway** (`nextron/adapters/ai/__init__.py`).
  - **`ClaudeGateway(secrets, *, meter, http=None)`** implements `LlmPort`.
  - **`default_http_client()`** is the live constructor: an `httpx2.Client` with 10 s to connect and 300 s for each other phase, that never follows redirects. When `http` isn't given, it is built on the first `run`, looked up at call time and under a lock.
  - **`run`:**
    1. A tuple `find` doesn't know raises `AdapterError(False, "llm_unknown_route")`.
    2. `user_turn` checks the blocks (`ValueError`).
    3. `read_credential(secrets, CredentialKey.CLAUDE)` reads the key on every call and never keeps it. Its `AdapterError`s propagate.

       Steps 1–3 make no request and record nothing.
    4. Each call builds `anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", http_client=http, max_retries=0, timeout=…)`, so the job runner owns every retry.
    5. The request, then the outcome table below.

    `urgency` must be an `Urgency`. In Epic 2 it changes nothing: every `run` is one direct Messages call.
  - **Outcomes:**

    | Answer | Result | `AiCall` |
    |---|---|---|
    | `end_turn`, text that parses as JSON and validates after case normalisation | `LlmOk(parsed)` | no error code |
    | any other stop reason, no text block, text that isn't JSON, or a schema failure | `LlmInvalid` | `llm_invalid` |
    | `refusal` | `LlmRefused`; no second request | `refused=True` |
    | `max_tokens`, when `min(2 × max_tokens, cap) > max_tokens` and there has been no retry yet | one retry at that `max_tokens` | `llm_truncated` |
    | `max_tokens` otherwise | `LlmTruncated` | `llm_truncated` |
    | HTTP 429, any 5xx, or an `APIConnectionError` (timeouts included) | raises `AdapterError(True, "llm_unavailable")` | `llm_unavailable` |
    | any other 4xx | `LlmFailed(False, "llm_rejected", status)` | `llm_rejected` |
    | any other exception from the SDK call | `LlmFailed(False, "llm_unexpected", None)` | `llm_unexpected` |

  - **Case normalisation.** Before validation, the response JSON is walked alongside `model_json_schema()` (resolving `$ref`, `anyOf`, `items` and `properties`), and every string whose schema has an `enum` becomes `strip().lower()`.
  - **Metering.** Each HTTP request attempted records exactly one `AiCall` before `run` returns or raises. The record holds:
    - the route and `route_tuple.key`;
    - the served model: the response's `model` when it fullmatches `MODEL_PATTERN`, else the tuple's model;
    - the four token counts, where a count that isn't a non-negative `int` reads 0 and the cache-write count is `cache_creation_input_tokens`;
    - `iterations`: `len(usage.iterations)` when the response reports a non-empty list, else 1, and 0 when no response came back;
    - `batch=False`;
    - `cost_micros` from `pricing`, splitting 5-minute and 1-hour writes by `usage.cache_creation` when the response has it, else pricing all writes at the 5-minute rate;
    - `refused` and the error code from the table.

    A request with no response records zero tokens and cost 0.
  - **No budget.** The gateway never reads usage or the budget: no `meter.snapshot` and no `domain.budget.allow`.
  - **Errors and text.**
    - Every `AdapterError` message is this module's own text, raised `from None`.
    - No API key, system prompt, block text, response text or SDK message appears in any `AdapterError`, `AiCall`, result `repr` or log record.
    - Nothing here logs. `nextron.logconfig` already keeps the `anthropic` logger at WARNING.
- **Isolation.**
  - `tests/live_guard.py` adds `("nextron.adapters.ai", "default_http_client")` to `LIVE_CONSTRUCTORS`, and `ADAPTER_PACKAGES["ai"] = Guarded(("default_http_client",))`.
  - `PORT_FAKES` maps `LlmPort` to `ScriptedLlm`.
  - Contract 5 already exempts `nextron.adapters.ai`.

**Never:**
- **No real Claude request in any test.** `ai_eval` stays opt-in and skipped, and this story adds no `ai_eval` test.
- **Nothing from Epic 4:** no `fallbacks`, betas, Message Batches, streaming or Opus routes.
- **Nothing from Story 2.8:** no prompt files, `manifest.lock`, `prompt_snapshots` or activations.
- **No budget check in the gateway.**
- **Nothing else:** no `main` wiring and no caller (Story 2.9), no migration, no new dependency, and no change to `AiCall` or `ai_usage`.

### Decisions made overnight (pending user review)

1. **One `ai_usage` row per HTTP request attempted,** like Story 1.13's `api_usage`. A `max_tokens` retry therefore writes two rows, and `LlmUsage` sums them. `iterations` is the API's own count (`usage.iterations`, per the review rubric).
2. **Prices** come from the bundled Claude API reference (cached 2026-11-03), not from the web.
   - The planning docs give only Haiku 5.5 at $0.10 / $0.50 and Opus 5.5 at $4 / $20.
   - The reference adds:
     - Haiku over 100K tokens: $0.50 / $2.50;
     - cache reads: 0.1× input on Haiku, $0.20 on Opus;
     - cache writes: 1.25× input for 5 minutes and 2× for 1 hour.
   - **Mac should check these against Anthropic's pricing page.**
   - Costs round up, and an unlisted model is priced at the dearest card, both so that the budget errs toward stopping early.
3. **Effort is `medium`** on all three routes: the model's own default, set explicitly. Open question OQ-A8 sizes the effort later.
4. **The `max_tokens` values and caps** are those in the table, capped at 16,000 so no request needs streaming. The gateway retries only when doubling actually raises `max_tokens`.
5. **The tuple carries the system prompt text.** Its SHA-256 is derived from the text, so the two can't disagree. Story 2.8 fills the text from the snapshot the activation names.
6. **Blocks belong to a subject**, not only a Lead, because a Signal Inbox item has no Lead yet. Mixing subjects raises an error.
7. **The v1 schemas** are as listed. Stories 2.9, 2.10 and 2.16 may revise a v1 schema before any activation exists.
8. **Two kinds of misuse:**
   - An unknown tuple is a deployment mismatch: a non-retryable `AdapterError`.
   - Bad inputs are a caller's bug: a `ValueError`.
9. **HTTP 408 and 409** are treated as "other 4xx", taking the acceptance criteria literally.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Ok | `extract_people`; `end_turn` with valid JSON | `LlmOk`, 1 request; `AiCall` with the tokens, iterations 1, the cost and the tuple key | N/A |
| Case | a `size_clues` kind of `" Staff"` | `parsed` holds `"staff"` | N/A |
| Refusal | `stop_reason: refusal`, `stop_details.category: "cyber"` | `LlmRefused("cyber")`, 1 request, `refused=True`; no `fallbacks` in the body | N/A |
| Truncated, then ok | `max_tokens`, then `end_turn` | second body `max_tokens` 16,000; `LlmOk` with `usage.requests` 2; two rows, the first `llm_truncated` | N/A |
| Truncated twice | `max_tokens` both times | `LlmTruncated`; 2 requests | N/A |
| At the cap | a probe candidate whose `max_tokens` equals its cap | `LlmTruncated`; 1 request | N/A |
| Bad output | non-JSON; a wrong type; an extra key; no text block; `tool_use` | `LlmInvalid`; `llm_invalid` | N/A |
| Unavailable | 429, 500, 503, 529; `ConnectError`; `ReadTimeout` | exactly 1 request; a row with zero tokens | `AdapterError(True, "llm_unavailable")` |
| Rejected | 400, 401, 403, 404, 413 | `LlmFailed(False, "llm_rejected", status)` | N/A |
| No key | no `anthropic/api_key` | 0 requests, 0 rows | `credential_not_set` |
| Unknown tuple | effort `high` on `extract_signals` | 0 requests, 0 rows | `AdapterError(False, "llm_unknown_route")` |
| Bad inputs | two subjects; no blocks; a repeated `block_id` | 0 requests | `ValueError` |
| Escape | `</untrusted>`, `< / UnTrUsted >`, `＜ｕｎｔｒｕｓｔｅｄ`, and `Smith & Jones <b>` | the first three start with `&lt;`; the last stays verbatim | N/A |
| Price | Haiku: 10,000 input + 1,000 output; 100,000 vs 100,001 prompt tokens; 1 input token. Opus: 1M input | 1,500; the standard card vs the long one; 1; 4,000,000 | N/A |
| Iterations | `usage.iterations` holds 2 entries | the row has iterations 2 | N/A |

</frozen-after-approval>

## Code Map

- **The adapter stub** (`nextron-engine/nextron/adapters/ai/__init__.py`): a docstring only today. It becomes the gateway, and `default_http_client` must live in this module for the live guard.
- **The pattern to copy** (`nextron/adapters/telegram/__init__.py`): the lazy, locked `_client`, `read_credential` on every call, `AdapterError(...) from None`, and a docstring with an outcome table.
- **The metering records** (`nextron/domain/budget.py`):
  - `AiCall` (line 192) is the record. Use it as it is.
  - `CODE_PATTERN` and `MODEL_PATTERN` (88-89) and `MAX_AI_NUMBER` (92). `BudgetClass` (116).
  - `nextron/ports/usage_meter.py`: `UsageMeterPort.record` never raises and must be called outside a transaction. `Metered` is for `ApiCall` only, so don't use it here.
- **Credentials:** `nextron/domain/credentials.py` (`CredentialKey.CLAUDE` → `anthropic/api_key`) and `nextron/ports/secrets.py` (`read_credential`).
- **The ports docstring** (`nextron/ports/__init__.py`): its import rule is domain and standard library only. Mention `llm`.
- **The adapters docstring** (`nextron/adapters/__init__.py`): `ai` is "reserved" today. Describe it, and say that the AI gateway records an `AiCall` per request instead of an `ApiCall`.
- **The SDK** (`.venv/Lib/site-packages/anthropic`):
  - `anthropic.transform_schema`;
  - the errors `APIStatusError` (`.status_code`), `APIConnectionError` and `APITimeoutError`;
  - `Usage` has `cache_creation_input_tokens` and `cache_read_input_tokens`. `iterations` isn't a typed field, so read it with `getattr`;
  - the client takes `api_key`, `base_url`, `http_client` (an `httpx2.Client`), `max_retries` and `timeout`. An explicit `api_key` stops it consulting the environment.
- **The tests to extend:**
  - `tests/contracts/test_adapter_boundary.py`:
    - add a `ClaudeGateway` driver to `DRIVERS`, with a planted Claude key in `PLANTED` and written under `anthropic/api_key`;
    - generalise `metering_problems` so this driver expects one `AiCall` per request. Its `error_code` is the raised error's code, or the code of an `LlmFailed` it returned;
    - update the docstring.
  - `tests/contracts/test_test_isolation.py`:
    - line 142: the port name set gains `"LlmPort"`;
    - line 412 asserts `ADAPTER_PACKAGES["ai"] == NotYetBuilt()`: change it to the `Guarded` classification.
  - `tests/live_guard.py`: `LIVE_CONSTRUCTORS`, `ADAPTER_PACKAGES` and the docstring.
  - `tests/fakes/__init__.py`: `PORT_FAKES` and the docstring.
  - `tests/contracts/test_source_tree.py`: `FILES` (line 27) gains every new file.
- **Docs:** `nextron-engine/README.md`, a new "The Claude gateway" section after "Metering, quotas and the AI budget" (line 1120).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/ai.py` -- `Urgency`, `Effort`, `RouteTuple` and `UntrustedBlock` -- the value types that services and Story 2.8's queries can share.
- [x] `nextron-engine/nextron/ports/llm.py`, `ports/__init__.py` docstring -- `LlmPort`, `LlmUsage` and the five results.
- [x] `nextron-engine/nextron/adapters/ai/schemas.py` -- the three v1 models, `OutputSchema` and `wire_schema`.
- [x] `nextron-engine/nextron/adapters/ai/routes.py` -- `Candidate`, `CANDIDATES`, `candidates`, `default_candidate` and `find`.
- [x] `nextron-engine/nextron/adapters/ai/pricing.py` -- the rate cards and `cost_micros`.
- [x] `nextron-engine/nextron/adapters/ai/prompting.py` -- `user_turn`, the escaping and `build_request`.
- [x] `nextron-engine/nextron/adapters/ai/__init__.py`, `adapters/__init__.py` docstring -- `ClaudeGateway`, `default_http_client`, the outcome table, case normalisation and metering.
- [x] `nextron-engine/tests/fakes/llm.py`, `tests/fakes/__init__.py` -- `ScriptedLlm(*script)`. It returns each scripted `LlmResult`, or raises each scripted `AdapterError`, in order, and records `(route_tuple, inputs, urgency)` in `calls`. It raises `AssertionError` when the script is exhausted. Its parameter names equal the port's.
- [x] `nextron-engine/tests/live_guard.py`, `tests/contracts/test_test_isolation.py`, `tests/contracts/test_adapter_boundary.py`, `tests/contracts/test_source_tree.py` -- the classification, the port set, the driver with the AI metering check, and `FILES`.
- [x] `nextron-engine/tests/unit/test_ai_domain.py` -- every field check, `key` and `prompt_sha256`, and that no `repr` shows the prompt or the text.
- [x] `nextron-engine/tests/unit/test_ai_routes.py` -- the exact table; `find` and the defaults; the cap rules refused at construction; every candidate's model has a price; no candidate field is named like `fallbacks`.
- [x] `nextron-engine/tests/unit/test_ai_schemas.py` -- the schema walk.
  - It fails on a property whose name contains `reasoning`, `chain_of_thought` or `thought` (in any case), and on an `explanation` without `maxLength` ≤ 200.
  - Every enum value fullmatches `[a-z][a-z0-9_]*`.
  - In every wire schema, every object has `additionalProperties: false` and there is no `minLength`, `maxLength`, `minimum` or `maximum`.
  - Probe schemas prove that each check catches its problem.
- [x] `nextron-engine/tests/unit/test_ai_prompting.py` -- the Escape and Bad inputs rows, the block format, and the request shape (the forbidden keys, and the schema absent from the system text).
- [x] `nextron-engine/tests/unit/test_ai_pricing.py` -- the Price row, the cache rates and an unlisted model.
- [x] `nextron-engine/tests/unit/test_ai_gateway.py` -- every other row of the matrix, through `httpx2.MockTransport`.
  - **The request:** it goes to `https://api.anthropic.com/v1/messages` with `x-api-key` and no `anthropic-beta` header.
  - **The key:** a changed key is used on the next call.
  - **No budget:** a meter whose `snapshot` raises still lets `run` work.
  - **No text anywhere:** with the root logger at DEBUG, no log record, `AiCall` or error holds the prompt, block or response text.
- [x] `nextron-engine/README.md` -- the "The Claude gateway" section: the routes, the outcomes, metering and prices, untrusted blocks, and what Story 2.8 and Epic 4 add.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes, `ai_eval` stays skipped, and every contract is kept. Contract 5 still lets only `nextron.adapters.ai` import `anthropic`.
- Given `serve`, when it starts, then no AI client or HTTP client has been built.

## Implementation Notes

Built as specified; what the spec left open, and where the code had to differ:

- **Caller-bug checks live in the domain.** `tests/contracts/test_error_contracts.py` lets a
  module under `nextron/adapters` raise only `AdapterError`. So `check_blocks`, `check_urgency`,
  `check_route_fields` and `check_token_limits` are in `nextron/domain/ai.py`. `user_turn`,
  `Candidate` and `ClaudeGateway.run` call them, and their `TypeError` and `ValueError`
  propagate as specified. `RouteTuple` uses `check_route_fields` too.
- **The API key header.** anthropic 2.8.0 sends an explicit `api_key` as
  `Authorization: Bearer <key>`, not `x-api-key`. The gateway test asserts the bearer header,
  and that there is no `anthropic-beta` header. An explicit `api_key` and `base_url` also stop
  `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` taking effect, which a test checks.
- **Which blocks count.** Haiku 5.5 thinks adaptively by default, so `thinking` and
  `redacted_thinking` blocks are skipped. An `end_turn` answer must have exactly one text block
  and no other kind of block. Otherwise it is `LlmInvalid`.
- **A 200 that isn't a Message.** For a non-JSON or odd body, the SDK returns a `str`, a `list`
  or a Message with empty fields. Every field is read defensively (`getattr`), so this is
  `LlmInvalid`, with iterations 1 and zero tokens. A 3xx (redirects are never followed) is
  "any other exception": `llm_unexpected`, with status `None`.
- **Case normalisation** also lowers a string `const`, which is pydantic's form of a one-value
  `Literal`; the v1 schemas have none. Validation is pydantic's strict JSON mode. `parsed` is
  `model_dump(mode="json")`, so dates are ISO strings.
- **Bounds on recorded numbers.**
  - A token count over `MAX_AI_NUMBER` (10^12) is recorded as `MAX_AI_NUMBER`, and so is a cost
    over it. `AiCall` would refuse the record otherwise, and the cap only errs toward stopping
    early.
  - When `usage.cache_creation` gives a 5-minute and 1-hour split, any written token it doesn't
    account for is priced at the 5-minute rate. A short breakdown can't under-price.
  - Cost is priced at the served model.
- **Text that can't be sent.** `RouteTuple.system_prompt` and `UntrustedBlock.text` must encode
  as UTF-8, so a lone surrogate is refused when the value is built. The SDK couldn't send it.
- **Logging.** At DEBUG, the SDK logs whole request bodies ("Request options"). Every `run`
  therefore keeps the `anthropic` logger at WARNING or above, whatever the root level, as
  `nextron.logconfig` already does in `serve`. The gateway itself never logs.
- **Port shapes.**
  - Results take `model` and `usage` as keyword-only arguments, for example
    `LlmFailed(False, code, status, model=..., usage=...)`.
  - `LlmUsage` fields default to 0.
  - `LlmResult` is a plain union, so `isinstance` works with it.
  - Every result checks its fields when it is built.
- **Lookups.** `candidates(route)` is `()`, and `default_candidate(route)` is `None`, for an
  unknown route. `find` returns `None` for anything that isn't a `RouteTuple`, so the gateway
  raises `llm_unknown_route` for it.
- **`schemas.validate(schema, document)`** does the case normalisation and the validation.
  `json_schema(schema)` is cached like `wire_schema`, and both hand out copies.
- **Contract tests.**
  - `ADAPTER_PACKAGES["ai"]` is `Guarded(("default_http_client",))`.
  - The probe test in `test_test_isolation.py` now classifies its `ai` probe as
    `NotYetBuilt()` itself, and drops ai's constructor from its list of live constructors.
  - `test_source_tree.FILES` gains the new source modules and `tests/fakes/llm.py`, since
    `FILES` lists no test modules.
  - `test_adapter_boundary.metering_problems(driver, record=ApiCall)` takes the record type
    from `RECORDS` (`ClaudeGateway: AiCall`).
- **No AI client in `serve`.** A test in a subprocess checks that importing `nextron.main` and
  `nextron.__main__` imports neither `anthropic` nor `nextron.adapters.ai`. The serve-process
  test's `usage_counts == (0, 0)` still covers "no request was made".

**Review fixes (pass 1):**

- **Port docstrings** (`ports/llm.py`).
  - `LlmTruncated` hit `max_tokens` on its last request: after the one retry at double
    `max_tokens` (up to the cap), or at once when `max_tokens` is already the cap.
  - `LlmFailed` is a failed request: the API rejected it (`status`), or something unexpected
    happened (`status` is `None`). The gateway returns it only with `retryable=False`; a
    retryable failure raises. The field and its checks are unchanged.
- **`ScriptedLlm` makes the gateway's caller checks** before it uses its script, in the
  gateway's order: an unknown tuple (`AdapterError(False, "llm_unknown_route")`), then
  `check_urgency`, then `check_blocks`. A refused call is not recorded in `calls` and consumes
  nothing. Its docstring now says it meters nothing, unlike the gateway.
- **`RouteTuple` refuses a blank system prompt** (only whitespace): "system_prompt must not be
  blank".
- **New tests.**
  - The gateway skips a `redacted_thinking` block, and very deeply nested JSON text is
    `LlmInvalid`.
  - The returned `LlmUsage` sums the cache tokens, for one request and across a `max_tokens`
    retry.
  - `ScriptedLlm`'s refusals.
  - A new `tests/unit/test_llm_port.py` covers every construction check of `LlmUsage` and the
    five results.
- **README.** The fake's behaviour and the blank-prompt rule.

**Final verification.**
- The first full run after the fixes failed only the known flaky
  `test_job_runner.py::test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper`.
- The re-run also hit a 60 s timeout in `test_the_composition_root_imports_neither_the_gateway_nor_the_sdk`: importing `nextron.main` took 32–58 s on the loaded PC, with FastAPI alone at 17 s. Its subprocess timeout is now 300 s. The test checks the import graph, not speed.
- **On the final tree:** one run failed only the known flaky `test_job_runner.py::test_a_second_start_raises_and_stop_is_idempotent` (a leftover interactive thread; untouched by this story). The re-run was clean.
- **Results:** `pytest` 4556 passed and 2 skipped (`ai_eval` and the symlink test); `lint-imports` 29 kept and 0 broken.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers: Blind Hunter (B, 12 findings), Edge Case Hunter (E, 12) and Verification Gap (V, 4). That is 28 raw findings, 24 after merging duplicates (B1+E7, B2+E1+E12 and B3+E6).
- **Routing:** 8 patch, 1 defer and 15 reject.
- **Loopbacks:** there are no `intent_gap` or `bad_spec` entries, so nothing loops back.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | B1+E7: the escaping misses a fullwidth or other slash look-alike (`＜／ｕｎｔｒｕｓｔｅｄ`), zero-width characters between the parts, and homoglyph letters (`prompting.py:69`). | medium | Real: `LOOKALIKE` allows only an ASCII `/` and `\s`, and a test pins that. But that is exactly the frozen rule: "optional whitespace and `/`, then `untrusted` … ASCII or fullwidth letters". A crawled page can already plant fake content without forging a delimiter, and every quote must be a substring of the page (Story 2.9). | reject: widening it changes the approved escaping rule; recommended to Mac |
| 2 | B2+E1+E12: `ANTHROPIC_CUSTOM_HEADERS` in the environment can override `Authorization` or add `anthropic-beta` (`__init__.py:346`). | low | Real in the SDK (`_client.py:250`). It needs someone who already controls Mac's environment, and the fix adds an environment guard. | reject |
| 3 | B3+E6: a dated served model id (`claude-haiku-5-5-2026…`) would miss `PRICES` and be priced on the Opus card. | maybe-false | The bundled reference says current model ids carry no date suffix and the response's `model` names the model that ran. It is unverifiable without a live response; if true it is medium (blockable work stops early). | defer |
| 4 | B4: the retry doubles once, so the caps of `extract_people` (16,000) and `signal_inbox_parse` (8,000) are never reached, and `LlmTruncated`'s docstring says "at the route's cap". | low | Real. The cap values are fixed in the frozen table and harmless. The docstring is wrong for those two routes. | patch (docstring only) |
| 5 | B5: `ScriptedLlm` runs no caller checks, and its docstring says it meters nothing "like the real gateway". | medium | Real: a Story 2.9 service test could pass with two subjects, a string urgency or an unknown tuple, all of which the gateway refuses. The real gateway does meter. | patch |
| 6 | B6: the catch-all `except Exception` turns local SDK errors into `llm_unexpected` and records a request that was never sent. | low | Real, but it is the frozen outcome table: "any other exception from the SDK call → `LlmFailed(False, "llm_unexpected", None)`". The row has zero tokens and cost. | reject: spec-mandated |
| 7 | B7: nothing limits the size of the untrusted input. | low | An oversized prompt is priced correctly (Haiku's long card) or fails as a typed `llm_rejected`. Which pages to send is Story 2.9's choice, and a limit adds parameters. | reject (note for Story 2.9) |
| 8 | B8: `signal_type` and `segment` are free strings, so they aren't case-normalised. | low | The frozen spec normalises only enum strings. The model sees the exact keys, and the callers match keys (Stories 2.9 and 2.16). | reject (note for Stories 2.9 and 2.16) |
| 9 | B9: the boundary metering check compares only `error_code`, never `refused` or zero tokens. | false | `test_a_refusal_is_refused_with_its_category_and_no_second_request` asserts `(refused, error_code) == (True, None)`, and `test_an_unavailable_api_raises_a_retryable_error_after_one_request` asserts the exact zero-token `AiCall`. | reject |
| 10 | B10: `LlmFailed`'s docstring says "shouldn't retry" yet accepts `retryable=True`, and the port docstring calls every failure "a request the API refused". | low | Real wording errors. The flag itself is what the spec asks for. | patch (docstrings) |
| 11 | B11: the clamp sets only the parent `anthropic` logger, so a child logger at DEBUG would still emit. | false | Nothing sets a child level. The SDK's `_utils/_logs.py` sets only `anthropic` and `httpx2`, and `anthropic._base_client` is NOTSET, so it inherits the clamp. | reject |
| 12 | B12: `user_turn` is built two or three times per `run`. | low | Negligible work, and the fix changes the specified `build_request` signature. | reject |
| 13 | E2: a 300 s read timeout against the SDK's 450 s estimate for 16,000 tokens. | maybe-false | The estimate assumes the slowest model, and Haiku outputs far faster. 300 s is in the frozen spec. | reject: spec value; noted for Mac |
| 14 | E3: a key with a newline, control or non-ASCII character. | low | `Credentials.save` strips the value (`app/setup/api.py:356`). Inner control or non-ASCII characters don't occur in Anthropic keys, and the fix adds a guard. | reject |
| 15 | E4: `default_http_client()` raising (a bad `SSL_CERT_FILE`) escapes as a non-`AdapterError`. | low | It is the same pattern as the Telegram and Google adapters and needs a broken SSL environment. The fix adds a guard. | reject |
| 16 | E5: a cache-creation split above the total over-prices. | low | It happens only on self-inconsistent API usage, and the deliberate overestimate errs toward stopping (Implementation Notes). | reject |
| 17 | E8: a whitespace-only `system_prompt` passes, and the API answers 400. | low | Real. The fix is one condition (`strip()`). | patch |
| 18 | E9: `SizeClue.number` is unbounded. | low | The frozen spec has schemas check shape only. Story 2.9 checks the number against its quote before storing. | reject (note for Story 2.9) |
| 19 | E10: the OpenTelemetry content mode could export prompt text. | false | Only `opentelemetry_api` is installed and nothing installs a tracer provider, so spans go to the no-op provider. | reject |
| 20 | E11: the "x-api-key" claim is untested, so live calls might get 401. | false | The pinned official SDK sends an explicit `api_key` as `Authorization: Bearer` (`anthropic/_client.py:378`). The spec's wording was wrong (Implementation Notes), and the test asserts what the SDK sends. | reject |
| 21 | V1: skipping a `redacted_thinking` block is untested. | low | Pre-verified gap. | patch |
| 22 | V2: the `RecursionError` path of `_parsed` (deep JSON) is untested through the gateway. | low | Pre-verified gap. | patch |
| 23 | V3: the cache-token sums of `LlmUsage` on a result are never asserted. | low | Pre-verified gap. | patch |
| 24 | V4: the construction checks of the port's results and `LlmUsage` have no test. | low | Pre-verified gap. | patch |

## Design Notes

```python
def run(self, route_tuple, inputs, urgency):
    candidate = routes.find(route_tuple) or _unknown()        # AdapterError, no request
    user = prompting.user_turn(inputs)                        # ValueError, no request
    key = read_credential(self._secrets, CredentialKey.CLAUDE)
    client = anthropic.Anthropic(api_key=key, base_url=API_BASE,
                                 http_client=self._client(), max_retries=0, timeout=TIMEOUT)
    max_tokens, tally = candidate.max_tokens, _Tally()
    while True:
        response = self._send(client, ..., max_tokens, tally)  # records one AiCall; may raise
        ...  # stop_reason → result; max_tokens retries at most once
```

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_ai_*.py tests/contracts/test_adapter_boundary.py tests/contracts/test_test_isolation.py` -- expected: all pass.
- The full suite through `suite_lock.py` -- expected: pytest and `lint-imports` both pass.
