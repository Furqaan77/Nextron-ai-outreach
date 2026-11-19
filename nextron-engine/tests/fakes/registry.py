"""``ScriptedRegistry``: the scripted fake of ``nextron.ports.registry.RegistryPort``.

``ScriptedRegistry(*, companies=None, searches=None, searches_by=None, officers=None)``
answers each call from a script (Story 8.4 added ``searches_by``, keyed ``(keyword,
sic_codes, page)`` for ``search``, and ``officers``, keyed by company number):
``companies`` maps a company number, and ``searches`` a search name, to the answer (a
``RegistryCompany`` or ``None`` for ``company``, a tuple of ``RegistryCompany`` for
``search_companies``), an ``AdapterError`` (raised) or a no-argument callable returning or
raising one of those (so a test can move its clock during a call). The script is kept, so a key
answers the same every time; a key with no answer raises ``AssertionError``, so a test can't make
a call it didn't plan.

``calls`` records every call, in order, as ``RegistryCall(operation, key)`` (``operation``
``search_companies``, ``get_company``, ``search_registry`` or ``list_officers``, as the adapter
meters them; a ``search`` key is the ``(keyword, sic_codes, page)`` tuple). Unlike the real adapter it
reads no key, meters nothing and doesn't rate-limit itself.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nextron.ports.errors import AdapterError
from nextron.ports.registry import RegistryCompany, RegistryOfficer

type CompanyAnswer = RegistryCompany | None | AdapterError | Callable[[], RegistryCompany | None]
type SearchAnswer = (
    tuple[RegistryCompany, ...] | AdapterError | Callable[[], tuple[RegistryCompany, ...]]
)

type OfficersAnswer = (
    tuple[RegistryOfficer, ...] | AdapterError | Callable[[], tuple[RegistryOfficer, ...]]
)
type SearchKey = tuple[str | None, tuple[str, ...], int]

SEARCH_COMPANIES = "search_companies"
GET_COMPANY = "get_company"
SEARCH_REGISTRY = "search_registry"
LIST_OFFICERS = "list_officers"


@dataclass(frozen=True, slots=True)
class RegistryCall:
    """One call the fake answered (or refused to)."""

    operation: str
    key: object


class ScriptedRegistry:
    def __init__(
        self,
        *,
        companies: Mapping[str, CompanyAnswer] | None = None,
        searches: Mapping[str, SearchAnswer] | None = None,
        searches_by: Mapping[SearchKey, SearchAnswer] | None = None,
        officers: Mapping[str, OfficersAnswer] | None = None,
    ) -> None:
        self.companies: dict[str, CompanyAnswer] = dict(companies or {})
        self.searches: dict[str, SearchAnswer] = dict(searches or {})
        self.searches_by: dict[SearchKey, SearchAnswer] = dict(searches_by or {})
        self.officer_lists: dict[str, OfficersAnswer] = dict(officers or {})
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

    def search(
        self, keyword: str | None, sic_codes: tuple[str, ...], page: int
    ) -> tuple[RegistryCompany, ...]:
        answer = self._answer(SEARCH_REGISTRY, self.searches_by, (keyword, tuple(sic_codes), page))
        if not (
            isinstance(answer, tuple) and all(isinstance(hit, RegistryCompany) for hit in answer)
        ):
            raise AssertionError("ScriptedRegistry: a search answer must be a tuple of companies")
        return answer

    def officers(self, company_number: str) -> tuple[RegistryOfficer, ...]:
        answer = self._answer(LIST_OFFICERS, self.officer_lists, company_number)
        if not (
            isinstance(answer, tuple)
            and all(isinstance(officer, RegistryOfficer) for officer in answer)
        ):
            raise AssertionError("ScriptedRegistry: an officers answer must be a tuple of officers")
        return answer

    def _answer(self, operation: str, script: Mapping[Any, object], key: object) -> object:
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
