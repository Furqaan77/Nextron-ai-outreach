"""Story 8.1: only the Operator's Settings form changes a quota (FR-57, AD-9). The quota
settings are named only by the settings schema (``nextron/domain/settings_schema.py``) and the
budget spec (``nextron/domain/budget.py``, whose ``QUOTAS`` hold each quota's setting); every
other module reaches a quota through ``QUOTAS`` and ``queries.setup.budget_limits``. The
check reads every module under ``nextron/`` (AST) for a ``Key.QUOTA_*`` attribute or a string
literal that is a quota setting name (``quota_<name>``), and a planted module proves it catches
both."""

import ast
import re
from pathlib import Path
from typing import Final

from tests.contracts.support import ENGINE_ROOT

PACKAGE: Final = "nextron"
ALLOWED: Final = frozenset({"nextron/domain/settings_schema.py", "nextron/domain/budget.py"})
SETTING_NAME: Final = re.compile(r"quota_[a-z0-9_]+")


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def quota_problems(root: Path) -> list[str]:
    """Each place outside ``ALLOWED`` that names a quota setting, as ``<file>:<line>``."""
    problems: list[str] = []
    for path in sorted((root / PACKAGE).rglob("*.py")):
        name = _relative(path, root)
        if name in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "Key"
                and node.attr.startswith("QUOTA_")
            ):
                problems.append(f"{name}:{node.lineno} Key.{node.attr}")
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SETTING_NAME.fullmatch(node.value)
            ):
                problems.append(f"{name}:{node.lineno} {node.value!r}")
    return problems


def test_only_the_schema_and_the_budget_spec_name_a_quota_setting() -> None:
    assert quota_problems(ENGINE_ROOT) == []


def test_the_allowed_modules_do_name_the_quota_settings() -> None:
    """The check would be empty if the settings had moved: both allowed modules name them."""
    for name in ALLOWED:
        text = (ENGINE_ROOT / name).read_text(encoding="utf-8")
        assert "quota_places_enterprise" in text or "QUOTA_PLACES_ENTERPRISE" in text, name


def test_the_check_catches_a_key_attribute_and_a_setting_literal(tmp_path: Path) -> None:
    planted = tmp_path / PACKAGE / "app" / "planted.py"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "from nextron.domain.settings_schema import Key\n"
        "LIMIT = Key.QUOTA_PLACES_PRO\n"
        "NAME = 'quota_hunter_credits'\n"
        "MESSAGE = 'quota_for_sku takes a Places SKU'\n",  # prose, not a setting name
        encoding="utf-8",
    )
    for allowed in ALLOWED:  # the allowed modules are skipped
        kept = tmp_path / allowed
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text("NAME = 'quota_places_pro'\n", encoding="utf-8")

    assert quota_problems(tmp_path) == [
        "nextron/app/planted.py:2 Key.QUOTA_PLACES_PRO",
        "nextron/app/planted.py:3 'quota_hunter_credits'",
    ]
