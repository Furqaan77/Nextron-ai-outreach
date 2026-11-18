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
- ``search_companies(name) -> tuple[RegistryCompany, ...]``: the register's search by name, in
  its order (empty when nothing matches).
- ``company(number) -> RegistryCompany | None``: the company with that number, or ``None`` when
  the register has none.

A failed call raises ``AdapterError`` (``nextron.ports.errors``): ``registry_rate_limited`` and
``registry_unavailable`` are retryable; ``registry_unauthorized``, ``registry_refused`` and
``registry_bad_response`` aren't, nor are the credential codes (``credential_not_set``,
``keyring_failed``).
"""

from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from nextron.domain.legal_form import (
    COMPANY_NUMBER_PATTERN,
    COMPANY_STATUS_PATTERN,
    LEGAL_FORM_PATTERN,
)

MAX_NAME: Final = 200
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


@runtime_checkable
class RegistryPort(Protocol):
    """Searches and reads the UK companies register."""

    def search_companies(self, name: str) -> tuple[RegistryCompany, ...]:
        """The companies the register finds for ``name``, in its order."""
        ...

    def company(self, number: str) -> RegistryCompany | None:
        """The company ``number``, or ``None`` when the register has none."""
        ...
