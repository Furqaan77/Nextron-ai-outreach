"""The one staleness test (AD-7, Story 4.20): ``nextron.domain.grounding.is_stale`` decides
whether a Draft revision is stale, and no other module defines a staleness test of its own
(a function named ``is_stale`` or a ``StaleReason``). A probe proves the check catches one.
"""

import ast
from pathlib import Path

from tests.contracts.support import PACKAGE

OWNER = PACKAGE / "domain" / "grounding.py"
NAMES = frozenset({"is_stale", "StaleReason"})


def _definitions(source: str) -> set[str]:
    """The staleness names ``source`` defines (functions, classes or assignments)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in NAMES:
                found.add(node.name)
        elif isinstance(node, ast.Assign):
            found.update(
                target.id
                for target in node.targets
                if isinstance(target, ast.Name) and target.id in NAMES
            )
    return found


def _offenders() -> list[str]:
    return sorted(
        f"{path.relative_to(PACKAGE.parent).as_posix()}: {', '.join(sorted(names))}"
        for path in PACKAGE.rglob("*.py")
        if path != OWNER and (names := _definitions(path.read_text(encoding="utf-8")))
    )


def test_only_the_grounding_domain_defines_the_staleness_test() -> None:
    assert _definitions(OWNER.read_text(encoding="utf-8")) == NAMES
    assert _offenders() == []


def test_the_check_catches_a_second_staleness_test() -> None:  # probe
    assert _definitions("def is_stale(revision):\n    return None\n") == {"is_stale"}
    assert _definitions("class Owner:\n    def is_stale(self):\n        return False\n") == {
        "is_stale"
    }
    assert _definitions("StaleReason = str\n") == {"StaleReason"}
    assert _definitions("from nextron.domain.grounding import is_stale\n") == set()
