"""``nextron.domain`` and ``nextron.ports`` import only the standard library (AD-1).

The domain may import itself; ports may import themselves and the domain. Contract 1 already
stops these layers importing higher ``nextron`` layers; this test also catches third-party
packages that no contract names, such as pydantic or SQLAlchemy.
"""

import ast
import sys

import pytest

from tests.contracts.support import ENGINE_ROOT, PACKAGE

ALLOWED_INTERNAL = {
    "domain": ("nextron.domain",),
    "ports": ("nextron.ports", "nextron.domain"),
}


def _is_inside(target: str, packages: tuple[str, ...]) -> bool:
    return any(target == p or target.startswith(p + ".") for p in packages)


def offending_imports(
    source: str, package: list[str], allowed: tuple[str, ...], label: str
) -> list[str]:
    """Return the imports in ``source`` (a module of ``package``) outside stdlib and ``allowed``."""
    problems = []
    for node in ast.walk(ast.parse(source, filename=label)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                if node.module:
                    targets = [".".join([*base, node.module])]
                else:  # from .. import name: each name may be a module
                    targets = [".".join([*base, alias.name]) for alias in node.names]
            else:
                targets = [node.module or ""]
        else:
            continue
        for target in targets:
            if target.split(".")[0] not in sys.stdlib_module_names and not _is_inside(
                target, allowed
            ):
                problems.append(f"{label}:{node.lineno} imports {target}")
    return problems


@pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL))
def test_layer_imports_only_the_standard_library(layer: str) -> None:
    problems = []
    for path in sorted((PACKAGE / layer).rglob("*.py")):
        parts = list(path.relative_to(ENGINE_ROOT).with_suffix("").parts)
        package = parts if path.name == "__init__.py" else parts[:-1]
        label = str(path.relative_to(ENGINE_ROOT))
        source = path.read_text(encoding="utf-8")
        problems += offending_imports(source, package, ALLOWED_INTERNAL[layer], label)

    assert problems == []


def test_the_check_catches_a_third_party_import() -> None:
    source = "import json\nfrom pydantic import BaseModel\nfrom . import x\n"

    problems = offending_imports(
        source, ["nextron", "domain"], ALLOWED_INTERNAL["domain"], "probe.py"
    )

    assert problems == ["probe.py:2 imports pydantic"]


def test_ports_may_import_the_domain_but_not_db() -> None:
    source = "from nextron.domain import x\nfrom .. import db\nimport nextron.db\n"

    problems = offending_imports(
        source, ["nextron", "ports"], ALLOWED_INTERNAL["ports"], "probe.py"
    )

    assert problems == ["probe.py:2 imports nextron.db", "probe.py:3 imports nextron.db"]
