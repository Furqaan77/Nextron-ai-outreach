"""Helpers for the contract tests: paths, ``pyproject.toml`` and running ``lint-imports``."""

import os
import re
import shutil
import subprocess
import sysconfig
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

ENGINE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ENGINE_ROOT / "nextron"
PYPROJECT = ENGINE_ROOT / "pyproject.toml"
LOCKFILE = ENGINE_ROOT / "uv.lock"

_STATUS_LINE = re.compile(r"^(?P<name>.+) (?P<status>KEPT|BROKEN)$")


@cache
def pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def contracts() -> list[dict[str, Any]]:
    return pyproject()["tool"]["importlinter"]["contracts"]


def contract(contract_id: str) -> dict[str, Any]:
    [match] = [c for c in contracts() if c["id"] == contract_id]
    return match


def run_lint_imports(root: Path = ENGINE_ROOT) -> subprocess.CompletedProcess[str]:
    """Run this environment's ``lint-imports`` on the ``nextron`` package under ``root``.

    ``PYTHONPATH`` puts ``root`` ahead of the editable install, so a copy under ``tmp_path`` is
    the package that gets analysed.
    """
    executable = shutil.which("lint-imports", path=sysconfig.get_path("scripts"))
    assert executable, "lint-imports is not installed; run uv sync --frozen"
    env = {
        **os.environ,
        "COLUMNS": "1000",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(root),
    }
    return subprocess.run(
        [executable, "--no-cache"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )


def contract_statuses(output: str) -> dict[str, str]:
    """Map each contract name in ``lint-imports`` output to KEPT or BROKEN."""
    statuses: dict[str, str] = {}
    for line in output.splitlines():
        match = _STATUS_LINE.match(line.strip())
        if match:
            statuses[match["name"]] = match["status"]
    return statuses


def lint_copy_with_probe(
    tmp_path: Path, package: str, source: str
) -> tuple[str, subprocess.CompletedProcess[str]]:
    """Copy ``nextron/`` and ``pyproject.toml`` under ``tmp_path``, add a probe module, lint it.

    ``package`` is a dotted package such as ``nextron.domain``. Returns the probe's dotted name
    and the ``lint-imports`` result. The real source tree is never touched.
    """
    root = tmp_path / "engine"
    shutil.copytree(PACKAGE, root / "nextron", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(PYPROJECT, root / "pyproject.toml")
    module = f"{package}._lint_probe"
    (root / Path(*module.split("."))).with_suffix(".py").write_text(source, encoding="utf-8")
    return module, run_lint_imports(root)
