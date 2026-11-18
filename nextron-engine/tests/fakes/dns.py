"""``FakeDns``: the scripted fake of ``nextron.ports.dns.DnsPort``.

``FakeDns(txt=None, mx=None, errors=None)`` answers from its scripts, each keyed by name in lower
case with the final dot stripped (``key(name)``):

- ``txt`` maps a name to its TXT strings, answered as a sorted tuple;
- ``mx`` maps a name to its ``MxRecord`` values, answered sorted by ``(preference, exchange)``;
- ``errors`` maps a name to the ``AdapterError`` that either query of it raises.

A name not scripted answers ``()``, as NXDOMAIN does. The scripts are plain dicts
(``txt_answers``, ``mx_answers`` and ``errors``), so a test may change them between queries.

Before reading a script a query applies the port's name rule (``is_dns_name``): a non-``str``
raises ``TypeError`` and a name that isn't a DNS name raises ``AdapterError(False,
"dns_invalid_name")``, neither recorded. ``queries`` records every other query as a ``(type,
name)`` pair, ``type`` ``txt`` or ``mx`` and ``name`` as asked. Unlike the real adapter it meters
nothing.
"""

from collections.abc import Iterable, Mapping

from nextron.ports.dns import DNS_INVALID_NAME, MxRecord, is_dns_name
from nextron.ports.errors import AdapterError


def key(name: str) -> str:
    """A script key: ``name`` in lower case with the final dot stripped."""
    return name.lower().removesuffix(".")


class FakeDns:
    """Answers TXT and MX queries from scripts; see the module docstring."""

    def __init__(
        self,
        txt: Mapping[str, Iterable[str]] | None = None,
        mx: Mapping[str, Iterable[MxRecord]] | None = None,
        errors: Mapping[str, AdapterError] | None = None,
    ) -> None:
        self.txt_answers: dict[str, tuple[str, ...]] = {
            key(name): tuple(values) for name, values in (txt or {}).items()
        }
        self.mx_answers: dict[str, tuple[MxRecord, ...]] = {
            key(name): tuple(values) for name, values in (mx or {}).items()
        }
        self.errors: dict[str, AdapterError] = {
            key(name): error for name, error in (errors or {}).items()
        }
        self.queries: list[tuple[str, str]] = []

    def _asked(self, kind: str, name: str) -> str:
        if not is_dns_name(name):
            raise AdapterError(False, DNS_INVALID_NAME, "the name is not a DNS name")
        self.queries.append((kind, name))
        found = key(name)
        error = self.errors.get(found)
        if error is not None:
            raise error
        return found

    def txt(self, name: str) -> tuple[str, ...]:
        return tuple(sorted(self.txt_answers.get(self._asked("txt", name), ())))

    def mx(self, name: str) -> tuple[MxRecord, ...]:
        return tuple(sorted(self.mx_answers.get(self._asked("mx", name), ())))
