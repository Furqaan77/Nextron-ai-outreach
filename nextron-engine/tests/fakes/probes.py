"""Fake probes for ``nextron.startup_checks``: every check passes unless a test overrides it.

The defaults: SQLite 3.53.1, Python 3.14.8, every timezone loads, no OneDrive variable, a free
port (a ``PortProbe``), no persistent ``NEXTRON_ENV`` and no file (``file_age`` is ``None``).
"""

from collections.abc import Callable, Mapping
from pathlib import PurePath
from typing import Any

from nextron.startup_checks import Probes


class PortProbe:
    """A fake port probe that records every call."""

    def __init__(self, *, free: bool = True) -> None:
        self.free = free
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> bool:
        self.calls.append((host, port))
        return self.free


def _load_zone(name: str) -> object:
    return object()


def _no_persistent_variable(name: str) -> Mapping[str, str]:
    return {}  # set in neither the user nor the machine environment


def _no_file(path: PurePath) -> float | None:
    return None


def fake_probes(
    *,
    sqlite_version: str | Callable[[], str] = "3.53.1",
    python_version: tuple[int, ...] | Callable[[], tuple[int, ...]] = (3, 14, 8),
    environ: Mapping[str, str] | None = None,
    port: PortProbe | None = None,
    **overrides: Any,
) -> Probes:
    """Probes for a healthy machine; pass keyword overrides to fail one check. A version may be
    given as a value or as the probe itself (one that raises, say)."""
    values: dict[str, Any] = {
        "sqlite_version": sqlite_version if callable(sqlite_version) else lambda: sqlite_version,
        "load_zone": _load_zone,
        "environ": environ if environ is not None else {},
        "port_is_free": port if port is not None else PortProbe(),
        "persistent_variable": _no_persistent_variable,
        "python_version": python_version if callable(python_version) else lambda: python_version,
        "file_age": _no_file,
    }
    return Probes(**(values | overrides))
