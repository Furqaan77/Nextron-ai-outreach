"""Error contracts (the *Errors* convention): typed domain errors and wrapped adapter errors.

- Every concrete ``DomainError`` subclass declares its own ``code``: unique and matching
  ``^[a-z][a-z0-9_]*$``. ``DomainError.__init_subclass__`` refuses a bad class when it is
  defined; the backstop here imports every ``nextron.domain`` module and checks again, which
  also catches a code changed after definition.
- Every ``raise`` under ``nextron/adapters`` is ``raise AdapterError(...)`` or a subclass,
  optionally ``from`` a name or ``None``. The raised name must be imported from
  ``nextron.ports.errors`` (``AdapterError`` or a subclass defined there, under any alias, or
  through the module, as in ``errors.AdapterError``) or be a class in the same file that
  subclasses one. A bare ``raise``, ``raise exc`` or ``raise SomeSdkError(...)`` would let a
  third-party exception out unwrapped.
- The arguments may not carry the caught exception's text. An expression rooted at the caught
  exception is allowed only as a plain attribute chain ending in ``status_code``, ``errno``,
  ``code`` or ``retry_after`` (``exc.status_code``, ``exc.response.status_code``); anything else
  fails (``str(exc)``, ``f"{exc}"``, ``exc.args``, ``exc.response.json()``,
  ``exc.headers[...]``), and so does any ``traceback.*``, ``sys.exc_info()`` or
  ``sys.exception()`` call.
- **Taint tracking.** The text may not arrive through a local either. A local assigned from the
  caught exception's text (``m = str(exc)``, ``m = f"{exc}"``, ``m = exc.args``,
  ``m = repr(exc)``, by ``=``, an annotated or augmented assignment, ``:=``, a ``for`` or a
  comprehension target), directly or through another such local (``n = m.upper()``), is
  tainted for its whole function (and the functions nested in it), whatever the branch;
  passing it into the error fails. A local assigned from an allowed attribute
  (``status = exc.status_code``) is not tainted.

The adapter check reads the source (AST); the proofs run it on a temporary copy of the tree with
a probe module.
"""

import ast
import importlib
import pickle
import pkgutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

import nextron.domain
from nextron.domain.errors import CODE_PATTERN, DomainError, is_abstract
from nextron.ports.errors import AdapterError
from tests.contracts.support import ENGINE_ROOT, copy_with_probe

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
ADAPTER_ERROR = "AdapterError"
PORTS_ERRORS = "nextron.ports.errors"
# The attributes of a caught exception an adapter may put in its message: numbers, not text.
ALLOWED_ATTRIBUTES = frozenset({"status_code", "errno", "code", "retry_after"})
# Names that format the exception being handled, as imported from traceback or sys.
TRACEBACK_NAMES = frozenset(
    {"format_exc", "format_exception", "format_exception_only", "format_tb", "exc_info"}
)


# DomainError ----------------------------------------------------------------------------------


def all_subclasses(cls: type) -> list[type]:
    def walk(parent: type) -> Iterator[type]:
        for child in parent.__subclasses__():
            yield child
            yield from walk(child)

    return list(dict.fromkeys(walk(cls)))


def domain_error_code_problems(classes: Iterable[type[DomainError]]) -> list[str]:
    """Each concrete class must declare its own valid code, and no two may share one."""
    problems = []
    owners: dict[str, str] = {}
    for cls in classes:
        if is_abstract(cls):
            continue
        name = f"{cls.__module__}.{cls.__qualname__}"
        code = cls.__dict__.get("code")
        if code is None:
            problems.append(f"{name} declares no code")
        elif not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
            problems.append(f"{name} has code {code!r}; a code matches ^[a-z][a-z0-9_]*$")
        elif code in owners:
            problems.append(f"{name} reuses code {code!r} of {owners[code]}")
        else:
            owners[code] = name
    return problems


def import_every_domain_module() -> list[str]:
    names = [
        info.name for info in pkgutil.walk_packages(nextron.domain.__path__, "nextron.domain.")
    ]
    for name in names:
        importlib.import_module(name)
    return names


def test_every_domain_error_subclass_declares_a_unique_code() -> None:
    modules = import_every_domain_module()
    classes = sorted(
        (cls for cls in all_subclasses(DomainError) if cls.__module__.startswith("nextron.")),
        key=lambda cls: (cls.__module__, cls.__qualname__),
    )

    assert {"nextron.domain.errors", "nextron.domain.redaction"} <= set(modules)
    assert domain_error_code_problems(classes) == []


def test_the_backstop_catches_codes_changed_after_definition() -> None:
    class Missing(DomainError):
        code = "backstop_missing"

    class Malformed(DomainError):
        code = "backstop_malformed"

    class First(DomainError):
        code = "backstop_first"

    class Second(DomainError):
        code = "backstop_second"

    class Grouping(DomainError, abstract=True):
        pass

    del Missing.code
    Malformed.code = "Backstop-Malformed"
    Second.code = "backstop_first"

    problems = domain_error_code_problems([Missing, Malformed, First, Second, Grouping])

    assert [problem.split(" ", 1)[0].rsplit(".", 1)[1] for problem in problems] == [
        "Missing",
        "Malformed",
        "Second",
    ]
    assert problems[0].endswith("declares no code")
    assert "'Backstop-Malformed'" in problems[1]
    assert problems[2].endswith(f"reuses code 'backstop_first' of {__name__}.{First.__qualname__}")


@pytest.mark.parametrize(
    "body",
    [{}, {"code": "Lead-Suppressed"}, {"code": "lead_paused\n"}, {"code": 7}, {"code": ""}],
)
def test_a_subclass_without_a_valid_code_is_refused_when_defined(body: dict[str, Any]) -> None:
    with pytest.raises(TypeError, match="must declare its own code"):
        type("Refused", (DomainError,), body)


def test_a_subclass_that_only_inherits_a_code_is_refused() -> None:
    class Parent(DomainError):
        code = "guard_parent"

    with pytest.raises(TypeError, match="must declare its own code"):

        class Child(Parent):
            pass


def test_a_duplicate_code_is_refused_when_defined() -> None:
    class First(DomainError):
        code = "guard_duplicate"

    with pytest.raises(TypeError, match="reuses code 'guard_duplicate' of"):

        class Second(DomainError):
            code = "guard_duplicate"


def test_defining_the_same_class_again_is_allowed() -> None:
    """A module reload defines each class again under the same name."""
    for _ in range(2):

        class Reloaded(DomainError):
            code = "guard_reloaded"

    assert Reloaded.code == "guard_reloaded"


def test_an_abstract_base_groups_errors_but_cannot_be_raised() -> None:
    class LeadError(DomainError, abstract=True):
        pass

    class LeadSuppressed(LeadError):
        code = "guard_lead_suppressed"

    assert isinstance(LeadSuppressed("x"), LeadError)
    assert is_abstract(DomainError) and is_abstract(LeadError)
    assert not is_abstract(LeadSuppressed)
    with pytest.raises(TypeError, match="abstract"):
        LeadError("x")
    with pytest.raises(TypeError, match="abstract"):
        DomainError("x")


def test_a_domain_error_carries_its_code_and_message() -> None:
    class LeadOnSuppressionList(DomainError):
        code = "guard_lead_on_suppression_list"

    error = LeadOnSuppressionList(f"lead {ULID} is on the Suppression List")

    assert isinstance(error, Exception)
    assert error.code == "guard_lead_on_suppression_list"
    assert error.message == str(error) == f"lead {ULID} is on the Suppression List"


# AdapterError ---------------------------------------------------------------------------------


def test_adapter_error_stores_its_fields_and_redacts_the_message() -> None:
    error = AdapterError(True, "rate_limited", "429 from https://h.io/v1?key=abc for ann@x.co")

    assert (error.retryable, error.code) == (True, "rate_limited")
    assert error.message == "429 from https://h.io/v1 for <email>"
    assert str(error) == error.message
    assert "ann@x.co" not in repr(error)


def test_adapter_error_keeps_its_fields_through_pickling() -> None:
    error = AdapterError(False, "auth_failed", "the API key was rejected")

    copy = pickle.loads(pickle.dumps(error))

    assert (type(copy), copy.retryable, copy.code, copy.message) == (
        AdapterError,
        False,
        "auth_failed",
        "the API key was rejected",
    )


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (("yes", "rate_limited", "m"), TypeError),
        ((1, "rate_limited", "m"), TypeError),
        ((None, "rate_limited", "m"), TypeError),
        ((True, 429, "m"), TypeError),
        ((True, "Rate-Limited", "m"), ValueError),
        ((True, "", "m"), ValueError),
        ((True, "rate_limited\n", "m"), ValueError),
        ((True, "rate_limited", b"bytes"), TypeError),
        ((True, "rate_limited", None), TypeError),
    ],
)
def test_adapter_error_refuses_bad_arguments(
    arguments: tuple[object, object, object], error: type[Exception]
) -> None:
    with pytest.raises(error):
        AdapterError(*arguments)  # type: ignore[arg-type]


# The adapter raise check ----------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _subclass_closure(tree: ast.Module, names: set[str], is_base: Any) -> set[str]:
    """``names`` plus every class in ``tree`` whose base passes ``is_base(base, names)``."""
    names = set(names)
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    grew = True
    while grew:
        grew = False
        for cls in classes:
            if cls.name not in names and any(is_base(base, names) for base in cls.bases):
                names.add(cls.name)
                grew = True
    return names


def ports_error_classes(root: Path) -> set[str]:
    """``AdapterError`` and the classes ``nextron/ports/errors.py`` derives from it."""
    tree = _parse(root / "nextron" / "ports" / "errors.py")
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert ADAPTER_ERROR in defined

    def is_base(base: ast.expr, names: set[str]) -> bool:
        return isinstance(base, ast.Name) and base.id in names

    return _subclass_closure(tree, {ADAPTER_ERROR}, is_base)


class _Raisable:
    """The names under which one adapter module can reach ``AdapterError`` and its subclasses."""

    def __init__(self, tree: ast.Module, ports_classes: set[str]) -> None:
        self.ports_classes = ports_classes
        names: set[str] = set()
        self.modules: set[str] = set()  # names bound to the nextron.ports.errors module
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module == PORTS_ERRORS:
                    names |= {a.asname or a.name for a in node.names if a.name in ports_classes}
                elif node.module == "nextron.ports":
                    self.modules |= {a.asname or a.name for a in node.names if a.name == "errors"}
            elif isinstance(node, ast.Import):
                self.modules |= {a.asname or a.name for a in node.names if a.name == PORTS_ERRORS}
        self.names = _subclass_closure(tree, names, self._names)

    def _names(self, func: ast.expr, names: set[str]) -> bool:
        if isinstance(func, ast.Name):
            return func.id in names
        if isinstance(func, ast.Attribute) and func.attr in self.ports_classes:
            return ast.unparse(func.value) in self.modules
        return False

    def __contains__(self, func: ast.expr) -> bool:
        return self._names(func, self.names)


def _chain_base(node: ast.expr) -> ast.expr:
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node


def _is_allowed_attribute(node: ast.expr) -> bool:
    """A plain attribute chain on the caught exception that ends in an allowed attribute."""
    if not (isinstance(node, ast.Attribute) and node.attr in ALLOWED_ATTRIBUTES):
        return False
    base = node.value
    while isinstance(base, ast.Attribute):
        base = base.value
    return isinstance(base, ast.Name)


def _is_traceback_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in TRACEBACK_NAMES
    if isinstance(func, ast.Attribute):
        base = _chain_base(func)
        if isinstance(base, ast.Name) and base.id == "traceback":
            return True
        return isinstance(func.value, ast.Name) and func.value.id == "sys" and func.attr in {
            "exc_info",
            "exception",
        }
    return False


def _leaks(node: ast.AST, caught: frozenset[str]) -> bool:
    """Whether ``node`` carries the text of a caught exception (a name in ``caught``)."""
    if _is_traceback_call(node):
        return True
    if isinstance(node, ast.Name | ast.Attribute | ast.Subscript | ast.Call):
        base = _chain_base(node)
        if isinstance(base, ast.Name) and base.id in caught:
            return not _is_allowed_attribute(node)
    return any(_leaks(child, caught) for child in ast.iter_child_nodes(node))


class _Taint(ast.NodeVisitor):
    """The locals of one scope that carry a caught exception's text: assigned from it, directly
    or through another such local. A nested function or class is a scope of its own, scanned
    with ``known``: the names caught or tainted where it is defined."""

    def __init__(self, known: frozenset[str] = frozenset()) -> None:
        self.known = known
        self.caught: list[str] = []
        self.tainted: set[str] = set()
        self.changed = False

    def scan(self, body: list[ast.stmt]) -> frozenset[str]:
        """The tainted locals of ``body``, repeated until no new one turns up."""
        while True:
            self.changed = False
            for statement in body:
                self.visit(statement)
            if not self.changed:
                return frozenset(self.tainted)

    def visit_FunctionDef(self, node: ast.AST) -> None:
        """A nested scope: ``_RaiseChecker`` scans it on its own."""

    visit_AsyncFunctionDef = visit_ClassDef = visit_Lambda = visit_FunctionDef

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.caught.append(node.name)
        self.generic_visit(node)
        if node.name:
            self.caught.pop()

    def _taint(self, target: ast.AST | None, value: ast.AST | None) -> None:
        if target is None or value is None:
            return
        if not _leaks(value, self.known | frozenset(self.caught) | self.tainted):
            return
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and node.id not in self.tainted:
                self.tainted.add(node.id)
                self.changed = True

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._taint(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._taint(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._taint(node.target, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._taint(node.target, node.value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self._taint(node.target, node.iter)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._taint(node.target, node.iter)
        self.generic_visit(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        self._taint(node.optional_vars, node.context_expr)
        self.generic_visit(node)


def tainted_locals(body: list[ast.stmt], known: frozenset[str] = frozenset()) -> frozenset[str]:
    """The locals of the scope ``body`` that carry a caught exception's text; ``known`` are the
    names caught or tainted in the scopes around it."""
    return _Taint(known).scan(body)


class _RaiseChecker(ast.NodeVisitor):
    def __init__(self, label: str, raisable: _Raisable) -> None:
        self.label = label
        self.raisable = raisable
        self.caught: list[str] = []
        self.tainted: list[frozenset[str]] = []  # one entry per enclosing scope
        self.problems: list[str] = []

    def _scope(self, node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        known = frozenset(self.caught).union(*self.tainted)
        self.tainted.append(tainted_locals(node.body, known))
        self.generic_visit(node)
        self.tainted.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self._scope(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.caught.append(node.name)
        self.generic_visit(node)
        if node.name:
            self.caught.pop()

    def visit_Raise(self, node: ast.Raise) -> None:
        problem = self._problem(node)
        if problem:
            self.problems.append(f"{self.label}:{node.lineno} {problem}")
        self.generic_visit(node)

    def _problem(self, node: ast.Raise) -> str | None:
        if node.exc is None:
            return "bare raise re-raises the caught exception unwrapped"
        if not isinstance(node.exc, ast.Call) or node.exc.func not in self.raisable:
            return (
                f"raises {ast.unparse(node.exc)}, not AdapterError(...) imported from "
                f"{PORTS_ERRORS} or a local subclass of it"
            )
        cause = node.cause
        if not (
            cause is None
            or isinstance(cause, ast.Name)
            or (isinstance(cause, ast.Constant) and cause.value is None)
        ):
            return f"raises from {ast.unparse(cause)}; use from the caught exception or None"
        caught = frozenset(self.caught).union(*self.tainted)
        arguments = [*node.exc.args, *(keyword.value for keyword in node.exc.keywords)]
        if any(_leaks(argument, caught) for argument in arguments):
            return f"puts exception text into {ast.unparse(node.exc)}"
        return None


def adapter_raise_problems(root: Path) -> list[str]:
    """Each ``raise`` under ``root/nextron/adapters`` that breaks the rule, as ``file:line why``."""
    ports_classes = ports_error_classes(root)
    problems = []
    for path in sorted((root / "nextron" / "adapters").rglob("*.py")):
        tree = _parse(path)
        checker = _RaiseChecker(path.relative_to(root).as_posix(), _Raisable(tree, ports_classes))
        checker.visit(tree)
        problems += checker.problems
    return problems


def test_adapters_raise_only_adapter_errors() -> None:
    assert adapter_raise_problems(ENGINE_ROOT) == []


PROBE = '''"""A probe adapter module for the raise check."""

import sys
import traceback

{imports}


class SdkError(Exception):
    status_code = 429


class RateLimited(AdapterError):
    pass


def call() -> None:
    """Stands in for an SDK call."""


def wrapped() -> None:
    try:
        call()
    except SdkError as exc:
        {statement}
'''
PORTS_IMPORTS = "from nextron.ports import errors\nfrom nextron.ports.errors import AdapterError"
PROBE_LINE = PROBE.format(imports=PORTS_IMPORTS, statement="X").splitlines().index("        X") + 1
PROBE_FILE = "nextron/adapters/_lint_probe.py"


def probe(statement: str, imports: str = PORTS_IMPORTS) -> str:
    return PROBE.format(imports=imports, statement=statement)


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        ("raise", "bare raise"),
        ("raise exc", "raises exc, not AdapterError"),
        ("raise SdkError", "raises SdkError, not AdapterError"),
        ("raise SdkError('wrapped') from exc", "not AdapterError"),
        ("raise ValueError('bad input')", "not AdapterError"),
        ("raise AdapterError(True, 'sdk_failed', str(exc)) from exc", "text"),
        ("raise AdapterError(True, 'sdk_failed', repr(exc))", "text"),
        ("raise AdapterError(True, 'sdk_failed', f'call failed: {exc}') from exc", "text"),
        ("raise AdapterError(True, 'sdk_failed', f'call failed: {exc!r}')", "text"),
        ("raise AdapterError(True, 'sdk_failed', 'failed: %s' % exc)", "text"),
        ("raise AdapterError(True, 'sdk_failed', 'failed: {}'.format(exc))", "text"),
        ("raise AdapterError(True, 'sdk_failed', message=exc.args[0])", "text"),
        ("raise AdapterError(True, 'sdk_failed', exc.__str__())", "text"),
        ("raise RateLimited(True, 'rate_limited', f'{exc.message}') from exc", "text"),
        ("raise AdapterError(True, 'sdk_failed', exc.response.json()['error'])", "text"),
        ("raise AdapterError(True, 'sdk_failed', exc.headers['x-request-id'])", "text"),
        ("raise AdapterError(True, 'sdk_failed', f'{exc.response.text}')", "text"),
        ("raise AdapterError(True, 'sdk_failed', traceback.format_exc())", "text"),
        ("raise AdapterError(True, 'sdk_failed', str(sys.exc_info()[1]))", "text"),
        ("raise AdapterError(True, 'sdk_failed', repr(sys.exception()))", "text"),
        ("raise AdapterError(True, 'sdk_failed', 'failed') from exc.__cause__", "raises from"),
    ],
)
def test_the_raise_check_catches_an_unwrapped_or_leaky_raise(
    statement: str, reason: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", probe(statement))

    [problem] = adapter_raise_problems(root)

    assert problem.startswith(f"{PROBE_FILE}:{PROBE_LINE} ")
    assert reason in problem


@pytest.mark.parametrize(
    ("imports", "statement"),
    [
        (
            "from third_party_sdk import AdapterError",
            "raise AdapterError(True, 'sdk_failed', 'failed') from exc",
        ),
        (
            "import third_party_sdk as sdk\nfrom third_party_sdk import AdapterError",
            "raise sdk.AdapterError(True, 'sdk_failed', 'failed') from exc",
        ),
        (
            "import third_party_sdk as errors\nfrom nextron.ports.errors import AdapterError",
            "raise errors.AdapterError(True, 'sdk_failed', 'failed') from exc",
        ),
    ],
)
def test_the_raise_check_catches_an_adapter_error_from_elsewhere(
    imports: str, statement: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", probe(statement, imports))

    problems = adapter_raise_problems(root)

    assert problems, "an AdapterError not imported from nextron.ports.errors must fail"
    assert all("not AdapterError(...) imported from" in problem for problem in problems)


TAINTED = [
    "m = str(exc)\n        raise AdapterError(True, 'sdk_failed', m)",
    "m = f'call failed: {exc}'\n        raise AdapterError(True, 'sdk_failed', m) from exc",
    "m = exc.args\n        raise AdapterError(True, 'sdk_failed', str(m))",
    "m = exc.args[0]\n        raise AdapterError(True, 'sdk_failed', message=m)",
    "m = repr(exc)\n        raise RateLimited(True, 'rate_limited', f'{m}')",
    "m = repr(exc)\n        n = m.upper()\n        raise AdapterError(True, 'sdk_failed', n)",
    "text = str(exc)\n        parts = [text, 'x']\n        message = ' '.join(parts)\n"
    "        raise AdapterError(True, 'sdk_failed', message)",
    "m: str = exc.response.text\n        raise AdapterError(True, 'sdk_failed', m)",
    "m = 'failed: '\n        m += str(exc)\n        raise AdapterError(True, 'sdk_failed', m)",
    "if (m := str(exc)):\n            raise AdapterError(True, 'sdk_failed', m)",
    "for m in exc.args:\n            raise AdapterError(True, 'sdk_failed', m)",
    "found = [part for part in exc.args]\n        raise AdapterError(True, 'sdk_failed', found[0])",
    "alias = exc\n        raise AdapterError(True, 'sdk_failed', f'{alias}')",
    "m = traceback.format_exc()\n        raise AdapterError(True, 'sdk_failed', m)",
]


@pytest.mark.parametrize("statement", TAINTED)
def test_the_raise_check_catches_exception_text_carried_by_a_local(
    statement: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", probe(statement))

    [problem] = adapter_raise_problems(root)

    line = PROBE_LINE + statement.count("\n")
    assert problem.startswith(f"{PROBE_FILE}:{line} ")
    assert "puts exception text into" in problem


LATER_RAISE = '''"""A probe: the text is kept in the handler and raised after it."""

from nextron.ports.errors import AdapterError


def call() -> None:
    """Stands in for an SDK call."""


def wrapped() -> None:
    note = "the call failed"
    try:
        call()
    except OSError as exc:
        note = str(exc)
    if note:
        raise AdapterError(False, "os_failed", note)


def nested() -> None:
    try:
        call()
    except OSError as exc:
        detail = f"{exc}"

        def later() -> None:
            raise AdapterError(False, "os_failed", detail)

        later()
'''


def test_the_raise_check_follows_a_tainted_local_past_its_handler_and_into_a_closure(
    tmp_path: Path,
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", LATER_RAISE)

    problems = adapter_raise_problems(root)

    assert [problem.split(" ", 1)[0] for problem in problems] == [
        f"{PROBE_FILE}:17",
        f"{PROBE_FILE}:27",
    ]
    assert all("puts exception text into" in problem for problem in problems)


NESTED_TAINT = '''"""A probe: a nested function turns its enclosing handler's exception to text."""

from nextron.ports.errors import AdapterError


def call() -> None:
    """Stands in for an SDK call."""


def wrapped() -> None:
    try:
        call()
    except OSError as exc:

        def later() -> None:
            detail = str(exc)
            raise AdapterError(False, "os_failed", detail)

        later()
'''


def test_the_raise_check_taints_a_local_of_a_nested_function_from_the_enclosing_handler(
    tmp_path: Path,
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", NESTED_TAINT)

    [problem] = adapter_raise_problems(root)

    raise_line = '            raise AdapterError(False, "os_failed", detail)'
    line = NESTED_TAINT.splitlines().index(raise_line) + 1
    assert problem.startswith(f"{PROBE_FILE}:{line} ")
    assert "puts exception text into" in problem


CLEAN_LOCALS = [
    "status = exc.status_code\n        raise RateLimited(True, 'rate_limited', f'HTTP {status}')",
    "code = exc.response.status_code\n"
    "        raise AdapterError(True, 'rate_limited', f'HTTP {code}') from exc",
    "number = exc.errno\n        shown = number\n"
    "        raise AdapterError(False, 'os_failed', f'errno {shown}') from None",
    "m = 'the SDK call failed'\n        raise AdapterError(True, 'sdk_failed', m) from exc",
    "wait = exc.retry_after\n        raise AdapterError(True, 'rate_limited', f'wait {wait} s')",
]


@pytest.mark.parametrize("statement", CLEAN_LOCALS)
def test_the_raise_check_allows_a_local_from_an_allowed_attribute_or_own_text(
    statement: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", probe(statement))

    assert adapter_raise_problems(root) == []


def test_the_raise_check_reaches_adapter_subpackages(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters.ai", probe("raise"))

    [problem] = adapter_raise_problems(root)

    assert problem.startswith(f"nextron/adapters/ai/_lint_probe.py:{PROBE_LINE} bare raise")


@pytest.mark.parametrize(
    ("imports", "statement"),
    [
        (PORTS_IMPORTS, "raise AdapterError(True, 'sdk_failed', 'the SDK call failed') from exc"),
        (
            PORTS_IMPORTS,
            "raise AdapterError(retryable=True, code='sdk_failed', message='failed') from None",
        ),
        (PORTS_IMPORTS, "raise RateLimited(True, 'rate_limited', f'HTTP {exc.status_code}')"),
        (
            PORTS_IMPORTS,
            "raise AdapterError(True, 'rate_limited', f'HTTP {exc.response.status_code}') from exc",
        ),
        (PORTS_IMPORTS, "raise AdapterError(False, 'os_failed', f'errno {exc.errno}') from None"),
        (PORTS_IMPORTS, "raise errors.AdapterError(False, 'not_configured', 'no credential set')"),
        (
            "from nextron.ports.errors import AdapterError\n"
            "from nextron.ports.errors import AdapterError as PortError",
            "raise PortError(True, 'sdk_failed', 'failed') from exc",
        ),
        (
            "import nextron.ports.errors\nfrom nextron.ports.errors import AdapterError",
            "raise nextron.ports.errors.AdapterError(True, 'sdk_failed', 'failed') from exc",
        ),
    ],
)
def test_the_raise_check_allows_a_wrapped_raise(
    imports: str, statement: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.adapters", probe(statement, imports))

    assert adapter_raise_problems(root) == []
