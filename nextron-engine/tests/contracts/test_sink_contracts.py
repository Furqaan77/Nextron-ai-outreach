"""Sink contracts (AD-14, Story 1.8 AC 1): outside ``prod``, no live ``mail`` or ``alert``
adapter can be obtained. Each check runs on the real code and, as a live proof, on a probe that
breaks it.

- **Wrapped outside prod.** For every ``nextron.main.ROLE_ADAPTERS`` entry,
  ``build_role_adapter`` returns, in ``dev`` and ``test``, the role's sink (``MailSink`` or
  ``AlertSink``) around the adapter, and in ``prod`` the adapter itself. Each ``build`` is
  replaced by one that makes an ``adapter_type`` without calling its ``__init__``, so nothing
  live runs.
- **Every sender is registered.** Every class under ``nextron.adapters`` whose ``send`` takes an
  ``AlertMessage`` or a ``MailMessage`` (the sinks aside) is the ``adapter_type`` of a
  ``ROLE_ADAPTERS`` entry with that role. A ``send`` whose message parameter is unannotated,
  unresolvable, ``Any`` or ``object`` fails too ("annotate send's message parameter"): nothing
  could tell whether it sends mail or alerts.
- **Built only through the registry.** A sender module is any adapter module that defines or
  re-exports a sender, plus the packages between ``nextron.adapters`` and it. Every module under
  ``nextron/`` is scanned, with relative imports resolved: ``main.py`` may refer to sender
  modules, or anything imported from them, only inside the ``ROLE_ADAPTERS`` assignment; a
  module under ``nextron.adapters`` only to its own adapter package's; any other module (the
  CLI and the composition helpers included) to none. A star import of a sender module counts
  as a reference. ``ROLE_ADAPTERS`` is used only in its assignment and ``build_role_adapter``.
- **The sink reads every field.** ``AlertMessage`` and ``MailMessage`` have exactly the fields the
  sink checks and records. A new field (a CC recipient, say) fails here until the sink is
  reviewed: the sink would otherwise pass it on unchecked.
"""

import ast
import dataclasses
import importlib
import inspect
import json
import pkgutil
import types
import typing
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

import pytest

import nextron.adapters
from nextron import main
from nextron.adapters.sink import AlertSink, MailSink, SinkRecorder
from nextron.adapters.telegram import TelegramAlert
from nextron.bootstrap import load_bootstrap
from nextron.main import AdapterContext, RoleAdapter, SinkRole, build_role_adapter
from nextron.ports.alert import AlertDelivery, AlertMessage
from nextron.ports.mail import MailDelivery, MailMessage
from tests.contracts.support import PACKAGE
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.mail import InMemoryMail
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

SINKS: Final[dict[SinkRole, type]] = {SinkRole.MAIL: MailSink, SinkRole.ALERT: AlertSink}
MESSAGE_ROLES: Final[dict[type, SinkRole]] = {
    AlertMessage: SinkRole.ALERT,
    MailMessage: SinkRole.MAIL,
}
SINK_CLASSES: Final = frozenset({AlertSink, MailSink})
REGISTRY: Final = "ROLE_ADAPTERS"
BUILDER: Final = "build_role_adapter"
ENVIRONMENTS: Final = ("dev", "test", "prod")


# Wrapped outside prod ---------------------------------------------------------------------------


def _stub_build(adapter_type: type) -> Any:
    def build(context: AdapterContext) -> object:
        return adapter_type.__new__(adapter_type)  # an adapter_type, its __init__ never run

    return build


def unsunk(
    entries: Iterable[RoleAdapter], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[str]:
    """Each ``(entry, environment)`` where ``build_role_adapter`` doesn't return the role's
    sink around the adapter outside ``prod``, or the adapter itself in ``prod``."""
    stubs = tuple(
        dataclasses.replace(entry, build=_stub_build(entry.adapter_type)) for entry in entries
    )
    monkeypatch.setattr(main, REGISTRY, stubs)
    problems = []
    for env in ENVIRONMENTS:
        monkeypatch.setenv("NEXTRON_ENV", env)
        monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / env))
        context = AdapterContext(load_bootstrap(), InMemorySecrets(env), FrozenClock())
        for entry in stubs:
            adapter = build_role_adapter(entry.name, context, units=FakeUnits())
            if env == "prod":
                ok = type(adapter) is entry.adapter_type
            else:
                ok = type(adapter) is SINKS[entry.role] and (
                    type(getattr(adapter, "wrapped", None)) is entry.adapter_type
                )
            if not ok:
                problems.append(f"{entry.name} in {env}: got a {type(adapter).__name__}")
    return problems


class ProbeTelegram(InMemoryAlert):
    """A probe live alert adapter."""


class ProbeGmail(InMemoryMail):
    """A probe live mail adapter."""


PROBE_ENTRIES: Final = (
    RoleAdapter("probe_telegram", SinkRole.ALERT, ProbeTelegram, lambda context: None),
    RoleAdapter("probe_gmail", SinkRole.MAIL, ProbeGmail, lambda context: None),
)


def test_every_registered_adapter_is_sunk_outside_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert unsunk(main.ROLE_ADAPTERS, monkeypatch, tmp_path) == []


def test_the_wrapping_check_passes_probe_registrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert unsunk(PROBE_ENTRIES, monkeypatch, tmp_path) == []


def test_the_wrapping_check_catches_a_live_adapter_outside_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main, "sink_applies", lambda env: False)  # a broken composition root

    assert unsunk(PROBE_ENTRIES, monkeypatch, tmp_path) == [
        "probe_telegram in dev: got a ProbeTelegram",
        "probe_gmail in dev: got a ProbeGmail",
        "probe_telegram in test: got a ProbeTelegram",
        "probe_gmail in test: got a ProbeGmail",
    ]


def test_the_wrapping_check_catches_the_wrong_sink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    swapped = {SinkRole.MAIL: AlertSink, SinkRole.ALERT: MailSink}
    monkeypatch.setattr(main, "ROLE_SINKS", types.MappingProxyType(swapped))

    assert unsunk(PROBE_ENTRIES, monkeypatch, tmp_path) == [
        "probe_telegram in dev: got a MailSink",
        "probe_gmail in dev: got a AlertSink",
        "probe_telegram in test: got a MailSink",
        "probe_gmail in test: got a AlertSink",
    ]


# Every sender is registered ---------------------------------------------------------------------

UNCLEAR: Final = object()  # a send whose message parameter has no usable annotation


def adapter_modules() -> list[types.ModuleType]:
    """Every module under ``nextron.adapters``, imported."""
    return [
        nextron.adapters,
        *(
            importlib.import_module(info.name)
            for info in pkgutil.walk_packages(nextron.adapters.__path__, "nextron.adapters.")
        ),
    ]


def _message_annotation(cls: type) -> object | None:
    """The resolved annotation of ``cls.send``'s message parameter (its first after
    ``self``); ``UNCLEAR`` when it is missing, unresolvable, ``Any`` or ``object``; ``None``
    when ``cls`` has no callable ``send`` taking a parameter."""
    send = getattr(cls, "send", None)
    if not callable(send):
        return None
    try:
        parameters = list(inspect.signature(send).parameters.values())[1:]  # after self
    except (TypeError, ValueError):
        return UNCLEAR
    if not parameters:
        return None
    if parameters[0].annotation is inspect.Parameter.empty:
        return UNCLEAR
    try:
        annotation = typing.get_type_hints(send).get(parameters[0].name, UNCLEAR)
    except Exception:  # noqa: BLE001 - an annotation that can't be resolved can't be checked
        return UNCLEAR
    return UNCLEAR if annotation in (Any, object) else annotation


def role_of_send(cls: type) -> SinkRole | None:
    """The role whose message ``cls.send`` takes, if any."""
    annotation = _message_annotation(cls)
    for candidate in (annotation, *typing.get_args(annotation)):
        if candidate in MESSAGE_ROLES:
            return MESSAGE_ROLES[candidate]  # type: ignore[index]
    return None


def _classes_in(module: types.ModuleType) -> list[type]:
    """The classes ``module`` defines (the sinks aside)."""
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and value not in SINK_CLASSES
    ]


def senders_in(modules: Iterable[types.ModuleType]) -> dict[type, SinkRole]:
    """Each class defined in ``modules`` (the sinks aside) whose ``send`` takes a message."""
    return {
        cls: role
        for module in modules
        for cls in _classes_in(module)
        if (role := role_of_send(cls)) is not None
    }


def unclear_senders(modules: Iterable[types.ModuleType]) -> list[str]:
    """Each class defined in ``modules`` (the sinks aside) whose ``send`` takes a message
    parameter that is unannotated, unresolvable, ``Any`` or ``object``: it might send mail or
    alerts, and nothing could tell."""
    return [
        f"{cls.__module__}.{cls.__qualname__}: annotate send's message parameter"
        for module in modules
        for cls in _classes_in(module)
        if _message_annotation(cls) is UNCLEAR
    ]


def unregistered(senders: dict[type, SinkRole], entries: Iterable[RoleAdapter]) -> list[str]:
    registered = {(entry.adapter_type, entry.role) for entry in entries}
    return [
        f"{cls.__module__}.{cls.__qualname__} sends {role} messages but is not registered "
        f"for the {role} role"
        for cls, role in senders.items()
        if (cls, role) not in registered
    ]


def probe_module(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)  # noqa: S102 - a fixed probe
    return module


PROBE_ADAPTER = '''
from __future__ import annotations

from typing import Any

from nextron.ports.alert import AlertDelivery, AlertMessage
from nextron.ports.mail import MailDelivery, MailMessage


class TelegramAlert:
    def send(self, message: AlertMessage) -> AlertDelivery:
        return AlertDelivery("1")


class GmailMail:
    def send(self, message: MailMessage) -> MailDelivery:
        return MailDelivery("1", "1")


class Helper:
    def send(self, payload: bytes) -> None:
        return None


class Unannotated:
    def send(self, message):
        return None


class AnyMessage:
    def send(self, message: Any) -> None:
        return None


class ObjectMessage:
    def send(self, message: object) -> None:
        return None


class Unresolvable:
    def send(self, message: NoSuchMessage) -> None:
        return None
'''


def test_every_mail_and_alert_adapter_is_registered_for_its_role() -> None:
    modules = adapter_modules()
    senders = senders_in(modules)

    assert unclear_senders(modules) == []
    assert unregistered(senders, main.ROLE_ADAPTERS) == []
    assert set(senders).isdisjoint(SINK_CLASSES)


def test_telegram_is_the_only_live_sender_today() -> None:
    """Story 1.12 added the first live one, Telegram; this pins where things stand."""
    assert senders_in(adapter_modules()) == {TelegramAlert: SinkRole.ALERT}
    assert [(entry.name, entry.role, entry.adapter_type) for entry in main.ROLE_ADAPTERS] == [
        ("telegram", SinkRole.ALERT, TelegramAlert)
    ]
    assert role_of_send(AlertSink) is SinkRole.ALERT
    assert role_of_send(MailSink) is SinkRole.MAIL


def test_the_sender_check_finds_and_requires_a_probe_adapter() -> None:
    module = probe_module("nextron.adapters.probe_sender", PROBE_ADAPTER)
    senders = senders_in([module])
    alert, mail = module.TelegramAlert, module.GmailMail

    assert senders == {alert: SinkRole.ALERT, mail: SinkRole.MAIL}
    assert unclear_senders([module]) == [
        f"nextron.adapters.probe_sender.{name}: annotate send's message parameter"
        for name in ("Unannotated", "AnyMessage", "ObjectMessage", "Unresolvable")
    ]
    assert unregistered(senders, []) == [
        "nextron.adapters.probe_sender.TelegramAlert sends alert messages but is not "
        "registered for the alert role",
        "nextron.adapters.probe_sender.GmailMail sends mail messages but is not registered "
        "for the mail role",
    ]
    wrong_role = [
        RoleAdapter("t", SinkRole.MAIL, alert, lambda context: None),
        RoleAdapter("g", SinkRole.MAIL, mail, lambda context: None),
    ]
    assert len(unregistered(senders, wrong_role)) == 1
    right = [
        RoleAdapter("t", SinkRole.ALERT, alert, lambda context: None),
        RoleAdapter("g", SinkRole.MAIL, mail, lambda context: None),
    ]
    assert unregistered(senders, right) == []


# Built only through the registry ----------------------------------------------------------------

ADAPTERS_PACKAGE: Final = "nextron.adapters"


def sender_modules(modules: Iterable[types.ModuleType]) -> set[str]:
    """Every adapter module whose namespace exposes a sender (defining or re-exporting it),
    plus the packages between ``nextron.adapters`` and each module that defines one."""
    modules = list(modules)
    senders = senders_in(modules)
    names = {
        module.__name__
        for module in modules
        if any(isinstance(value, type) and value in senders for value in vars(module).values())
    }
    for cls in senders:
        parts = cls.__module__.split(".")
        names |= {".".join(parts[:end]) for end in range(3, len(parts))}
    return names


def _within(node: ast.AST) -> set[int]:
    return {id(child) for child in ast.walk(node)}


def _registry_value(tree: ast.Module) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == REGISTRY for target in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == REGISTRY
        ):
            return node.value
    return None


def _builder(tree: ast.Module) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == BUILDER:
            return node
    return None


def _assigns(tree: ast.Module, target: ast.Name, value: ast.AST) -> bool:
    """Whether ``target`` is the target of the statement whose value is ``value``."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.value is value:
            return any(child is target for child in node.targets)
        if isinstance(node, ast.AnnAssign) and node.value is value:
            return node.target is target
    return False


def _imported_module(node: ast.ImportFrom, package: str) -> str:
    """The absolute module ``node`` imports from, relative imports resolved in ``package``."""
    if not node.level:
        return node.module or ""
    base = package.split(".")
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _aliases(tree: ast.Module, package: str) -> dict[str, str]:
    """Each name ``tree``'s imports bind, mapped to the dotted path it is bound to."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _imported_module(node, package)
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
    return aliases


def _expanded(node: ast.AST, aliases: dict[str, str]) -> str | None:
    """The dotted path a ``Name`` or ``Attribute`` chain refers to through the imports."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _expanded(node.value, aliases)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _is_sender_path(path: str | None, sender_modules: set[str]) -> bool:
    return path is not None and any(
        path == module or path.startswith(f"{module}.") for module in sender_modules
    )


def stray_references(
    source: str,
    sender_modules: set[str],
    *,
    package: str = "nextron",
    registry_allowed: bool = True,
) -> list[str]:
    """Each reference to a sender module (or anything in one), and each star import of one,
    outside the ``ROLE_ADAPTERS`` assignment, and each use of ``ROLE_ADAPTERS`` outside its
    assignment and ``build_role_adapter``, as ``"<line>: <what>"``. Relative imports resolve
    in ``package``. With ``registry_allowed=False`` (any module but ``main.py``) every one is
    stray."""
    tree = ast.parse(source)
    value = _registry_value(tree) if registry_allowed else None
    builder = _builder(tree) if registry_allowed else None
    in_registry = _within(value) if value is not None else set()
    in_builder = _within(builder) if builder is not None else set()
    aliases = _aliases(tree, package)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            module = _imported_module(node, package)
            if _is_sender_path(module, sender_modules):
                problems.append(f"{node.lineno}: import * from {module}")
        if isinstance(node, ast.Name | ast.Attribute) and id(node) not in in_registry:
            inner = node.value if isinstance(node, ast.Attribute) else None
            if _is_sender_path(_expanded(node, aliases), sender_modules) and not (
                inner is not None and _is_sender_path(_expanded(inner, aliases), sender_modules)
            ):
                problems.append(f"{node.lineno}: {ast.unparse(node)} outside {REGISTRY}")
        is_registry = (isinstance(node, ast.Name) and node.id == REGISTRY) or (
            isinstance(node, ast.Attribute) and node.attr == REGISTRY
        )
        if is_registry and id(node) not in in_builder:
            stored_here = (
                value is not None
                and isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Store)
                and _assigns(tree, node, value)
            )
            if not stored_here:
                problems.append(f"{node.lineno}: {REGISTRY} outside {BUILDER}")
    return sorted(set(problems), key=lambda problem: (int(problem.split(":")[0]), problem))


def registry_problems(root: Path, senders: set[str]) -> list[str]:
    """``stray_references`` for every module under ``root/nextron``: ``main.py`` may use the
    ``ROLE_ADAPTERS`` assignment; a module under ``nextron.adapters`` may refer to sender
    modules of its own adapter package only; any other module to none."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root)
        parts = list(relative.with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_package else parts)
        package = module if is_package else module.rpartition(".")[0]
        visible = senders
        if module.startswith(f"{ADAPTERS_PACKAGE}."):
            own = ".".join(module.split(".")[:3])
            visible = {name for name in senders if not _is_sender_path(name, {own})}
        found = stray_references(
            path.read_text(encoding="utf-8"),
            visible,
            package=package,
            registry_allowed=module == "nextron.main",
        )
        problems += [f"{relative.as_posix()}:{problem}" for problem in found]
    return problems


def test_mail_and_alert_adapters_are_built_only_through_the_registry() -> None:
    source = (PACKAGE / "main.py").read_text(encoding="utf-8")
    senders = sender_modules(adapter_modules())

    assert senders == {"nextron.adapters.telegram"}
    assert registry_problems(PACKAGE.parent, senders) == []
    # Neither the jobs, the web, the app nor the startup checks refer to the Telegram package.
    others = [*(PACKAGE / "jobs").rglob("*.py"), *(PACKAGE / "web").rglob("*.py")]
    others += [*(PACKAGE / "app").rglob("*.py"), PACKAGE / "startup_checks.py"]
    for path in others:
        text = path.read_text(encoding="utf-8")
        assert "adapters.telegram" not in text and "TelegramAlert" not in text, path
    assert _registry_value(ast.parse(source)) is not None
    assert _builder(ast.parse(source)) is not None


PROBE_MAIN = """
import nextron.adapters.telegram
import nextron.adapters.telegram as tg
from nextron.adapters import telegram
from nextron.adapters.telegram import TelegramAlert, default_client

ROLE_ADAPTERS: tuple = (
    RoleAdapter("telegram", SinkRole.ALERT, TelegramAlert, lambda c: TelegramAlert(c.secrets)),
)


def build_role_adapter(name):
    return [entry for entry in ROLE_ADAPTERS if entry.name == name]


def build_app(secrets):
    first = TelegramAlert(secrets)
    second = telegram.TelegramAlert(secrets)
    third = nextron.adapters.telegram.TelegramAlert(secrets)
    fourth = tg.default_client()
    fifth = default_client()
    entries = ROLE_ADAPTERS
    ROLE_ADAPTERS2 = main.ROLE_ADAPTERS
"""
CLEAN_PROBE_MAIN = """
from nextron.adapters.telegram import TelegramAlert

ROLE_ADAPTERS: tuple = (
    RoleAdapter("telegram", SinkRole.ALERT, TelegramAlert, lambda c: TelegramAlert(c.secrets)),
)


def build_role_adapter(name):
    return [entry for entry in ROLE_ADAPTERS if entry.name == name]
"""
PROBE_IMPORTS = """
from nextron import adapters
from nextron.adapters.telegram import *
from .adapters.telegram import TelegramAlert as Relative

adapters.telegram.TelegramAlert(None)
Relative(None)
"""


def test_the_registry_check_catches_a_probe_built_outside_the_registry() -> None:
    modules = {"nextron.adapters.telegram"}

    assert stray_references(CLEAN_PROBE_MAIN, modules) == []
    assert stray_references(PROBE_MAIN, modules) == [
        "17: TelegramAlert outside ROLE_ADAPTERS",
        "18: telegram outside ROLE_ADAPTERS",
        "19: nextron.adapters.telegram outside ROLE_ADAPTERS",
        "20: tg outside ROLE_ADAPTERS",
        "21: default_client outside ROLE_ADAPTERS",
        "22: ROLE_ADAPTERS outside build_role_adapter",
        "23: ROLE_ADAPTERS outside build_role_adapter",
    ]
    assert stray_references(CLEAN_PROBE_MAIN, modules, registry_allowed=False) == [
        "4: ROLE_ADAPTERS outside build_role_adapter",
        "5: TelegramAlert outside ROLE_ADAPTERS",
        "10: ROLE_ADAPTERS outside build_role_adapter",
    ]
    assert stray_references(PROBE_IMPORTS, modules, registry_allowed=False) == [
        "3: import * from nextron.adapters.telegram",
        "6: adapters.telegram outside ROLE_ADAPTERS",
        "7: Relative outside ROLE_ADAPTERS",
    ]


def test_a_sender_re_exported_by_its_package_makes_the_package_a_sender_module() -> None:
    client = probe_module("nextron.adapters.probe_tg.client", PROBE_ADAPTER)
    package = types.ModuleType("nextron.adapters.probe_tg")
    package.TelegramAlert = client.TelegramAlert  # type: ignore[attr-defined]
    helper = types.ModuleType("nextron.adapters.probe_helpers")
    helper.Alert = client.TelegramAlert  # type: ignore[attr-defined]

    modules = sender_modules([package, client, helper])

    assert modules == {
        "nextron.adapters.probe_tg",
        "nextron.adapters.probe_tg.client",
        "nextron.adapters.probe_helpers",
    }
    assert sender_modules([client]) == {
        "nextron.adapters.probe_tg",
        "nextron.adapters.probe_tg.client",
    }
    source = "from nextron.adapters.probe_tg import TelegramAlert\nTelegramAlert(None)\n"
    assert stray_references(source, modules, registry_allowed=False) == [
        "2: TelegramAlert outside ROLE_ADAPTERS"
    ]


def test_the_registry_check_covers_every_module_of_a_probe_tree(tmp_path: Path) -> None:
    files = {
        "nextron/main.py": CLEAN_PROBE_MAIN,
        "nextron/startup_checks.py": (
            "from nextron.adapters.telegram import TelegramAlert\n"
            "def probe():\n"
            "    return TelegramAlert(None)\n"
        ),
        "nextron/adapters/__init__.py": "",
        "nextron/adapters/telegram/__init__.py": (
            "from .client import TelegramAlert\n"
            "DEFAULT = TelegramAlert\n"  # its own package may refer to it
        ),
        "nextron/adapters/telegram/client.py": "class TelegramAlert: ...\n",
        "nextron/adapters/gmail/__init__.py": (
            "from ..telegram import TelegramAlert\n"
            "ALERTS = TelegramAlert(None)\n"
        ),
        "nextron/adapters/gmail/mailer.py": "from nextron.adapters.gmail import ALERTS\n",
    }
    for relative, source in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(source, encoding="utf-8")
    senders = {"nextron.adapters.telegram", "nextron.adapters.telegram.client"}

    assert registry_problems(tmp_path, senders) == [
        "nextron/adapters/gmail/__init__.py:2: TelegramAlert outside ROLE_ADAPTERS",
        "nextron/startup_checks.py:3: TelegramAlert outside ROLE_ADAPTERS",
    ]


def test_the_registry_check_catches_a_second_assignment() -> None:
    source = CLEAN_PROBE_MAIN + "\nROLE_ADAPTERS = ()\n"

    assert stray_references(source, set()) == ["12: ROLE_ADAPTERS outside build_role_adapter"]


# The sink reads every field ---------------------------------------------------------------------

SINK_FIELDS: Final[dict[type, list[str]]] = {
    AlertMessage: ["recipient", "text"],
    MailMessage: ["recipient", "subject", "text"],
}


def field_problems(message_type: type, expected: list[str]) -> list[str]:
    actual = [field.name for field in dataclasses.fields(message_type)]
    return [
        f"{message_type.__name__}.{name} is not read by the sink; review it, then update this "
        "contract"
        for name in actual
        if name not in expected
    ] + [f"{message_type.__name__} lost {name}" for name in expected if name not in actual]


def test_the_messages_have_exactly_the_fields_the_sink_reads() -> None:
    for message_type, expected in SINK_FIELDS.items():
        assert field_problems(message_type, expected) == []
        assert [field.name for field in dataclasses.fields(message_type)] == expected
    assert [field.name for field in dataclasses.fields(AlertDelivery)] == ["message_id", "sink"]
    assert [field.name for field in dataclasses.fields(MailDelivery)] == [
        "message_id",
        "thread_id",
        "sink",
    ]


def test_the_sink_records_exactly_those_fields(tmp_path: Path) -> None:
    recorder = SinkRecorder(tmp_path, FrozenClock())
    AlertSink(InMemoryAlert(), tuple, recorder).send(AlertMessage("-1", "t"))
    MailSink(InMemoryMail(), tuple, recorder).send(MailMessage("a@b.co", "s", "t"))

    [alert_line, mail_line] = (tmp_path / "2026-11-16.jsonl").read_text("ascii").splitlines()
    assert list(json.loads(alert_line)) == ["time", "port", *SINK_FIELDS[AlertMessage]]
    assert list(json.loads(mail_line)) == ["time", "port", *SINK_FIELDS[MailMessage]]


@dataclasses.dataclass(frozen=True, slots=True)
class ProbeMailMessage:
    """``MailMessage`` as Epic 6 might extend it, with a second recipient."""

    recipient: str
    subject: str
    text: str
    cc: str


def test_the_field_check_catches_a_field_the_sink_would_ignore(tmp_path: Path) -> None:
    assert field_problems(ProbeMailMessage, SINK_FIELDS[MailMessage]) == [
        "ProbeMailMessage.cc is not read by the sink; review it, then update this contract"
    ]
    # Why it matters: the sink checks and records only the fields it knows.
    fake = InMemoryMail()
    sink = MailSink(fake, lambda: ["boss@nextron.example"], SinkRecorder(tmp_path, FrozenClock()))
    message: Any = ProbeMailMessage("boss@nextron.example", "s", "t", "ann@prospect.example")
    sink.send(message)
    assert fake.sent == [message]  # delivered, cc and all, with the cc never checked
