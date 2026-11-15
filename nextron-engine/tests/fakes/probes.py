"""Fake probes for ``nextron.startup_checks``: every check passes unless a test overrides it."""

from collections.abc import Mapping
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


def fake_probes(
    *,
    sqlite_version: str = "3.53.1",
    environ: Mapping[str, str] | None = None,
    port: PortProbe | None = None,
    **overrides: Any,
) -> Probes:
    """Probes for a healthy machine; pass keyword overrides to fail one check."""
    values: dict[str, Any] = {
        "sqlite_version": lambda: sqlite_version,
        "load_zone": _load_zone,
        "environ": environ if environ is not None else {},
        "port_is_free": port if port is not None else PortProbe(),
    }
    return Probes(**(values | overrides))
