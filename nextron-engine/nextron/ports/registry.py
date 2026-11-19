"""The ``RegistryPort`` (FR-12, FR-57, AD-9, INT-5; Story 2.12): the UK companies register.

``nextron.adapters.companies_house.CompaniesHouseRegistry`` implements it over the Companies
House public data API; tests use ``tests.fakes.registry.ScriptedRegistry``. Every call is
metered, and the adapter keeps itself under the API's rate limit.

- ``RegistryCompany(number, name, company_type, company_status, office_postcode)``: one company
  as the register holds it. ``number`` fullmatches ``[A-Z0-9]{8}``; ``name`` (the registered
  name) is a stripped text of 1 to 200 characters; ``company_type`` and ``company_status`` are
  ``None`` (unknown) or match ``nextron.domain.legal_form``'s ``LEGAL_FORM_PATTERN`` and
  ``COMPANY_STATUS_PATTERN``; ``office_postcode`` (the registered office's) is ``None`` or a
  text of at most 16 characters. A wrong type raises ``TypeError``, a bad value
  ``ValueError``; no message holds a value, and the ``repr`` leaves out the name and postcode.
  ``accounts_category`` (Story 8.4) is ``None`` (unknown) or the latest accounts' type code
  (``ACCOUNTS_CATEGORY_PATTERN``, such as ``full`` or ``dormant``), and ``sic_codes`` a tuple
  of distinct 5-digit SIC codes (``SIC_CODE_PATTERN``); both default to unknown and empty.
- ``RegistryOfficer(name, role, occupation, resigned)`` (Story 8.4): one officer of a company as
  the register lists it. ``name`` is a stripped text of 1 to 200 characters (the register's
  "SURNAME, Forenames" form), ``role`` an officer role code (``LEGAL_FORM_PATTERN``, such as
  ``director``, ``llp-designated-member`` or ``corporate-director``), ``occupation`` ``None`` or
  a stripped text of 1 to 200 characters, and ``resigned`` whether the officer has resigned.
  The ``repr`` leaves out the name and occupation.
- ``search_companies(name) -> tuple[RegistryCompany, ...]``: the register's search by name, in
  its order (empty when nothing matches).
- ``company(number) -> RegistryCompany | None``: the company with that number, or ``None`` when
  the register has none.
- ``search(keyword, sic_codes, page) -> tuple[RegistryCompany, ...]`` (Story 8.4): one page of
  ``PAGE_SIZE`` (20) active companies whose name includes ``keyword`` (``None``: any name) and
  that carry one of ``sic_codes`` (``SIC_CODE_PATTERN`` each), ``page`` counted from 0; fewer
  than 20 means the last page.
- ``officers(company_number) -> tuple[RegistryOfficer, ...]`` (Story 8.4): the company's
  officers in the register's order (empty when the register has no such company).

A failed call raises ``AdapterError`` (``nextron.ports.errors``): ``registry_rate_limited`` and
``registry_unavailable`` are retryable; ``registry_unauthorized``, ``registry_refused`` and
``registry_bad_response`` aren't, nor are the credential codes (``credential_not_set``,
``keyring_failed``).
"""

from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from nextron.domain.legal_form import (
    ACCOUNTS_CATEGORY_PATTERN,
    COMPANY_NUMBER_PATTERN,
    COMPANY_STATUS_PATTERN,
    LEGAL_FORM_PATTERN,
    SIC_CODE_PATTERN,
)

__all__ = [
    "ACCOUNTS_CATEGORY_PATTERN",
    "MAX_NAME",
    "MAX_POSTCODE",
    "PAGE_SIZE",
    "REGISTRY_BAD_RESPONSE",
    "REGISTRY_CODES",
    "REGISTRY_RATE_LIMITED",
    "REGISTRY_REFUSED",
    "REGISTRY_UNAUTHORIZED",
    "REGISTRY_UNAVAILABLE",
    "SIC_CODE_PATTERN",
    "RegistryCompany",
    "RegistryOfficer",
    "RegistryPort",
]

MAX_NAME: Final = 200
PAGE_SIZE: Final = 20  # a search page's companies (Story 8.4)
MAX_POSTCODE: Final = 16
REGISTRY_RATE_LIMITED: Final = "registry_rate_limited"
REGISTRY_UNAVAILABLE: Final = "registry_unavailable"
REGISTRY_UNAUTHORIZED: Final = "registry_unauthorized"
REGISTRY_REFUSED: Final = "registry_refused"
REGISTRY_BAD_RESPONSE: Final = "registry_bad_response"
REGISTRY_CODES: Final = (
    REGISTRY_RATE_LIMITED,
    REGISTRY_UNAVAILABLE,
    REGISTRY_UNAUTHORIZED,
    REGISTRY_REFUSED,
    REGISTRY_BAD_RESPONSE,
)


@dataclass(frozen=True, slots=True)
class RegistryCompany:
    """One company as the register holds it."""

    number: str
    name: str = field(repr=False)
    company_type: str | None
    company_status: str | None
    office_postcode: str | None = field(repr=False)
    accounts_category: str | None = None
    sic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.number, str) or not isinstance(self.name, str):
            raise TypeError("RegistryCompany takes its number and name as str")
        if COMPANY_NUMBER_PATTERN.fullmatch(self.number) is None:
            raise ValueError("RegistryCompany.number isn't a Companies House number")
        if not 1 <= len(self.name) <= MAX_NAME or self.name != self.name.strip():
            raise ValueError(f"RegistryCompany.name must be a stripped text of 1 to {MAX_NAME}")
        for value, pattern, what in (
            (self.company_type, LEGAL_FORM_PATTERN, "company_type"),
            (self.company_status, COMPANY_STATUS_PATTERN, "company_status"),
        ):
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(f"RegistryCompany.{what} must be a str or None")
            if pattern.fullmatch(value) is None:
                raise ValueError(f"RegistryCompany.{what} isn't a Companies House code")
        if self.office_postcode is not None:
            if not isinstance(self.office_postcode, str):
                raise TypeError("RegistryCompany.office_postcode must be a str or None")
            if len(self.office_postcode) > MAX_POSTCODE:
                raise ValueError(
                    f"RegistryCompany.office_postcode must be at most {MAX_POSTCODE} characters"
                )
        if self.accounts_category is not None:
            if not isinstance(self.accounts_category, str):
                raise TypeError("RegistryCompany.accounts_category must be a str or None")
            if ACCOUNTS_CATEGORY_PATTERN.fullmatch(self.accounts_category) is None:
                raise ValueError("RegistryCompany.accounts_category isn't an accounts type code")
        if not isinstance(self.sic_codes, tuple) or not all(
            isinstance(code, str) for code in self.sic_codes
        ):
            raise TypeError("RegistryCompany.sic_codes must be a tuple of str")
        if any(SIC_CODE_PATTERN.fullmatch(code) is None for code in self.sic_codes) or len(
            set(self.sic_codes)
        ) != len(self.sic_codes):
            raise ValueError("RegistryCompany.sic_codes must be distinct 5-digit SIC codes")


@dataclass(frozen=True, slots=True)
class RegistryOfficer:
    """One officer of a company as the register lists it (Story 8.4)."""

    name: str = field(repr=False)
    role: str
    occupation: str | None = field(repr=False)
    resigned: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.role, str):
            raise TypeError("RegistryOfficer takes its name and role as str")
        if not 1 <= len(self.name) <= MAX_NAME or self.name != self.name.strip():
            raise ValueError(f"RegistryOfficer.name must be a stripped text of 1 to {MAX_NAME}")
        if LEGAL_FORM_PATTERN.fullmatch(self.role) is None:
            raise ValueError("RegistryOfficer.role isn't a Companies House officer role")
        if self.occupation is not None:
            if not isinstance(self.occupation, str):
                raise TypeError("RegistryOfficer.occupation must be a str or None")
            if not 1 <= len(self.occupation) <= MAX_NAME or (
                self.occupation != self.occupation.strip()
            ):
                raise ValueError(
                    f"RegistryOfficer.occupation must be a stripped text of 1 to {MAX_NAME}"
                )
        if not isinstance(self.resigned, bool):
            raise TypeError("RegistryOfficer.resigned must be a bool")


@runtime_checkable
class RegistryPort(Protocol):
    """Searches and reads the UK companies register."""

    def search_companies(self, name: str) -> tuple[RegistryCompany, ...]:
        """The companies the register finds for ``name``, in its order."""
        ...

    def company(self, number: str) -> RegistryCompany | None:
        """The company ``number``, or ``None`` when the register has none."""
        ...

    def search(
        self, keyword: str | None, sic_codes: tuple[str, ...], page: int
    ) -> tuple[RegistryCompany, ...]:
        """One page (from 0) of the active companies matching ``keyword`` and ``sic_codes``."""
        ...

    def officers(self, company_number: str) -> tuple[RegistryOfficer, ...]:
        """The officers of the company ``company_number``, in the register's order."""
        ...
