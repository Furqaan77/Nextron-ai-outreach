"""Contracts that name "every package except ..." list their sources explicitly; these tests
keep the lists complete.

Contract 4 covers every non-adapter package, contract 5 every package except
``nextron.adapters.ai``, and the composition-root-helper and placement contracts every
top-level package except the ones they allow. A new top-level module or adapter subpackage
fails here until it is added to ``pyproject.toml``.
"""

import ast
import sys
from pathlib import Path

import pytest

from tests.contracts.support import PACKAGE, contract

COMPOSITION_ROOT = {
    "nextron.main",
    "nextron.__main__",
    "nextron.bootstrap",
    "nextron.lock",
    "nextron.logconfig",
    "nextron.startup_checks",
}
# contract id -> the top-level packages allowed to import what it forbids
EVERYTHING_EXCEPT = {
    "c1-composition-root-helpers": COMPOSITION_ROOT,
    "c4-sqlalchemy-only-in-db": {"nextron.db"},
    "c4-apscheduler-only-in-jobs": {"nextron.jobs"},
    "c4-pydantic-settings-only-in-composition-root": {"nextron.main", "nextron.bootstrap"},
    "c4-web-framework-only-in-web-and-main": {"nextron.web", "nextron.main"},
}


def _is_package(folder: Path) -> bool:
    """A folder counts when it has ``__init__.py`` or any module (a namespace package)."""
    return folder.is_dir() and any(folder.glob("*.py"))


def _children(folder: Path, prefix: str) -> set[str]:
    names = set()
    for entry in folder.iterdir():
        if _is_package(entry):
            names.add(f"{prefix}.{entry.name}")
        elif entry.suffix == ".py" and entry.name != "__init__.py":
            names.add(f"{prefix}.{entry.stem}")
    return names


def non_adapter_modules() -> set[str]:
    return _children(PACKAGE, "nextron") - {"nextron.adapters"}


def non_ai_adapters() -> set[str]:
    return _children(PACKAGE / "adapters", "nextron.adapters") - {"nextron.adapters.ai"}


def test_contract_4_covers_every_non_adapter_package() -> None:
    assert set(contract("c4-sdks-only-in-adapters")["source_modules"]) == non_adapter_modules()


def test_contract_5_covers_every_package_except_adapters_ai() -> None:
    expected = non_adapter_modules() | non_ai_adapters()

    assert set(contract("c5-anthropic-only-in-ai")["source_modules"]) == expected


@pytest.mark.parametrize("contract_id", sorted(EVERYTHING_EXCEPT))
def test_contract_covers_every_package_it_does_not_allow(contract_id: str) -> None:
    expected = _children(PACKAGE, "nextron") - EVERYTHING_EXCEPT[contract_id]

    assert set(contract(contract_id)["source_modules"]) == expected


def test_a_folder_of_modules_without_init_counts_as_a_package(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("")
    (tmp_path / "data").mkdir()

    assert _children(tmp_path, "x") == {"x.pkg"}


def test_package_roots_import_only_the_standard_library() -> None:
    """``nextron/__init__.py`` and ``nextron/adapters/__init__.py`` sit outside contracts 4-5."""
    for init in (PACKAGE / "__init__.py", PACKAGE / "adapters" / "__init__.py"):
        tree = ast.parse(init.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                assert root in sys.stdlib_module_names, f"{init} imports {root}"
