"""The live-adapter and network guards (AD-14): no test reaches a real external system.

``tests/conftest.py`` applies both to every test (``live_adapters_refused`` and
``network_refused``).

**Live constructors.** ``guard_live_constructors`` patches every constructor that would reach
a real external system to raise ``LiveAdapterInTests``:

- each ``(module, name)`` in ``LIVE_CONSTRUCTORS``, such as
  ``nextron.adapters.secrets.default_backend`` (Windows Credential Manager), so
  ``KeyringSecrets("test")`` and ``main.build_app`` without injected secrets raise, and
  ``nextron.adapters.google_oauth.default_http_client`` (Google's OAuth endpoints), so a
  ``GoogleOAuthClient(meter=...)`` raises on its first call, and
  ``nextron.adapters.telegram.default_http_client`` (the Telegram Bot API), so a
  ``TelegramAlert`` raises on its first send, and ``nextron.adapters.ai.default_http_client``
  (the Claude API), so a ``ClaudeGateway`` without an injected ``http`` raises on its first
  ``run``;
- the ``build`` of each ``nextron.main.ROLE_ADAPTERS`` entry (every ``mail`` and ``alert``
  adapter), so ``build_role_adapter`` raises for any registered name.

A test that needs one of them (to check its wiring, say) restores it with
``allow_live_constructor(monkeypatch, module, name)``, after neutralising what it would reach:
``allow_live_constructor(monkeypatch, "nextron.adapters.secrets", "default_backend")``, or
``allow_live_constructor(monkeypatch, "nextron.main", "<registered name>")`` for a role
adapter.

**Credential Manager.** It also patches ``keyring.backends.Windows.WinVaultKeyring``'s
``get_password``, ``set_password``, ``delete_password`` and ``get_credential``
(``CREDENTIAL_MANAGER_CALLS``) to raise ``LiveAdapterInTests``, so a ``WinVaultKeyring`` built
directly, or ``keyring.get_password(...)`` through keyring's own backend choice, never reaches
the real Credential Manager. ``allow_live_constructor`` never restores these: a test that
restores ``default_backend`` gets a backend whose calls still raise.

**The default browser.** It patches ``webbrowser.open``, ``open_new`` and ``open_new_tab``
(``BROWSER_CALLS``) to raise ``LiveAdapterInTests``, so neither ``nextron open`` nor a Google
consent flow can open a real browser; ``allow_live_constructor`` never restores them. Inject a
recording browser instead.

**Adapter packages.** ``ADAPTER_PACKAGES`` classifies every package under
``nextron/adapters`` exactly once: ``Guarded`` (its live constructors, each listed in
``LIVE_CONSTRUCTORS``), ``NoExternalSystem`` (with the reason; none of its modules may define a
mail or alert sender) or ``NotYetBuilt`` (it holds only its ``__init__.py`` and defines
nothing). ``tests/contracts/test_test_isolation.py`` fails when a package is missing or
misclassified; a new live adapter adds its constructors here.

**Network.** ``guard_network`` raises ``NetworkInTests`` for any host other than a loopback
address or ``localhost`` (``is_local_host``): a safety net under the constructor guard. It
patches:

- the lookups ``socket.getaddrinfo``, ``gethostbyname``, ``gethostbyname_ex`` and
  ``gethostbyaddr`` (by the host name or address asked for) and ``getnameinfo`` (by its
  socket address's host);
- ``socket.socket.connect``, ``connect_ex``, ``sendto`` and, where the platform has it,
  ``sendmsg`` (by the address);
- on Windows, asyncio's Proactor, which skips both of those for an IP literal:
  ``asyncio.windows_events.IocpProactor.connect`` (``ConnectEx``) and ``sendto`` (by the
  address, when one is given).
"""

import dataclasses
import importlib
import ipaddress
import socket
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import pytest
from keyring.backends.Windows import WinVaultKeyring

from nextron import main

ROLE_ADAPTER_MODULE: Final = "nextron.main"

LIVE_CONSTRUCTORS: Final[tuple[tuple[str, str], ...]] = (
    ("nextron.adapters.secrets", "default_backend"),
    ("nextron.adapters.google_oauth", "default_http_client"),
    ("nextron.adapters.telegram", "default_http_client"),
    ("nextron.adapters.ai", "default_http_client"),
)
# The WinVaultKeyring methods that call Windows Credential Manager; never restored.
CREDENTIAL_MANAGER_CALLS: Final = (
    "get_password",
    "set_password",
    "delete_password",
    "get_credential",
)
# The webbrowser functions that open the real default browser; never restored.
BROWSER_CALLS: Final = ("open", "open_new", "open_new_tab")


class LiveAdapterInTests(RuntimeError):
    """A test tried to construct an adapter that reaches a real external system."""


class NetworkInTests(RuntimeError):
    """A test tried to reach a host other than this machine."""


@dataclass(frozen=True, slots=True)
class Guarded:
    """A live adapter: ``constructors`` are the names in its module that reach the system."""

    constructors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoExternalSystem:
    """An adapter that reaches nothing outside this process and its own files."""

    reason: str


@dataclass(frozen=True, slots=True)
class NotYetBuilt:
    """A package the spine reserves; it must define nothing until its story builds it."""


ADAPTER_PACKAGES: Final[dict[str, Guarded | NoExternalSystem | NotYetBuilt]] = {
    "secrets": Guarded(("default_backend",)),
    "google_oauth": Guarded(("default_http_client",)),
    "telegram": Guarded(("default_http_client",)),
    "clock": NoExternalSystem("reads the system clock; reaches no external system"),
    "crypto": NoExternalSystem(
        "pure computation over the streams it is given; opens no file and reaches nothing"
    ),
    "backup_target": NoExternalSystem(
        "encrypts and writes files in the folder it is given (a local or network folder the "
        "Operator chose; tests pass a temporary one); calls no service"
    ),
    "sink": NoExternalSystem(
        "writes JSON lines under the data dir and only calls the adapter it wraps"
    ),
    "ai": Guarded(("default_http_client",)),
    "email_syntax": NoExternalSystem(
        "checks an address's syntax with email-validator, deliverability off; no DNS or network"
    ),
}


_ORIGINAL: Final = "__live_guard_original__"  # what a refusing stand-in replaced


def _refusing(what: str, original: object) -> Callable[..., Any]:
    """A stand-in that raises ``LiveAdapterInTests`` and remembers ``original``."""
    original = getattr(original, _ORIGINAL, original)  # never wrap a stand-in in another

    def refuse(*args: object, **kwargs: object) -> Any:
        raise LiveAdapterInTests(
            f"tests must not reach the live {what}; inject a fake from tests/fakes "
            "(or call allow_live_constructor after neutralising what it reaches)"
        )

    setattr(refuse, _ORIGINAL, original)
    return refuse


def is_refused(constructor: object) -> bool:
    """Whether ``constructor`` is a guard's stand-in."""
    return hasattr(constructor, _ORIGINAL)


def guard_live_constructors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every live constructor raise ``LiveAdapterInTests``; see the module docstring."""
    for module, name in LIVE_CONSTRUCTORS:
        target = importlib.import_module(module)
        monkeypatch.setattr(target, name, _refusing(f"{module}.{name}", getattr(target, name)))
    monkeypatch.setattr(
        main,
        "ROLE_ADAPTERS",
        tuple(
            dataclasses.replace(entry, build=_refusing(f"role adapter {entry.name}", entry.build))
            for entry in main.ROLE_ADAPTERS
        ),
    )
    for method in CREDENTIAL_MANAGER_CALLS:
        what = f"Credential Manager (WinVaultKeyring.{method})"
        monkeypatch.setattr(
            WinVaultKeyring, method, _refusing(what, vars(WinVaultKeyring)[method])
        )
    for name in BROWSER_CALLS:
        what = f"default browser (webbrowser.{name})"
        monkeypatch.setattr(webbrowser, name, _refusing(what, getattr(webbrowser, name)))


def allow_live_constructor(monkeypatch: pytest.MonkeyPatch, module: str, name: str) -> None:
    """Restore one live constructor for this test: a ``LIVE_CONSTRUCTORS`` entry, or with
    ``module="nextron.main"`` the ``build`` of the role adapter registered as ``name``. Raises
    ``LookupError`` for anything else."""
    if module == ROLE_ADAPTER_MODULE:
        entries = main.ROLE_ADAPTERS
        if not any(entry.name == name for entry in entries):
            raise LookupError(f"no role adapter is registered as {name!r}")
        monkeypatch.setattr(
            main,
            "ROLE_ADAPTERS",
            tuple(
                dataclasses.replace(entry, build=getattr(entry.build, _ORIGINAL, entry.build))
                if entry.name == name
                else entry
                for entry in entries
            ),
        )
        return
    if (module, name) not in LIVE_CONSTRUCTORS:
        raise LookupError(f"{module}.{name} is not a guarded live constructor")
    target = importlib.import_module(module)
    current = getattr(target, name)
    monkeypatch.setattr(target, name, getattr(current, _ORIGINAL, current))


# Network ------------------------------------------------------------------------------------

LOCAL_NAMES: Final = frozenset({"localhost"})
_INET_FAMILIES: Final = frozenset({socket.AF_INET, socket.AF_INET6})


def is_local_host(host: object) -> bool:
    """Whether ``host`` names this machine: a loopback address (``127.0.0.0/8``, ``::1``, or
    IPv4-mapped loopback) or ``localhost``. ``None`` (no host) counts as local."""
    if host is None:
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    if host.lower() in LOCAL_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


def _check_address(sock: socket.socket, address: object, call: str) -> None:
    unix = getattr(socket, "AF_UNIX", None)
    if unix is not None and sock.family == unix:
        return  # a local socket file
    host = address[0] if isinstance(address, tuple) and address else address
    if sock.family not in _INET_FAMILIES or not is_local_host(host):
        raise NetworkInTests(f"tests must not {call} {host!r}: only loopback is allowed")


def _check_host(host: object, call: str) -> None:
    if not is_local_host(host):
        raise NetworkInTests(f"tests must not {call} {host!r}: only loopback is allowed")


def _guarded_lookup(real: Callable[..., Any], call: str) -> Callable[..., Any]:
    """``real`` (a lookup taking the host first), refusing any host but loopback."""

    def lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
        _check_host(host, call)
        return real(host, *args, **kwargs)

    return lookup


def guard_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse lookups of and connections to anything but loopback; see the module docstring."""
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(socket, name, _guarded_lookup(getattr(socket, name), "look up"))
    real_getnameinfo = socket.getnameinfo

    def getnameinfo(sockaddr: Any, flags: Any) -> Any:
        host = sockaddr[0] if isinstance(sockaddr, tuple) and sockaddr else sockaddr
        _check_host(host, "look up")
        return real_getnameinfo(sockaddr, flags)

    monkeypatch.setattr(socket, "getnameinfo", getnameinfo)

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto

    def connect(self: socket.socket, address: Any) -> None:
        _check_address(self, address, "connect to")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _check_address(self, address, "connect to")
        return real_connect_ex(self, address)

    def sendto(self: socket.socket, data: Any, *args: Any) -> int:
        if args:  # sendto(data, address) or sendto(data, flags, address)
            _check_address(self, args[-1], "send to")
        return real_sendto(self, data, *args)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)

    real_sendmsg = getattr(socket.socket, "sendmsg", None)
    if real_sendmsg is not None:  # not on Windows

        def sendmsg(self: socket.socket, buffers: Any, *args: Any, **kwargs: Any) -> int:
            # sendmsg(buffers[, ancdata[, flags[, address]]])
            address = args[2] if len(args) > 2 else kwargs.get("address")
            if address is not None:
                _check_address(self, address, "send to")
            return real_sendmsg(self, buffers, *args, **kwargs)

        monkeypatch.setattr(socket.socket, "sendmsg", sendmsg)

    if sys.platform == "win32":
        from asyncio.windows_events import IocpProactor  # noqa: PLC0415 - Windows only

        real_proactor_connect = IocpProactor.connect
        real_proactor_sendto = IocpProactor.sendto

        def proactor_connect(self: Any, conn: socket.socket, address: Any) -> Any:
            _check_address(conn, address, "connect to")
            return real_proactor_connect(self, conn, address)

        def proactor_sendto(
            self: Any, conn: socket.socket, buf: Any, flags: int = 0, addr: Any = None
        ) -> Any:
            if addr is not None:
                _check_address(conn, addr, "send to")
            return real_proactor_sendto(self, conn, buf, flags, addr)

        monkeypatch.setattr(IocpProactor, "connect", proactor_connect)
        monkeypatch.setattr(IocpProactor, "sendto", proactor_sendto)
