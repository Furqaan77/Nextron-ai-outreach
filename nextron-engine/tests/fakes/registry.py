"""``ScriptedRegistry``: the scripted fake of ``nextron.ports.registry.RegistryPort``.

``ScriptedRegistry(*, companies=None, searches=None)`` answers each call from a script:
``companies`` maps a company number, and ``searches`` a search name, to the answer (a
``RegistryCompany`` or ``None`` for ``company``, a tuple of ``RegistryCompany`` for
``search_companies``), an ``AdapterError`` (raised) or a no-argument callable returning or
raising one of those (so a test can move its clock during a call). The script is kept, so a key
answers the same every time; a key with no answer raises ``AssertionError``, so a test can't make
a call it didn't plan.

``calls`` records every call, in order, as ``RegistryCall(operation, key)`` (``operation``
``search_companies`` or ``get_company``, as the adapter meters them). Unlike the real adapter it
reads no key, meters nothing and doesn't rate-limit itself.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from nextron.ports.errors import AdapterError
from nextron.ports.registry import RegistryCompany

type CompanyAnswer = RegistryCompany | None | AdapterError | Callable[[], RegistryCompany | None]
type SearchAnswer = (
    tuple[RegistryCompany, ...] | AdapterError | Callable[[], tuple[RegistryCompany, ...]]
)

SEARCH_COMPANIES = "search_companies"
GET_COMPANY = "get_company"


@dataclass(frozen=True, slots=True)
class RegistryCall:
    """One call the fake answered (or refused to)."""

    operation: str
    key: str


class ScriptedRegistry:
    def __init__(
        self,
        *,
        companies: Mapping[str, CompanyAnswer] | None = None,
        searches: Mapping[str, SearchAnswer] | None = None,
    ) -> None:
        self.companies: dict[str, CompanyAnswer] = dict(companies or {})
        self.searches: dict[str, SearchAnswer] = dict(searches or {})
        self.calls: list[RegistryCall] = []

    def search_companies(self, name: str) -> tuple[RegistryCompany, ...]:
        answer = self._answer(SEARCH_COMPANIES, self.searches, name)
        if not (
            isinstance(answer, tuple) and all(isinstance(hit, RegistryCompany) for hit in answer)
        ):
            raise AssertionError("ScriptedRegistry: a search answer must be a tuple of companies")
        return answer

    def company(self, number: str) -> RegistryCompany | None:
        answer = self._answer(GET_COMPANY, self.companies, number)
        if answer is not None and not isinstance(answer, RegistryCompany):
            raise AssertionError("ScriptedRegistry: a company answer must be a company or None")
        return answer

    def _answer(self, operation: str, script: Mapping[str, object], key: str) -> object:
        self.calls.append(RegistryCall(operation, key))
        if key not in script:
            raise AssertionError(f"ScriptedRegistry: no {operation} answer is scripted")
        answer = script[key]
        if callable(answer):
            answer = answer()
        if isinstance(answer, AdapterError):
            raise answer
        return answer

    def operations(self) -> list[str]:
        """The operations called, in order."""
        return [call.operation for call in self.calls]
