"""Metering contracts (AD-8, AD-9, FR-57, NFR-12), each with a live proof on a probe.

- **One writer.** Only ``nextron/db/metering.py`` names the ``api_usage`` and ``ai_usage``
  tables or their records (``ApiUsageRecord``, ``AiUsageRecord``), besides the models, the
  migration and the two maps (``owners.py``, ``pii_registry.py``); a docstring may mention them.
  Only ``nextron/main.py`` imports ``nextron.db.metering``: everything else records through
  ``UsageMeterPort``.
- **No personal data fits.** No ``str`` field of ``ApiCall`` or ``AiCall`` accepts a value
  holding ``@`` or a space, so no free text or address can reach the usage tables.
- **Quotas are settings.** Every ``QUOTAS`` setting is a declared ``integer`` setting.
- **No paid Places use.** The Places quota settings' maximums are Google's free monthly
  allowances (decision 6: Enterprise 1,000 and Pro 5,000 requests), so no value can start paid
  use, and no other setting's key or label names Places except those ``PLACES_LIMITERS`` lists
  (Story 8.3's discovery settings, which can only narrow Places use: a location list and a page
  cap of at most 3 per run, never a quota).
"""

import ast
import dataclasses
import re
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest

from nextron.domain import settings_schema
from nextron.domain.budget import QUOTAS, AiCall, ApiCall
from nextron.domain.settings_schema import (
    REQUIRED,
    InvalidSettingValue,
    Section,
    Setting,
    SettingType,
    maximum,
    parse,
)
from tests.contracts.support import ENGINE_ROOT, PACKAGE

TABLE_WORDS: Final = re.compile(r"\b(?:api|ai)_usage\b")
RECORD_NAMES: Final = frozenset({"ApiUsageRecord", "AiUsageRecord"})
METER_MODULE: Final = "nextron.db.metering"
MODELS_MODULE: Final = "nextron.db.models.metering"
# The files that may name the usage tables or records.
TABLE_NAMERS: Final = frozenset(
    {
        "nextron/db/metering.py",  # the one writer, and the only SQL on them
        "nextron/db/models/metering.py",
        "nextron/db/models/__init__.py",  # imports every model
        "nextron/db/alembic/versions/0006_1_13_metering.py",
        "nextron/db/owners.py",  # the owner map
        "nextron/db/pii_registry.py",  # the PII map
    }
)
METER_IMPORTERS: Final = frozenset({"nextron/main.py"})
PLACES_ALLOWANCES: Final = {"quota_places_enterprise": 1_000, "quota_places_pro": 5_000}
# Settings that name Places but can only narrow its use, never raise a quota (Story 8.3)
PLACES_LIMITERS: Final = {
    "places_discovery_locations": "where a run may search; no request without one",
    "places_max_pages_per_run": "at most 3 pages per run, counted inside the quotas",
}


# One writer -------------------------------------------------------------------------------------


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every string constant that is a statement of its own (a docstring)."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def table_references(source: str) -> list[str]:
    """Each ``line: what`` in ``source`` that names a usage table or record, docstrings aside."""
    tree = ast.parse(source)
    skipped = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id in RECORD_NAMES:
            found.append(f"{line}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in RECORD_NAMES:
            found.append(f"{line}: {node.attr}")
        elif isinstance(node, ast.ImportFrom):
            if node.module == MODELS_MODULE:
                found.append(f"{line}: import from {MODELS_MODULE}")
            found += [f"{line}: import {a.name}" for a in node.names if a.name in RECORD_NAMES]
            if node.module == "nextron.db.models" and any(a.name == "metering" for a in node.names):
                found.append(f"{line}: import {MODELS_MODULE}")
        elif isinstance(node, ast.Import):
            if any(a.name == MODELS_MODULE for a in node.names):
                found.append(f"{line}: import {MODELS_MODULE}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
            and TABLE_WORDS.search(node.value)
        ):
            found.append(f"{line}: the text {node.value!r}")
    return sorted(set(found), key=lambda item: (int(item.split(":")[0]), item))


def meter_imports(source: str) -> list[str]:
    """Each ``line: import`` in ``source`` that imports ``nextron.db.metering``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"{node.lineno}: {a.name}" for a in node.names if a.name == METER_MODULE]
        elif isinstance(node, ast.ImportFrom):
            if node.module == METER_MODULE:
                found.append(f"{node.lineno}: from {METER_MODULE}")
            elif node.module == "nextron.db" and any(a.name == "metering" for a in node.names):
                found.append(f"{node.lineno}: from nextron.db import metering")
    return found


def tree_problems(root: Path) -> list[str]:
    """Each module under ``root/nextron`` that names the usage tables or imports the meter
    without being allowed to."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        if relative not in TABLE_NAMERS:
            problems += [f"{relative}:{found}" for found in table_references(source)]
        if relative not in METER_IMPORTERS and relative != "nextron/db/metering.py":
            problems += [f"{relative}:{found}" for found in meter_imports(source)]
    return problems


def test_only_the_meter_names_the_usage_tables_and_only_main_imports_it() -> None:
    assert tree_problems(ENGINE_ROOT) == []
    meter_source = (PACKAGE / "db" / "metering.py").read_text(encoding="utf-8")
    assert table_references(meter_source)  # the check sees the real writer's references
    assert meter_imports((PACKAGE / "main.py").read_text(encoding="utf-8"))


PROBE = '''"""A docstring may say api_usage and ApiUsageRecord."""
from sqlalchemy import insert, text
from nextron.db.models.metering import ApiUsageRecord
from nextron.db.models import metering
import nextron.db.models.metering
from nextron.db import metering as meter_module
from nextron.db.metering import UsageMeter


def sneak(session):
    """Nor here: ai_usage."""
    session.execute(insert(ApiUsageRecord).values(units=2))
    session.execute(text("DELETE FROM ai_usage"))
    return models.AiUsageRecord
'''


def test_the_checks_catch_a_probe_that_writes_the_usage_tables(tmp_path: Path) -> None:
    assert table_references(PROBE) == [
        "3: import ApiUsageRecord",
        "3: import from nextron.db.models.metering",
        "4: import nextron.db.models.metering",
        "5: import nextron.db.models.metering",
        "12: ApiUsageRecord",
        "13: the text 'DELETE FROM ai_usage'",
        "14: AiUsageRecord",
    ]
    assert meter_imports(PROBE) == [
        "6: from nextron.db import metering",
        "7: from nextron.db.metering",
    ]
    probe = tmp_path / "nextron" / "app" / "ops" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(PROBE, encoding="utf-8")
    problems = tree_problems(tmp_path)
    assert "nextron/app/ops/probe.py:13: the text 'DELETE FROM ai_usage'" in problems
    assert "nextron/app/ops/probe.py:7: from nextron.db.metering" in problems


# No personal data fits ----------------------------------------------------------------------------

BASELINES: Final[Mapping[type, Mapping[str, Any]]] = MappingProxyType(
    {
        ApiCall: {
            "provider": "hunter",
            "operation": "find",
            "ok": False,
            "error_code": "hunter_failed",
            "sku": "pro",
        },
        AiCall: {
            "route": "draft_email",
            "route_tuple": "draft_email:v1:claude-opus-5-5",
            "served_model": "claude-opus-5-5",
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_micros": 1,
            "error_code": "ai_failed",
        },
    }
)
HOSTILE: Final = ("mac@example.com", "a@b", "@", "a b", "Mac Smith", " x", "x ")


def _text_fields(cls: type) -> list[str]:
    """The fields of ``cls`` that take a ``str``."""
    hints = typing.get_type_hints(cls)
    names = []
    for found in dataclasses.fields(cls):
        hint = hints[found.name]
        options = typing.get_args(hint) if isinstance(hint, types.UnionType) else (hint,)
        if str in options:
            names.append(found.name)
    return names


def accepted_hostile_values(cls: type, baseline: Mapping[str, Any]) -> list[str]:
    """Each ``field=value`` of ``cls`` that accepts a value holding ``@`` or a space."""
    accepted = []
    for name in _text_fields(cls):
        for value in HOSTILE:
            try:
                cls(**{**baseline, name: value})
            except (TypeError, ValueError):
                continue
            accepted.append(f"{name}={value!r}")
    return accepted


@pytest.mark.parametrize("cls", [ApiCall, AiCall], ids=lambda cls: cls.__name__)
def test_no_text_field_of_a_call_record_accepts_an_at_sign_or_a_space(cls: type) -> None:
    baseline = BASELINES[cls]
    cls(**baseline)  # the baseline itself is valid

    assert set(_text_fields(cls)) <= set(baseline) | {"sku", "error_code"}
    assert accepted_hostile_values(cls, baseline) == []


@dataclasses.dataclass(frozen=True)
class ProbeCall:
    provider: str
    note: str | None = None

    def __post_init__(self) -> None:
        if " " in self.provider:
            raise ValueError("provider must hold no space")


def test_the_check_catches_a_probe_field_that_takes_free_text() -> None:
    accepted = accepted_hostile_values(ProbeCall, {"provider": "x"})

    assert "provider='mac@example.com'" in accepted  # an address gets through
    assert "provider='a b'" not in accepted
    assert "note='Mac Smith'" in accepted


# Quotas are settings ----------------------------------------------------------------------------


def test_every_quota_setting_is_a_declared_integer_setting() -> None:
    for spec in QUOTAS:
        found = settings_schema.setting(spec.setting)
        assert found.type is SettingType.INTEGER, spec.setting
        assert found.section is Section.BUDGET_AND_LIMITS, spec.setting
        assert found.default is not REQUIRED, spec.setting


# No paid Places use -------------------------------------------------------------------------------


def maximum_problems(schema: Mapping[str, Setting], allowances: Mapping[str, int]) -> list[str]:
    """Each Places setting whose largest accepted value isn't exactly its free allowance."""
    problems = []
    for key, allowance in allowances.items():
        found = schema.get(key)
        if found is None:
            problems.append(f"{key}: not declared")
            continue
        try:
            parse(found, str(allowance))
        except InvalidSettingValue:
            problems.append(f"{key}: refuses its allowance {allowance}")
        try:
            parse(found, str(allowance + 1))
        except InvalidSettingValue as refused:
            if refused.rule != "maximum":
                problems.append(f"{key}: {allowance + 1} is refused by {refused.rule}")
        else:
            problems.append(f"{key}: accepts {allowance + 1}, past the free allowance")
    return problems


def places_namers(schema: Mapping[str, Setting]) -> list[str]:
    """The keys of the settings whose key or label names Places."""
    return sorted(
        key
        for key, found in schema.items()
        if "places" in found.key.lower() or "places" in found.label.lower()
    )


def test_the_places_quota_maximums_are_googles_free_allowances() -> None:
    assert maximum_problems(settings_schema.SCHEMA, PLACES_ALLOWANCES) == []
    assert settings_schema.PLACES_ENTERPRISE_FREE_MONTHLY == 1_000
    assert settings_schema.PLACES_PRO_FREE_MONTHLY == 5_000


def test_no_other_setting_names_places() -> None:
    assert places_namers(settings_schema.SCHEMA) == sorted(
        [*PLACES_ALLOWANCES, *PLACES_LIMITERS]
    )
    pages = settings_schema.setting("places_max_pages_per_run")
    assert pages.type is SettingType.INTEGER and pages.default == 3
    assert maximum_problems(settings_schema.SCHEMA, {"places_max_pages_per_run": 3}) == []


def _setting(key: str, label: str, *rules: settings_schema.Rule) -> Setting:
    return Setting(
        key=key,
        type=SettingType.INTEGER,
        section=Section.BUDGET_AND_LIMITS,
        label=label,
        unit="requests",
        default=0,
        rules=rules,
    )


def _probe(*settings: Setting) -> Mapping[str, Setting]:
    return MappingProxyType({found.key: found for found in settings})


def test_the_places_checks_catch_a_probe_schema() -> None:
    probe = _probe(
        _setting("quota_places_enterprise", "Places Enterprise", maximum(2_000, "2,000")),
        _setting("quota_places_pro", "Places Pro", maximum(5_000, "5,000")),
        _setting("paid_places_spend", "Paid Places spend"),
        _setting("discovery_cap", "Google PLACES calls"),
    )

    assert maximum_problems(probe, PLACES_ALLOWANCES) == [
        "quota_places_enterprise: accepts 1001, past the free allowance"
    ]
    assert maximum_problems(_probe(), PLACES_ALLOWANCES) == [
        "quota_places_enterprise: not declared",
        "quota_places_pro: not declared",
    ]
    assert places_namers(probe) == [
        "discovery_cap",
        "paid_places_spend",
        "quota_places_enterprise",
        "quota_places_pro",
    ]
