"""Log event names stay under 32 characters (the *Logging* convention).

The first argument of a logging call (the second for ``.log(level, ...)``) is the event name.
A literal one of 32 characters or more fails here: redaction masks any run of 32
``[A-Za-z0-9_-]`` characters as ``<key>``, so a long snake_case event would reach the log file
as ``<key>``. The check reads every module under ``nextron/`` (AST) and looks at calls on
``log``, ``logger``, ``logging``, names ending ``_log`` or ``_logger``, and
``getLogger(...)``; the proofs run it on a temporary copy of the tree with a probe module.
"""

import ast
from pathlib import Path

import pytest

from tests.contracts.support import ENGINE_ROOT, copy_with_probe

LEVEL_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal"}
)
LIMIT = 32  # an event name must be shorter than this


def _is_logger(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name == "getLogger"
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    else:
        return False
    name = name.lower()
    return name in {"log", "logger", "logging"} or name.endswith(("_log", "_logger"))


def long_event_names(root: Path) -> list[str]:
    """Each logging call under ``root/nextron`` whose literal event name is too long."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        label = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if not _is_logger(node.func.value):
                continue
            method = node.func.attr
            index = 0 if method in LEVEL_METHODS else 1 if method == "log" else None
            if index is None or len(node.args) <= index:
                continue
            event = node.args[index]
            if isinstance(event, ast.Constant) and isinstance(event.value, str):
                if len(event.value) >= LIMIT:
                    problems.append(
                        f"{label}:{node.lineno} event {event.value!r} has {len(event.value)} "
                        f"characters; keep it under {LIMIT}"
                    )
    return problems


def test_every_literal_event_name_is_under_32_characters() -> None:
    assert long_event_names(ENGINE_ROOT) == []


PROBE = '''"""A probe module for the event-name check."""

import logging

log = logging.getLogger(__name__)


def run() -> None:
    {statement}
'''
PROBE_LINE = PROBE.splitlines().index("    {statement}") + 1
EVENT_32 = "lead_" + "x" * 27
EVENT_31 = "lead_" + "x" * 26


@pytest.mark.parametrize(
    "statement",
    [
        'log.info("discovery_places_text_search_completed")',
        f'log.warning("{EVENT_32}", extra={{"lead_id": "x"}})',
        f'logging.getLogger("nextron.x").error("{EVENT_32}")',
        f'logging.info("{EVENT_32}")',
        f'log.log(logging.INFO, "{EVENT_32}")',
        f'self_logger = log; self_logger.exception("{EVENT_32}")',
    ],
)
def test_the_check_catches_a_long_event_name(statement: str, tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app", PROBE.format(statement=statement))

    [problem] = long_event_names(root)

    assert problem.startswith(f"nextron/app/_lint_probe.py:{PROBE_LINE} event ")
    assert "keep it under 32" in problem


@pytest.mark.parametrize(
    "statement",
    [
        f'log.info("{EVENT_31}")',
        "log.info(EVENT_NAME_FROM_A_CONSTANT)",
        f'parser.error("{EVENT_32}")',
        f'log.info("short", extra={{"detail": "{EVENT_32}"}})',
    ],
)
def test_the_check_allows_short_or_non_literal_event_names(
    statement: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app", PROBE.format(statement=statement))

    assert long_event_names(root) == []
