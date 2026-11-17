"""The Credentials page (AD-12, FR-56, NFR-18, UX-DR27): one write-only row per credential.

``create_app`` serves it; every route answers through ``page()``, which sends ``Cache-Control:
no-store``. The two POSTs here are ``@personal_data(False)``; ``GET /credentials`` is
``@personal_data(True)``, because its Google section (``nextron.web.google_page``) shows each
grant's account address. No route ever shows, echoes or logs a value, even a refused one.

- ``GET /credentials`` (``credentials_page``) renders ``credentials.html``, which extends
  ``base.html``: an introduction that names the environment the values are stored for and asks
  Mac to decline any browser offer to save or fill a password there, a link back to Settings,
  then ``credential_row.html`` once per credential, in registry order
  (``nextron.domain.credentials``), then the Google section.
- ``POST /credentials/save`` (``credential_save``) takes exactly the fields ``credential`` and
  ``value``, each once (``csrf_token`` aside), with a declared ``credential``
  (``submitted_save``), and runs ``Credentials.save``. It answers with the row alone, which HTMX
  swaps in for the old one (``hx-target="#credential-<key>" hx-swap="outerHTML"``): saved, the
  row with ``Saved.`` (``role="status"``); refused (``InvalidCredentialValue``), the row with
  the reason (``role="alert"``); ``CredentialStoreFailed``, the row as ``status(key)`` reads it
  now, with ``STORE_FAILED`` (``role="alert"``). A refusal answers 200, because HTMX swaps only
  2xx responses. The value field is always empty.
- ``POST /credentials/remove`` (``credential_remove``) takes ``credential`` and an optional
  ``confirm`` that must be exactly ``yes`` (``submitted_remove``). Without ``confirm`` nothing
  is deleted: the row asks (``role="alert"``) whether to remove the credential from Windows
  Credential Manager, with a form that posts ``confirm=yes`` and a plain link back to
  ``/credentials`` to cancel. With it, ``Credentials.remove`` runs and the row says
  ``Removed.``; ``CredentialStoreFailed`` is answered as on save.

Any other form raises ``MalformedForm`` (400), an undeclared ``credential`` included
(``wrong_fields``).

Each row shows the label, ``required`` or ``optional``, and its state (``state_text``): ``Set,
last updated YYYY-MM-DD HH:MM UTC`` (UTC, because the page can't assume the sender's timezone is
set), ``Set, last update time unknown``, ``Not set``, or ``Unknown: Windows Credential Manager
could not be read``. Its save form has the hidden ``credential``, an ``<input
type="password" name="value" autocomplete="new-password">`` with no ``value`` and no
``maxlength`` (a browser would cut a pasted value short without a word; the server refuses it
instead), a hint (at most 1,280 characters, a character outside the Basic Multilingual Plane
counting 2) and a Save button. ``new-password`` because Chrome and Edge ignore ``off`` on a
password field and would fill in a saved password. A Remove form appears only while the
credential is set.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from typing import Final

from nextron.app.setup.api import CredentialState, CredentialStatus
from nextron.domain.credentials import declared
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "credentials.html"
ROW_TEMPLATE: Final = "credential_row.html"
CREDENTIAL_FIELD: Final = "credential"
VALUE_FIELD: Final = "value"
CONFIRM_FIELD: Final = "confirm"
CONFIRMED: Final = "yes"

SAVED: Final = "Saved."
REMOVED: Final = "Removed."
STORE_FAILED: Final = "Windows Credential Manager failed. The row shows what is stored now."
NOT_SET: Final = "Not set"
TIME_UNKNOWN: Final = "Set, last update time unknown"
UNREADABLE: Final = "Unknown: Windows Credential Manager could not be read"
SHOWN_TIME_FORMAT: Final = "%Y-%m-%d %H:%M"


class Role(StrEnum):
    """The ARIA role of a row's notice."""

    STATUS = "status"
    ALERT = "alert"


@dataclass(frozen=True, slots=True)
class Notice:
    """What happened to a row: ``Saved.``, ``Removed.``, or why nothing changed."""

    role: Role
    text: str


@dataclass(frozen=True, slots=True)
class CredentialRow:
    """One credential's row, as ``credential_row.html`` renders it. It never holds a value."""

    key: str
    label: str
    required: bool
    state: str
    removable: bool  # the credential is set, so the row offers Remove
    notice: Notice | None = None
    confirming: bool = False  # the row asks whether to remove the credential


def state_text(status: CredentialStatus) -> str:
    """The row's state, as the Operator reads it."""
    if status.state is CredentialState.UNKNOWN:
        return UNREADABLE
    if status.state is CredentialState.NOT_SET:
        return NOT_SET
    if status.updated_at is None:
        return TIME_UNKNOWN
    return f"Set, last updated {status.updated_at.astimezone(UTC):{SHOWN_TIME_FORMAT}} UTC"


def credential_row(
    status: CredentialStatus, *, notice: Notice | None = None, confirming: bool = False
) -> CredentialRow:
    """The row of ``status``, with an optional notice, or asking to confirm a removal."""
    found = status.credential
    return CredentialRow(
        key=str(found.key),
        label=found.label,
        required=found.required,
        state=state_text(status),
        removable=status.state is CredentialState.SET,
        notice=notice,
        confirming=confirming,
    )


def page_rows(statuses: Iterable[CredentialStatus]) -> list[CredentialRow]:
    """Every row of the page, without notices."""
    return [credential_row(status) for status in statuses]


def saved() -> Notice:
    return Notice(Role.STATUS, SAVED)


def removed() -> Notice:
    return Notice(Role.STATUS, REMOVED)


def refused(reason: str) -> Notice:
    """Why a value was refused; ``reason`` never holds the value."""
    return Notice(Role.ALERT, f"Nothing was saved: {reason}.")


def store_failed() -> Notice:
    return Notice(Role.ALERT, STORE_FAILED)


def single_value(fields: FormFields, name: str) -> str:
    """The one value of the field ``name``; raises ``MalformedForm`` when it is repeated."""
    values = fields[name]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    return values[0]


def _declared_key(text: str) -> str:
    if text not in {str(found.key) for found in declared()}:
        raise MalformedForm(WRONG_FIELDS)
    return text


def submitted_save(fields: FormFields) -> tuple[str, str]:
    """The ``credential`` and ``value`` of a save. Raises ``MalformedForm`` unless,
    ``csrf_token`` aside, the fields are exactly ``credential`` and ``value``, each given once,
    and ``credential`` is declared."""
    if set(fields) - {CSRF_FIELD} != {CREDENTIAL_FIELD, VALUE_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    key = single_value(fields, CREDENTIAL_FIELD)
    value = single_value(fields, VALUE_FIELD)
    return _declared_key(key), value


def submitted_remove(fields: FormFields) -> tuple[str, bool]:
    """The ``credential`` of a removal and whether it is confirmed. Raises ``MalformedForm``
    unless, ``csrf_token`` aside, the fields are ``credential`` and optionally ``confirm``, each
    given once, ``credential`` is declared, and ``confirm`` is exactly ``yes``."""
    names = set(fields) - {CSRF_FIELD}
    if names not in ({CREDENTIAL_FIELD}, {CREDENTIAL_FIELD, CONFIRM_FIELD}):
        raise MalformedForm(WRONG_FIELDS)
    key = single_value(fields, CREDENTIAL_FIELD)
    confirmed = CONFIRM_FIELD in names
    if confirmed and single_value(fields, CONFIRM_FIELD) != CONFIRMED:
        raise MalformedForm(WRONG_FIELDS)
    return _declared_key(key), confirmed
