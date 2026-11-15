"""Contract group 7 is generated from ``nextron/db/owners.py`` and stays in sync with it."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nextron.db.owners import OWNERS
from tests.contracts.support import ENGINE_ROOT, PYPROJECT, contracts

GENERATOR = ENGINE_ROOT / "scripts" / "gen_owner_contracts.py"


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_owner_contracts", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pyproject_holds_exactly_the_generated_contracts() -> None:
    generator = load_generator()
    current = PYPROJECT.read_text(encoding="utf-8")

    assert generator.splice(current, generator.render_contracts(OWNERS)) == current


def test_check_mode_passes_on_the_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ENGINE_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr


def test_each_owner_may_import_only_its_own_repositories() -> None:
    generated = {c["id"]: c for c in contracts() if c["id"].startswith("c7-")}

    assert set(generated) == {f"c7-owner-{owner}" for owner in OWNERS}
    for owner in OWNERS:
        c7 = generated[f"c7-owner-{owner}"]
        assert c7["type"] == "forbidden"
        assert c7["source_modules"] == [f"nextron.app.{owner}", f"nextron.app.queries.{owner}"]
        assert set(c7["forbidden_modules"]) == {
            f"nextron.db.repos.{other}" for other in OWNERS if other != owner
        }
        assert c7["allow_indirect_imports"] is True


def test_check_mode_detects_and_write_mode_repairs_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    generator = load_generator()
    current = PYPROJECT.read_text(encoding="utf-8")
    stale = tmp_path / "pyproject.toml"
    stale.write_text(
        generator.splice(current, generator.render_contracts(OWNERS[:-1])), encoding="utf-8"
    )
    monkeypatch.setattr(generator, "PYPROJECT", stale)

    assert generator.main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().err
    assert generator.main([]) == 0
    assert generator.main(["--check"]) == 0
    assert stale.read_text(encoding="utf-8") == current


def test_duplicate_owners_are_refused() -> None:
    with pytest.raises(ValueError):
        load_generator().render_contracts(["setup", "setup"])
