"""The ``ai_eval`` marker is registered and opt-in (AD-12, AD-14).

The ``ai_eval`` suite (``tests/ai_eval``, in ``testpaths`` since Story 2.8) runs the extraction
eval through the real ``LlmPort``; Epic 4 adds the AI Test Sets. This test shows the selection
rule: a plain ``pytest`` run skips it, and ``pytest -m ai_eval`` runs it. Every test function in
``tests/ai_eval`` is marked ``ai_eval`` (so none can reach the Claude API in a default run), and
Story 4.5's gate eval runs the gate's runner with ``activate=False``, so it never writes
``ai_route_activations``.
"""

import ast

import pytest

from tests.contracts.support import ENGINE_ROOT

AI_EVAL_DIR = ENGINE_ROOT / "tests" / "ai_eval"


def _marked(function: ast.FunctionDef) -> bool:
    return any(
        ast.unparse(decorator) == "pytest.mark.ai_eval" for decorator in function.decorator_list
    )


def test_marker_is_registered(pytestconfig: pytest.Config) -> None:
    markers = pytestconfig.getini("markers")

    assert any(line.startswith("ai_eval:") for line in markers)


@pytest.mark.ai_eval
def test_ai_eval_runs_only_when_selected(pytestconfig: pytest.Config) -> None:
    assert "ai_eval" in (pytestconfig.getoption("markexpr") or "")


def test_the_ai_eval_folder_is_collected(pytestconfig: pytest.Config) -> None:
    assert "tests/ai_eval" in pytestconfig.getini("testpaths")


def test_every_ai_eval_test_is_marked_ai_eval() -> None:
    found = []
    for path in sorted(AI_EVAL_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [
            (path.name, node.name, _marked(node))
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ]

    assert {name for name, _, _ in found} >= {"test_extraction_eval.py", "test_gate_eval.py"}
    assert [entry for entry in found if not entry[2]] == []


def test_the_gate_eval_never_activates_a_tuple() -> None:
    source = (AI_EVAL_DIR / "test_gate_eval.py").read_text(encoding="utf-8")

    assert 'runs.run(found.active.tuple_id, "extraction", activate=False)' in source
    assert "assert activation_rows(db_engine) == before" in source
