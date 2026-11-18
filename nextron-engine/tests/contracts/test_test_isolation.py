"""Test isolation contracts (AD-14, Story 1.8 AC 6): every test runs as ``NEXTRON_ENV=test``
on a temporary data dir, every port resolves to its fake, constructing a live adapter raises,
every adapter package is classified, and nothing reaches the network. Where a check could
silently pass, it also runs on a probe that breaks it.

The guards themselves live in ``tests/live_guard.py`` and are applied by ``tests/conftest.py``.

**Engines a test starts (Story 4.5, decision 10).** A child process gets none of pytest's
guards, so every test module that starts the engine passes the child's environment through
``tests.live_guard.child_env``: ``python -m nextron`` directly, ``uv run ... nextron``, or
``scripts/run.ps1`` (Story 1.21's supervisor, whose default command is ``uv run --frozen python
-m nextron serve`` and whose child inherits its environment); and a child started with it can't
look up or reach a real host (or does not start at all when the guard can't be installed),
while loopback still works.
"""

import asyncio
import importlib
import inspect
import os
import pkgutil
import re
import socket
import subprocess
import sys
import types
import typing
import urllib.request
import webbrowser
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import pytest
from keyring.backends.Windows import WinVaultKeyring

import nextron.adapters.google_oauth as google_oauth_adapter
import nextron.ports
from nextron import main
from nextron.adapters.secrets import KeyringSecrets
from nextron.bootstrap import load_bootstrap
from nextron.main import AdapterContext, RoleAdapter, SinkRole, build_role_adapter
from tests.contracts.support import ENGINE_ROOT, PACKAGE
from tests.contracts.test_sink_contracts import senders_in
from tests.fakes import PORT_FAKES
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import (
    ADAPTER_PACKAGES,
    BROWSER_CALLS,
    CREDENTIAL_MANAGER_CALLS,
    LIVE_CONSTRUCTORS,
    Guarded,
    LiveAdapterInTests,
    NetworkInTests,
    NoExternalSystem,
    NotYetBuilt,
    allow_live_constructor,
    child_env,
    guard_live_constructors,
    is_local_host,
    is_refused,
)

ADAPTERS: Final = PACKAGE / "adapters"
TEST_NET: Final = "192.0.2.1"  # RFC 5737 TEST-NET-1: never routed


# The environment --------------------------------------------------------------------------------


def test_every_test_runs_as_test_on_a_temporary_data_dir(
    local_app_data: Path, tmp_path: Path
) -> None:
    assert os.environ["NEXTRON_ENV"] == "test"
    assert [name for name in os.environ if name.upper().startswith("NEXTRON_")] == [
        "NEXTRON_ENV"
    ]
    assert os.environ["LOCALAPPDATA"] == str(local_app_data)
    assert local_app_data.is_relative_to(tmp_path)
    config = load_bootstrap()
    assert config.env == "test"
    assert config.data_dir == local_app_data / "Nextron" / "test"
    assert config.port == 8767
    assert list(local_app_data.iterdir()) == []


# Every port resolves to its fake ----------------------------------------------------------------


def port_protocols(modules: Iterable[types.ModuleType]) -> set[type]:
    """Every Protocol class defined in ``modules``."""
    return {
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and typing.is_protocol(value)
    }


def port_modules() -> list[types.ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.walk_packages(nextron.ports.__path__, "nextron.ports.")
    ]


def _parameters(function: Any) -> list[str]:
    names = list(inspect.signature(function).parameters)
    return names[1:] if names[:1] == ["self"] else names


def fake_problems(protocols: set[type], fakes: Mapping[type, type]) -> list[str]:
    """Each port without a fake, fake without a port, or fake that doesn't match its port's
    methods (by name and parameter names) or can't be built with no arguments."""
    problems = [f"{port.__name__} has no fake" for port in protocols - set(fakes)]
    problems += [f"{port.__name__} is not a port" for port in set(fakes) - protocols]
    for port, fake in fakes.items():
        if port not in protocols:
            continue
        for member in sorted(typing.get_protocol_members(port)):
            implemented = getattr(fake, member, None)
            if not callable(implemented):
                problems.append(f"{fake.__name__} lacks {port.__name__}.{member}")
            elif _parameters(implemented) != _parameters(getattr(port, member)):
                problems.append(f"{fake.__name__}.{member} has other parameters than the port")
        try:
            instance = fake()
        except TypeError:
            problems.append(f"{fake.__name__}() needs arguments")
            continue
        if not isinstance(instance, port):
            problems.append(f"{fake.__name__} does not satisfy {port.__name__}")
    return sorted(problems)


def test_every_port_has_a_fake_that_satisfies_it() -> None:
    protocols = port_protocols(port_modules())

    assert {port.__name__ for port in protocols} == {
        "AlertPort",
        "BackupTargetPort",
        "ClientFilePort",
        "Clock",
        "ConsentListener",
        "CrawlerPort",
        "EmailSyntaxPort",
        "GoogleOAuthPort",
        "LlmPort",
        "MailPort",
        "RendererPort",
        "SecretsPort",
        "TokenCodecPort",
        "UsageMeterPort",
    }
    assert fake_problems(protocols, PORT_FAKES) == []


@runtime_checkable
class ProbePort(Protocol):
    def fetch(self, key: str) -> str: ...


class ProbeFakeWithoutFetch:
    pass


class ProbeFakeWithOtherParameters:
    def fetch(self, name: str) -> str:
        return name


class ProbeFakeNeedingArguments:
    def __init__(self, store: dict[str, str]) -> None:
        self.store = store

    def fetch(self, key: str) -> str:
        return self.store[key]


def test_the_fake_check_catches_a_probe_port_without_a_good_fake() -> None:
    protocols = {ProbePort}

    assert fake_problems(protocols, {}) == ["ProbePort has no fake"]
    assert fake_problems(protocols, {ProbePort: ProbeFakeWithoutFetch}) == [
        "ProbeFakeWithoutFetch does not satisfy ProbePort",
        "ProbeFakeWithoutFetch lacks ProbePort.fetch",
    ]
    assert fake_problems(protocols, {ProbePort: ProbeFakeWithOtherParameters}) == [
        "ProbeFakeWithOtherParameters.fetch has other parameters than the port"
    ]
    assert fake_problems(protocols, {ProbePort: ProbeFakeNeedingArguments}) == [
        "ProbeFakeNeedingArguments() needs arguments"
    ]
    assert fake_problems(set(), {ProbePort: ProbeFakeWithoutFetch}) == [
        "ProbePort is not a port"
    ]


# Live constructors raise ------------------------------------------------------------------------


def test_the_keyring_adapter_cant_be_built_without_an_injected_backend() -> None:
    with pytest.raises(LiveAdapterInTests):
        KeyringSecrets("test")
    with pytest.raises(LiveAdapterInTests):
        KeyringSecrets("prod")

    injected = KeyringSecrets("test", backend=object())  # type: ignore[arg-type]
    assert injected.service == "nextron-engine/test"  # an injected backend isn't live


def test_the_composition_root_cant_build_the_live_keyring() -> None:
    config = load_bootstrap()

    with pytest.raises(LiveAdapterInTests):
        main.build_app(config, units=FakeUnits())
    with pytest.raises(LiveAdapterInTests):
        main.build_entry(config)

    main.build_app(config, units=FakeUnits(), secrets=InMemorySecrets("test"))  # injected: fine


def test_every_live_constructor_is_refused_now() -> None:
    for module, name in LIVE_CONSTRUCTORS:
        assert is_refused(getattr(importlib.import_module(module), name)), f"{module}.{name}"
    for entry in main.ROLE_ADAPTERS:
        assert is_refused(entry.build), entry.name


def test_a_registered_role_adapter_cant_be_built_until_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[InMemoryAlert] = []

    def build(context: AdapterContext) -> InMemoryAlert:
        built.append(InMemoryAlert())
        return built[-1]

    probe = RoleAdapter("probe_telegram", SinkRole.ALERT, InMemoryAlert, build)
    monkeypatch.setattr(main, "ROLE_ADAPTERS", (probe,))
    guard_live_constructors(monkeypatch)  # as the autouse fixture does for real registrations
    clock = FrozenClock()
    context = AdapterContext(
        load_bootstrap(), InMemorySecrets("test"), clock, InMemoryUsageMeter(clock)
    )

    with pytest.raises(LiveAdapterInTests, match="role adapter probe_telegram"):
        build_role_adapter("probe_telegram", context, units=FakeUnits())
    assert built == []

    allow_live_constructor(monkeypatch, "nextron.main", "probe_telegram")
    adapter = build_role_adapter("probe_telegram", context, units=FakeUnits())

    assert [getattr(adapter, "wrapped", None)] == built


def test_guarding_twice_still_restores_the_real_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard_live_constructors(monkeypatch)  # a second time, on top of the fixture's

    allow_live_constructor(monkeypatch, "nextron.adapters.secrets", "default_backend")

    import nextron.adapters.secrets as secrets_module  # noqa: PLC0415

    assert not is_refused(secrets_module.default_backend)
    assert secrets_module.default_backend.__module__ == "nextron.adapters.secrets"


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("get_password", ("nextron-engine/test", "probe")),
        ("set_password", ("nextron-engine/test", "probe", "value")),
        ("delete_password", ("nextron-engine/test", "probe")),
        ("get_credential", ("nextron-engine/test", "probe")),
    ],
)
def test_credential_manager_cant_be_called_even_on_a_directly_built_backend(
    method: str, arguments: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert method in CREDENTIAL_MANAGER_CALLS

    with pytest.raises(LiveAdapterInTests, match="Credential Manager"):
        getattr(WinVaultKeyring(), method)(*arguments)

    allow_live_constructor(monkeypatch, "nextron.adapters.secrets", "default_backend")
    import nextron.adapters.secrets as secrets_module  # noqa: PLC0415

    backend = secrets_module.default_backend()  # the real factory, restored
    assert type(backend) is WinVaultKeyring
    with pytest.raises(LiveAdapterInTests, match="Credential Manager"):
        getattr(backend, method)(*arguments)  # its calls stay refused


def test_the_google_http_client_cant_be_built() -> None:
    assert ("nextron.adapters.google_oauth", "default_http_client") in LIVE_CONSTRUCTORS
    with pytest.raises(LiveAdapterInTests):
        google_oauth_adapter.default_http_client()
    meter = InMemoryUsageMeter()
    client = google_oauth_adapter.GoogleOAuthClient(meter=meter)  # lazy: building reaches nothing
    with pytest.raises(LiveAdapterInTests):
        client.revoke("token")
    assert meter.records == []  # no request was attempted, so none was recorded


@pytest.mark.parametrize("name", BROWSER_CALLS)
def test_no_test_can_open_the_default_browser(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(BROWSER_CALLS) == {"open", "open_new", "open_new_tab"}

    with pytest.raises(LiveAdapterInTests, match="default browser"):
        getattr(webbrowser, name)("http://127.0.0.1:1/")
    with pytest.raises(LookupError):
        allow_live_constructor(monkeypatch, "webbrowser", name)
    with pytest.raises(LiveAdapterInTests, match="default browser"):
        main.open_in_browser("http://127.0.0.1:1/")  # it looks webbrowser.open up when called


def test_only_guarded_constructors_can_be_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LookupError):
        allow_live_constructor(monkeypatch, "nextron.adapters.clock", "SystemClock")
    with pytest.raises(LookupError):
        allow_live_constructor(monkeypatch, "nextron.main", "no_such_adapter")


# Every adapter package is classified ------------------------------------------------------------


def adapter_packages(folder: Path = ADAPTERS) -> set[str]:
    return {
        entry.name
        for entry in folder.iterdir()
        if entry.is_dir() and entry.name != "__pycache__" and any(entry.rglob("*.py"))
    }


def _public_names(module: types.ModuleType) -> list[str]:
    return sorted(name for name in vars(module) if not name.startswith("_"))


def package_modules(name: str) -> list[types.ModuleType]:
    """The package ``name`` and every module under it, imported; the package first."""
    package = importlib.import_module(name)
    return [
        package,
        *(
            importlib.import_module(info.name)
            for info in pkgutil.walk_packages(getattr(package, "__path__", []), f"{name}.")
        ),
    ]


def _contents(folder: Path) -> list[str]:
    """Every file and folder under ``folder``, relative, ``__pycache__`` aside."""
    return sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if "__pycache__" not in path.relative_to(folder).parts
    )


def classification_problems(
    packages: set[str],
    classified: Mapping[str, object],
    live_constructors: Iterable[tuple[str, str]],
    modules_of: Callable[[str], list[types.ModuleType]] = package_modules,
    folder: Path = ADAPTERS,
) -> list[str]:
    """Each adapter package that is unclassified or misclassified; see the module docstring of
    ``tests/live_guard.py``. ``modules_of(name)`` gives a package and all its modules, the
    package first."""
    live = set(live_constructors)
    problems = [f"{name} is not classified" for name in sorted(packages - set(classified))]
    problems += [f"{name} is classified but missing" for name in sorted(set(classified) - packages)]
    listed: set[tuple[str, str]] = set()
    for name in sorted(packages & set(classified)):
        kind = classified[name]
        module_name = f"nextron.adapters.{name}"
        modules = modules_of(module_name)
        module = modules[0]
        if isinstance(kind, Guarded):
            if not kind.constructors:
                problems.append(f"{name} is guarded but lists no constructor")
            for constructor in kind.constructors:
                listed.add((module_name, constructor))
                if not hasattr(module, constructor):
                    problems.append(f"{name} has no {constructor}")
                if (module_name, constructor) not in live:
                    problems.append(f"{module_name}.{constructor} is not in LIVE_CONSTRUCTORS")
        elif isinstance(kind, NoExternalSystem):
            if not kind.reason.strip():
                problems.append(f"{name} gives no reason it reaches no external system")
            if senders_in(modules):  # the sinks themselves aren't senders
                problems.append(f"{name} sends mail or alerts, so it is live: guard it")
        elif isinstance(kind, NotYetBuilt):
            defined = sorted(
                f"{each.__name__}.{public}" for each in modules for public in _public_names(each)
            )
            if defined:
                problems.append(f"{name} is not built yet but defines {defined}")
            contents = _contents(folder / name)
            if contents != ["__init__.py"]:
                problems.append(f"{name} is not built yet but holds {contents}")
        else:
            problems.append(f"{name} has no valid classification")
    problems += [
        f"{module}.{constructor} is in LIVE_CONSTRUCTORS but no guarded package lists it"
        for module, constructor in sorted(live - listed)
    ]
    return problems


def test_every_adapter_package_is_classified_once() -> None:
    packages = adapter_packages()

    assert packages == {
        "ai",
        "backup_target",
        "clock",
        "crawler",
        "crypto",
        "email_syntax",
        "google_oauth",
        "renderer",
        "secrets",
        "sink",
        "telegram",
    }
    assert classification_problems(packages, ADAPTER_PACKAGES, LIVE_CONSTRUCTORS) == []


def test_the_ai_package_is_guarded_and_a_gateway_reaches_no_claude_api() -> None:
    import nextron.adapters.ai as ai  # noqa: PLC0415
    from nextron.domain.ai import Effort, RouteTuple, UntrustedBlock, Urgency  # noqa: PLC0415

    assert ADAPTER_PACKAGES["ai"] == Guarded(("default_http_client",))
    assert ("nextron.adapters.ai", "default_http_client") in LIVE_CONSTRUCTORS
    with pytest.raises(LiveAdapterInTests):
        ai.default_http_client()
    secrets, meter = InMemorySecrets("test"), InMemoryUsageMeter()
    secrets.write("anthropic/api_key", "sk-ant-test-key")
    gateway = ai.ClaudeGateway(secrets, meter=meter)  # builds no client
    route = RouteTuple("extract_people", "claude-haiku-5-5", Effort.MEDIUM, "v1", 1, "Find.")
    block = UntrustedBlock("lead:01JD3QZ8X5V9K2M7N4P6R8T0WY", "page", "p1", "text")

    with pytest.raises(LiveAdapterInTests):
        gateway.run(route, [block], Urgency.BULK)  # its first run looks the client up

    assert meter.calls == []


def test_the_classification_check_catches_probe_packages(tmp_path: Path) -> None:
    names = (
        "ai",
        "backup_target",
        "clock",
        "crawler",
        "crypto",
        "email_syntax",
        "google_oauth",
        "renderer",
        "secrets",
        "sink",
    )
    for name in (*names, "telegram", "gmail"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("")
    (tmp_path / "ai" / "gateway.py").write_text("")
    modules = {
        "nextron.adapters.ai": [probe_module("nextron.adapters.ai", "route = 'haiku'")],
        "nextron.adapters.backup_target": package_modules("nextron.adapters.backup_target"),
        "nextron.adapters.clock": package_modules("nextron.adapters.clock"),
        "nextron.adapters.crawler": package_modules("nextron.adapters.crawler"),
        "nextron.adapters.crypto": package_modules("nextron.adapters.crypto"),
        "nextron.adapters.email_syntax": package_modules("nextron.adapters.email_syntax"),
        "nextron.adapters.google_oauth": package_modules("nextron.adapters.google_oauth"),
        "nextron.adapters.renderer": package_modules("nextron.adapters.renderer"),
        "nextron.adapters.secrets": package_modules("nextron.adapters.secrets"),
        "nextron.adapters.sink": package_modules("nextron.adapters.sink"),
        "nextron.adapters.telegram": package_modules("nextron.adapters.telegram"),
        "nextron.adapters.gmail": [probe_module("nextron.adapters.gmail", "x = 1")],
    }
    classified = {
        **ADAPTER_PACKAGES,
        "ai": NotYetBuilt(),  # as a package the spine reserves would be
        "clock": NoExternalSystem(" "),
        "secrets": Guarded(("default_backend", "missing_constructor")),
    }
    live = [entry for entry in LIVE_CONSTRUCTORS if entry[0] != "nextron.adapters.ai"]

    problems = classification_problems(
        adapter_packages(tmp_path),
        classified,
        [*live, ("nextron.adapters.gmail_api", "default_service")],
        modules_of=modules.__getitem__,
        folder=tmp_path,
    )

    assert problems == [
        "gmail is not classified",
        "ai is not built yet but defines ['nextron.adapters.ai.route']",
        "ai is not built yet but holds ['__init__.py', 'gateway.py']",
        "clock gives no reason it reaches no external system",
        "secrets has no missing_constructor",
        "nextron.adapters.secrets.missing_constructor is not in LIVE_CONSTRUCTORS",
        "nextron.adapters.gmail_api.default_service is in LIVE_CONSTRUCTORS but no guarded "
        "package lists it",
    ]


def test_the_classification_check_looks_inside_a_not_yet_built_package(tmp_path: Path) -> None:
    (tmp_path / "ai" / "prompts" / "route").mkdir(parents=True)
    (tmp_path / "ai" / "__init__.py").write_text("")
    (tmp_path / "ai" / "prompts" / "route" / "v1.md").write_text("")
    (tmp_path / "ai" / "__pycache__").mkdir()
    (tmp_path / "ai" / "__pycache__" / "x.pyc").write_bytes(b"")
    package = probe_module("nextron.adapters.ai", "")
    gateway = probe_module("nextron.adapters.ai.gateway", "ROUTES = {}")

    problems = classification_problems(
        {"ai"},
        {"ai": NotYetBuilt()},
        [],
        modules_of={"nextron.adapters.ai": [package, gateway]}.__getitem__,
        folder=tmp_path,
    )

    assert problems == [
        "ai is not built yet but defines ['nextron.adapters.ai.gateway.ROUTES']",
        "ai is not built yet but holds ['__init__.py', 'prompts', 'prompts/route', "
        "'prompts/route/v1.md']",
    ]


SENDER = (
    "from nextron.ports.alert import AlertMessage\n"
    "class TelegramAlert:\n"
    "    def send(self, message: AlertMessage) -> None: ...\n"
)


@pytest.mark.parametrize("where", ["package", "submodule"])
def test_the_classification_check_catches_an_unguarded_sender(where: str) -> None:
    if where == "package":
        modules = [probe_module("nextron.adapters.telegram", SENDER)]
    else:
        modules = [
            probe_module("nextron.adapters.telegram", ""),
            probe_module("nextron.adapters.telegram.client", SENDER),
        ]

    problems = classification_problems(
        {"telegram"},
        {"telegram": NoExternalSystem("it says so")},
        [],
        modules_of={"nextron.adapters.telegram": modules}.__getitem__,
    )

    assert problems == ["telegram sends mail or alerts, so it is live: guard it"]


def probe_module(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)  # noqa: S102 - a fixed probe
    return module


# Nothing reaches the network --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True),
        ("127.8.9.10", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("localhost", True),
        ("LOCALHOST", True),
        (b"127.0.0.1", True),
        (None, True),
        (TEST_NET, False),
        ("10.0.0.1", False),
        ("0.0.0.0", False),
        ("::", False),
        ("", False),
        ("example.com", False),
        ("localhost.example.com", False),
        ("api.telegram.org", False),
        (42, False),
    ],
)
def test_only_loopback_counts_as_local(host: object, local: bool) -> None:
    assert is_local_host(host) is local


def test_a_lookup_of_a_real_host_is_refused() -> None:
    with pytest.raises(NetworkInTests):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(NetworkInTests):
        socket.gethostbyname("example.com")
    with pytest.raises(NetworkInTests):
        socket.gethostbyname_ex("example.com")
    with pytest.raises(NetworkInTests):
        socket.gethostbyaddr(TEST_NET)
    with pytest.raises(NetworkInTests):
        socket.getnameinfo((TEST_NET, 443), 0)
    with pytest.raises(NetworkInTests):
        urllib.request.urlopen("https://example.com/", timeout=1)  # noqa: S310 - refused


def test_a_connection_to_a_real_address_is_refused() -> None:
    with pytest.raises(NetworkInTests):
        socket.create_connection((TEST_NET, 443), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkInTests):
            sock.connect((TEST_NET, 443))
        with pytest.raises(NetworkInTests):
            sock.connect_ex((TEST_NET, 443))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(NetworkInTests):
            sock.sendto(b"x", (TEST_NET, 53))
        with pytest.raises(NetworkInTests):
            sock.sendto(b"x", 0, (TEST_NET, 53))


def test_loopback_still_works() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        with socket.create_connection(("localhost", port), timeout=5):
            pass
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            assert client.connect_ex(("127.0.0.1", port)) == 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"ping", receiver.getsockname())
        assert receiver.recv(16) == b"ping"
    assert socket.gethostbyname("127.0.0.1") == "127.0.0.1"
    numeric = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    assert socket.getnameinfo(("127.0.0.1", 80), numeric) == ("127.0.0.1", "80")


def test_sendmsg_is_refused_where_the_platform_has_it() -> None:
    if not hasattr(socket.socket, "sendmsg"):
        assert sys.platform == "win32"  # Windows sockets have no sendmsg
        return
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(NetworkInTests):
            sock.sendmsg([b"x"], [], 0, (TEST_NET, 53))


@pytest.mark.skipif(sys.platform != "win32", reason="asyncio's Proactor exists on Windows only")
def test_asyncios_proactor_cant_reach_a_real_address() -> None:
    """An IP literal skips ``getaddrinfo`` and the Proactor connects with ``ConnectEx``, not
    ``socket.connect``: the guard is on the Proactor itself."""
    from asyncio.windows_events import IocpProactor  # noqa: PLC0415

    async def tcp() -> None:
        await asyncio.open_connection(TEST_NET, 443)

    async def udp() -> None:
        await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=(TEST_NET, 53)
        )

    with pytest.raises(NetworkInTests):
        asyncio.run(tcp())
    with pytest.raises(NetworkInTests):
        asyncio.run(udp())
    proactor = IocpProactor()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with pytest.raises(NetworkInTests):
                proactor.sendto(sock, b"x", 0, (TEST_NET, 53))
    finally:
        proactor.close()


def test_loopback_still_works_through_asyncio() -> None:
    async def exchange() -> tuple[bytes, bytes]:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(await reader.readexactly(4))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"ping")
            await writer.drain()
            echoed = await reader.readexactly(4)
            writer.close()
            await writer.wait_closed()

        loop = asyncio.get_running_loop()
        received: asyncio.Future[bytes] = loop.create_future()

        class Receiver(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: object) -> None:
                received.set_result(data)

        receiver, _ = await loop.create_datagram_endpoint(Receiver, local_addr=("127.0.0.1", 0))
        sender, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        sender.sendto(b"pong", receiver.get_extra_info("sockname"))
        datagram = await asyncio.wait_for(received, 5)
        sender.close()
        receiver.close()
        return echoed, datagram

    assert asyncio.run(exchange()) == (b"ping", b"pong")


# Engines a test starts (Story 4.5) -----------------------------------------------------------

STARTS_THE_ENGINE: Final = re.compile(
    "|".join(
        (
            r"""["']-m["']\s*,\s*["']nextron["']""",  # [python, "-m", "nextron", ...]
            # ["uv", "run", ..., "nextron", ...]: uv runs the engine, or its console script
            r"""["']uv(?:\.exe)?["']\s*,\s*["']run["'](?:\s*,\s*[^,\]\)\n]+?)*?"""
            r"""\s*,\s*["']nextron["']""",
            # run.ps1 run by PowerShell: "& <run.ps1>" in a script, or [..., "-File", <run.ps1>]
            r"""&\s*\{[^{}\n]*\bRUN_PS1\b[^{}\n]*\}""",
            r"""&\s*["'][^"'\n]*run\.ps1["']""",
            r"""["']-File["']\s*,\s*[^,\]\)\n]*(?:\bRUN_PS1\b|run\.ps1)""",
        )
    )
)
CHILD_PROBE: Final = (
    "import socket\n"
    "for call in (lambda: socket.getaddrinfo('example.com', 443),\n"
    "             lambda: socket.create_connection(('192.0.2.1', 443), timeout=1)):\n"
    "    try:\n"
    "        call()\n"
    "    except Exception as error:\n"
    "        print(type(error).__name__)\n"
    "    else:\n"
    "        print('reached')\n"
    "server = socket.socket()\n"
    "server.bind(('127.0.0.1', 0))\n"
    "server.listen()\n"
    "socket.create_connection(server.getsockname(), timeout=5).close()\n"
    "print('loopback')\n"
)


def modules_that_start_the_engine(root: Path) -> list[Path]:
    """Every test module under ``root`` that starts the engine: ``python -m nextron``,
    ``uv run ... nextron`` or ``scripts/run.ps1``."""
    return sorted(
        path
        for path in root.rglob("*.py")
        if STARTS_THE_ENGINE.search(path.read_text(encoding="utf-8")) is not None
    )


def test_every_test_that_starts_the_engine_guards_its_network() -> None:
    found = modules_that_start_the_engine(ENGINE_ROOT / "tests")

    assert found  # the check looks at real modules
    unguarded = [
        path.relative_to(ENGINE_ROOT).as_posix()
        for path in found
        if "child_env(" not in path.read_text(encoding="utf-8")
    ]
    assert unguarded == []


ENGINE_START_PROBES: Final = {  # each starts the engine, so the check must find it
    "test_probe.py": (
        'import subprocess, sys\nsubprocess.run([sys.executable, "-m", "nextron", "serve"])\n'
    ),
    "test_uv_run.py": 'subprocess.run(["uv", "run", "--frozen", "nextron", "serve"])\n',
    "test_uv_exe.py": 'subprocess.run(\n    ["uv.exe", "run", "nextron", "serve"], check=False\n)\n',
    "test_run_ps1.py": 'script = f"& {ps_string(RUN_PS1)} -Env test -MaxRestarts 0"\n',
    "test_run_ps1_path.py": "script = \"& 'scripts/run.ps1' -Env test\"\n",
    "test_run_ps1_file.py": (
        'subprocess.run([POWERSHELL, "-File", str(RUN_PS1), "-Env", "test"])\n'
    ),
}
NOT_ENGINE_STARTS: Final = {  # none starts the engine
    "test_other.py": "print('-m nextron')\n",
    "test_installer.py": (
        'RUN_PS1 = ENGINE_ROOT / "scripts" / "run.ps1"\n'
        "ACTION = f'-WindowStyle Hidden -File \"{RUN_PS1}\" -Env prod'\n"
        'script = f"& {ps_string(INSTALL)} -Env prod"\n'
        'subprocess.run(["uv", "sync", "--frozen"])\n'
    ),
}


def test_the_engine_check_finds_a_module_that_starts_the_engine(tmp_path: Path) -> None:
    for name, source in {**ENGINE_START_PROBES, **NOT_ENGINE_STARTS}.items():
        (tmp_path / name).write_text(source, encoding="utf-8")

    assert modules_that_start_the_engine(tmp_path) == sorted(
        tmp_path / name for name in ENGINE_START_PROBES
    )


def test_a_child_started_with_child_env_reaches_only_loopback() -> None:
    variables = child_env({**os.environ, "PYTHONPATH": str(ENGINE_ROOT / "missing")})

    done = subprocess.run(  # noqa: S603 - this interpreter, a fixed snippet
        [sys.executable, "-c", CHILD_PROBE],
        env=variables,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert (done.returncode, done.stdout.split()) == (
        0,
        ["NetworkInTests", "NetworkInTests", "loopback"],
    ), done.stderr
    assert variables["PYTHONPATH"].split(os.pathsep) == [
        str(ENGINE_ROOT / "tests" / "child_guard"),
        str(ENGINE_ROOT),
        str(ENGINE_ROOT / "missing"),
    ]


def test_a_child_whose_guard_cant_be_installed_never_runs(tmp_path: Path) -> None:
    broken = tmp_path / "tests"
    broken.mkdir()
    (broken / "__init__.py").write_text("", encoding="utf-8")
    (broken / "live_guard.py").write_text("raise RuntimeError('no guard')\n", encoding="utf-8")
    variables = child_env(dict(os.environ))
    paths = variables["PYTHONPATH"].split(os.pathsep)
    variables["PYTHONPATH"] = os.pathsep.join([paths[0], str(tmp_path), *paths[2:]])

    done = subprocess.run(  # noqa: S603 - this interpreter, a fixed snippet
        [sys.executable, "-c", "print('ran')"],
        env=variables,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert done.returncode == 97
    assert "ran" not in done.stdout
    assert "the child network guard failed: RuntimeError" in done.stderr
