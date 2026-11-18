"""Erasure ledger contracts (FR-50, FR-52, AD-16, AD-17, Story 3.16), each with a probe proving
it catches a violation.

- **Its name stays put.** ``ERASURE_LEDGER_FILE`` (as a name, an attribute or an import) and the
  text ``erasure-ledger.jsonl`` (in a string that isn't a docstring) appear only in
  ``domain/backup.py`` (the definition), ``app/ops/api.py`` (the off-PC copy) and ``main.py``
  (the wiring, the doctor check and the restore's ledger recovery). Everything else gets the
  path handed to it, so only those modules and ``app/compliance/ledger.py`` reach the file.
- **Append-only.** ``app/compliance/ledger.py`` opens files only with mode ``"ab"`` or ``"rb"``
  (a literal, or a module constant holding one; no mode at all, which reads text, fails too;
  ``os.fdopen`` likewise), and ``os.open`` only with flags that include ``O_APPEND`` and none of
  ``O_CREAT``, ``O_TRUNC`` or ``O_RDWR`` (so an append never creates a missing ledger),
  and calls nothing that truncates, renames, replaces, removes or rewrites a file
  (``truncate``, ``unlink``, ``remove``, ``rename``, ``replace``, ``rmtree``, ``move``,
  ``discard``, ``write_text``, ``write_bytes``).
- **Nobody else rewrites it.** In the modules that name the ledger, every function that names it
  opens files only ``"ab"`` or ``"rb"`` and calls none of those either, except the restore's
  ``_recover_ledger`` in ``main.py``, which renames the decrypted ``.part`` file into place
  (never over an existing ledger).

The probes run the checks on small trees under ``tmp_path``, never on the real tree.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

from tests.contracts.support import PACKAGE

NAME = "ERASURE_LEDGER_FILE"
FILE_TEXT = "erasure-ledger.jsonl"
NAME_HOMES = frozenset({"domain/backup.py", "app/ops/api.py", "main.py"})
LEDGER_MODULE = "app/compliance/ledger.py"
MODES = frozenset({"ab", "rb"})
FORBIDDEN_CALLS = frozenset(
    {
        "truncate",
        "unlink",
        "remove",
        "rename",
        "replace",
        "rmtree",
        "move",
        "discard",
        "write_text",
        "write_bytes",
    }
)
RENAME_ALLOWED = ("main.py", "_recover_ledger", "rename")


def modules(root: Path) -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        yield relative, ast.parse(path.read_text(encoding="utf-8"))


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every bare string statement's constant (docstrings included)."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _names_ledger(node: ast.AST, docstrings: set[int]) -> bool:
    if isinstance(node, ast.Name) and node.id == NAME:
        return True
    if isinstance(node, ast.Attribute) and node.attr == NAME:
        return True
    if isinstance(node, ast.alias) and node.name == NAME:
        return True
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and FILE_TEXT in node.value
        and id(node) not in docstrings
    )


def name_problems(root: Path) -> list[str]:
    """Each place outside ``NAME_HOMES`` that names the erasure ledger."""
    problems = []
    for relative, tree in modules(root):
        if relative in NAME_HOMES:
            continue
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if _names_ledger(node, docstrings):
                line = getattr(node, "lineno", 0)
                problems.append(f"{relative}:{line} names the erasure ledger")
    return sorted(set(problems))


def _constants(tree: ast.Module) -> dict[str, object]:
    """Module-level ``NAME = <value>`` and ``NAME: T = <value>`` bindings: a constant's value,
    or any other expression's source text."""
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = _value(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            found[node.target.id] = _value(node.value)
    return found


def _value(node: ast.expr) -> object:
    return node.value if isinstance(node, ast.Constant) else ast.unparse(node)


def _is_os_call(node: ast.Call, name: str) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    )


def _flags_problem(node: ast.Call, constants: dict[str, object]) -> str | None:
    """Why an ``os.open`` call's flags could create, truncate or read-write, or ``None``."""
    flags = node.args[1] if len(node.args) > 1 else None
    if flags is None:
        return "no flags"
    text = str(constants.get(flags.id, flags.id)) if isinstance(flags, ast.Name) else (
        ast.unparse(flags)
    )
    if "O_APPEND" not in text or any(bad in text for bad in ("O_CREAT", "O_TRUNC", "O_RDWR")):
        return text
    return None


def _called(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _mode(node: ast.Call, constants: dict[str, object], position: int | None = None) -> object:
    """The mode an ``open`` call passes, or ``None`` when it passes none: ``open(path,
    mode)``, or ``path.open(mode)`` for a ``Path``."""
    if position is None:
        position = 0 if isinstance(node.func, ast.Attribute) else 1
    found: ast.expr | None = node.args[position] if len(node.args) > position else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            found = keyword.value
    if found is None:
        return None
    if isinstance(found, ast.Constant):
        return found.value
    if isinstance(found, ast.Name):
        return constants.get(found.id, f"<{found.id}>")
    return "<expression>"


def _call_problems(
    relative: str, where: str, body: ast.AST, constants: dict[str, object]
) -> list[str]:
    problems = []
    for node in ast.walk(body):
        if not isinstance(node, ast.Call):
            continue
        called = _called(node)
        if _is_os_call(node, "open"):
            if (problem := _flags_problem(node, constants)) is not None:
                problems.append(f"{relative}:{node.lineno} {where} opens with flags {problem}")
        elif _is_os_call(node, "fdopen"):
            mode = _mode(node, constants, position=1)
            if mode not in MODES:
                problems.append(f"{relative}:{node.lineno} {where} opens a file with mode {mode!r}")
        elif called == "open":
            mode = _mode(node, constants)
            if mode not in MODES:
                problems.append(f"{relative}:{node.lineno} {where} opens a file with mode {mode!r}")
        elif called in FORBIDDEN_CALLS and (relative, where, called) != RENAME_ALLOWED:
            problems.append(f"{relative}:{node.lineno} {where} calls {called}")
    return problems


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def rewrite_problems(root: Path) -> list[str]:
    """Each call that could truncate, rewrite or remove the ledger: anywhere in the ledger
    module, and in each function of ``NAME_HOMES`` that names the ledger."""
    problems = []
    for relative, tree in modules(root):
        constants = _constants(tree)
        if relative == LEDGER_MODULE:
            problems += _call_problems(relative, "module", tree, constants)
            continue
        if relative not in NAME_HOMES:
            continue
        docstrings = _docstrings(tree)
        for function in _functions(tree):
            inner = {
                id(node)
                for child in _functions(function)
                if child is not function
                for node in ast.walk(child)
            }
            names = any(
                _names_ledger(node, docstrings)
                for node in ast.walk(function)
                if id(node) not in inner
            )
            if names:
                problems += _call_problems(relative, function.name, function, constants)
    return sorted(set(problems))


def probe_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "nextron"
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


# The real tree ----------------------------------------------------------------------------------


def test_only_the_backup_ops_and_main_modules_name_the_ledger() -> None:
    assert name_problems(PACKAGE) == []


def test_nothing_truncates_rewrites_renames_over_or_removes_the_ledger() -> None:
    assert rewrite_problems(PACKAGE) == []


def test_the_checks_see_the_real_uses() -> None:
    """The real tree has what the checks look at, so an empty answer means something."""
    tree = ast.parse((PACKAGE / LEDGER_MODULE).read_text(encoding="utf-8"))
    opens = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _called(node) == "open"
    ]
    assert opens
    main = (PACKAGE / "main.py").read_text(encoding="utf-8")
    assert "def _recover_ledger" in main and NAME in main


# Probes ------------------------------------------------------------------------------------------


def test_the_name_check_catches_the_ledger_named_elsewhere(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "app/pipeline/api.py": (
                '"""Mentions erasure-ledger.jsonl in a docstring, which is fine."""\n'
                "from nextron.domain.backup import ERASURE_LEDGER_FILE\n"
                "from nextron.domain import backup\n"
                "A = backup.ERASURE_LEDGER_FILE\n"
                "B = 'C:/x/erasure-ledger.jsonl'\n"
            ),
            "main.py": "from nextron.domain.backup import ERASURE_LEDGER_FILE\n",
        },
    )

    assert name_problems(root) == [
        "app/pipeline/api.py:2 names the erasure ledger",
        "app/pipeline/api.py:4 names the erasure ledger",
        "app/pipeline/api.py:5 names the erasure ledger",
    ]


def test_the_rewrite_check_catches_a_bad_mode_or_call_in_the_ledger_module(
    tmp_path: Path,
) -> None:
    root = probe_tree(
        tmp_path,
        {
            LEDGER_MODULE: (
                "import os\n"
                "APPEND = 'ab'\n"
                "WRITE = 'wb'\n"
                "def ok(path):\n"
                "    open(path, APPEND).close()\n"
                "    open(path, mode='rb').close()\n"
                "def bad(path):\n"
                "    open(path, 'wb')\n"
                "    open(path, WRITE)\n"
                "    open(path)\n"
                "    path.open('r+b')\n"
                "    path.unlink()\n"
                "    os.replace(path, path)\n"
                "    path.write_bytes(b'')\n"
                "    handle = open(path, 'ab')\n"
                "    handle.truncate(0)\n"
            ),
        },
    )

    assert rewrite_problems(root) == sorted(
        [
            f"{LEDGER_MODULE}:8 module opens a file with mode 'wb'",
            f"{LEDGER_MODULE}:9 module opens a file with mode 'wb'",
            f"{LEDGER_MODULE}:10 module opens a file with mode None",
            f"{LEDGER_MODULE}:11 module opens a file with mode 'r+b'",
            f"{LEDGER_MODULE}:12 module calls unlink",
            f"{LEDGER_MODULE}:13 module calls replace",
            f"{LEDGER_MODULE}:14 module calls write_bytes",
            f"{LEDGER_MODULE}:16 module calls truncate",
        ]
    )


def test_the_rewrite_check_catches_a_function_that_names_and_removes_the_ledger(
    tmp_path: Path,
) -> None:
    root = probe_tree(
        tmp_path,
        {
            "main.py": (
                "from nextron.domain.backup import ERASURE_LEDGER_FILE\n"
                "def tidy(folder):\n"
                "    (folder / ERASURE_LEDGER_FILE).unlink()\n"
                "def _recover_ledger(part, folder):\n"
                "    part.rename(folder / ERASURE_LEDGER_FILE)\n"
                "def recover_elsewhere(part, folder):\n"
                "    part.rename(folder / ERASURE_LEDGER_FILE)\n"
                "def unrelated(path):\n"
                "    path.unlink()\n"
            ),
            "app/ops/api.py": (
                "from nextron.domain.backup import ERASURE_LEDGER_FILE\n"
                "def copy(folder):\n"
                "    open(folder / ERASURE_LEDGER_FILE, 'w')\n"
            ),
        },
    )

    assert rewrite_problems(root) == [
        "app/ops/api.py:3 copy opens a file with mode 'w'",
        "main.py:3 tidy calls unlink",
        "main.py:7 recover_elsewhere calls rename",
    ]


def test_the_rewrite_check_lets_os_open_only_append_to_an_existing_file(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            LEDGER_MODULE: (
                "import os\n"
                "FLAGS = os.O_WRONLY | os.O_APPEND\n"
                "CREATE = os.O_WRONLY | os.O_APPEND | os.O_CREAT\n"
                "def ok(path):\n"
                "    fd = os.open(path, FLAGS)\n"
                "    os.fdopen(fd, 'ab')\n"
                "def bad(path):\n"
                "    os.open(path, CREATE)\n"
                "    os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_APPEND)\n"
                "    os.open(path, os.O_WRONLY)\n"
                "    os.fdopen(3, 'wb')\n"
            ),
        },
    )

    assert rewrite_problems(root) == sorted(
        [
            f"{LEDGER_MODULE}:8 module opens with flags os.O_WRONLY | os.O_APPEND | os.O_CREAT",
            f"{LEDGER_MODULE}:9 module opens with flags os.O_WRONLY | os.O_TRUNC | os.O_APPEND",
            f"{LEDGER_MODULE}:10 module opens with flags os.O_WRONLY",
            f"{LEDGER_MODULE}:11 module opens a file with mode 'wb'",
        ]
    )
