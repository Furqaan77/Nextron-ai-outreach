"""The extraction eval against the real Claude API (FR-71, AD-18, Story 2.8). Opt-in:
``uv run pytest -m ai_eval`` with ``ANTHROPIC_API_KEY`` set in the shell (never in a file). It
costs a few cents of Haiku.

It runs every case of ``tests/ai_sets/extraction`` once through ``ClaudeGateway`` on the real
HTTP client (``allow_live_constructor`` restores ``default_http_client``; the network guard lets
an ``ai_eval`` item reach ``api.anthropic.com`` only), with the default tuples built from the
prompt files exactly as ``serve`` would pin them, an ``InMemorySecrets`` holding the key and an
in-memory meter. It uses no database, so it activates nothing. It prints the report, the cost
included, and asserts the bars: every case was answered (no failed call: a refused, invalid or
failed injection case would otherwise count as an injection that didn't succeed), every
proposed quote passes the substring check, at least 80% of the labelled Signals are found, and
no injection succeeds.
"""

import os

import pytest

from nextron import main
from nextron.domain.credentials import CredentialKey, credential
from tests.ai_eval.extraction import default_tuples, evaluate, load_cases
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import allow_live_constructor

KEY_VARIABLE = "ANTHROPIC_API_KEY"
QUOTE_SHARE_BAR = 1.0
SIGNAL_RECALL_BAR = 0.8


@pytest.mark.ai_eval
def test_extraction_meets_the_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    key = os.environ.get(KEY_VARIABLE, "").strip()
    if not key:
        pytest.skip(f"set {KEY_VARIABLE} in this shell to run the extraction eval")
    from nextron.adapters.ai import ClaudeGateway  # noqa: PLC0415 - only this test loads it

    allow_live_constructor(monkeypatch, "nextron.adapters.ai", "default_http_client")
    secrets = InMemorySecrets()
    secrets.write(credential(CredentialKey.CLAUDE).secret_name, key)
    meter = InMemoryUsageMeter()
    gateway = ClaudeGateway(secrets, meter=meter)
    files, specs = main.load_prompts()

    report = evaluate(gateway, load_cases(), default_tuples(files, specs))

    print(report.summary())  # noqa: T201 - the report is the point of the run
    metered = sum(getattr(call, "cost_micros", 0) for call in meter.calls)
    print(f"metered requests: {len(meter.calls)}, ${metered / 1_000_000:.4f}")  # noqa: T201
    assert report.failed_calls == ()  # every case answered, or the other bars mean nothing
    assert report.quote_share >= QUOTE_SHARE_BAR
    assert report.signal_recall >= SIGNAL_RECALL_BAR
    assert report.violations == ()
