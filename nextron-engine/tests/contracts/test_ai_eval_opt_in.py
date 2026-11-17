"""The ``ai_eval`` marker is registered and opt-in (AD-12, AD-14).

The ``ai_eval`` suite (``tests/ai_eval``, in ``testpaths`` since Story 2.8) runs the extraction
eval through the real ``LlmPort``; Epic 4 adds the AI Test Sets. This test shows the selection
rule: a plain ``pytest`` run skips it, and ``pytest -m ai_eval`` runs it.
"""

import pytest


def test_marker_is_registered(pytestconfig: pytest.Config) -> None:
    markers = pytestconfig.getini("markers")

    assert any(line.startswith("ai_eval:") for line in markers)


@pytest.mark.ai_eval
def test_ai_eval_runs_only_when_selected(pytestconfig: pytest.Config) -> None:
    assert "ai_eval" in (pytestconfig.getoption("markexpr") or "")


def test_the_ai_eval_folder_is_collected(pytestconfig: pytest.Config) -> None:
    assert "tests/ai_eval" in pytestconfig.getini("testpaths")
