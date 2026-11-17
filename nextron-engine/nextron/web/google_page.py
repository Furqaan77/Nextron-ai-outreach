"""The Google section of the Credentials page (AD-12, AD-13, AD-14, FR-56, NFR-18, UX-DR27).

``create_app`` serves it through ``GoogleAccess`` (``nextron.app.setup.api``). The section sits
under the credential rows of ``credentials.html``: one client row (``google_client_row.html``),
then one row per grant in ``GRANTS`` order (``google_grant_row.html``). Every answer goes
through ``page()``, so it is ``Cache-Control: no-store``; a refusal answers 200, because HTMX
swaps only 2xx responses. No row or answer ever holds a token, the client secret, a code, the
state, the consent URL or the path; a grant row shows its account address.

- ``POST /credentials/google/client`` (``google_client_import``, ``@personal_data(False)``)
  takes exactly the field ``path``, once (``csrf_token`` aside; ``submitted_path``), and runs
  ``GoogleAccess.import_client``. It answers with the client row: ``IMPORTED_DELETED``
  (``role="status"``), ``IMPORTED_KEPT``, ``Nothing was imported: <reason>.`` or
  ``STORE_FAILED`` (each ``role="alert"``). After an import the row asks Mac to reload the page,
  where the Grant buttons now appear.
- ``POST /credentials/google/grant`` (``google_grant_start``, ``@personal_data(True)``) takes
  exactly ``grant`` (``submitted_grant``) and runs ``GoogleAccess.start_grant``: the row, waiting
  (or with the failure), or ``IMPORT_FIRST`` (``role="alert"``) when no client is imported, or
  ``STORE_FAILED``. It is the only route that starts a flow.
- ``GET /credentials/google/grant/{grant}`` (``google_grant_row``, ``@personal_data(True)``):
  the row alone, which a waiting row polls; an unknown grant is 404. It never starts a flow.
- ``POST /credentials/google/remove`` (``google_grant_remove``, ``@personal_data(True)``) takes
  ``grant`` and an optional ``confirm`` that must be exactly ``yes`` (``submitted_remove``), in
  two steps like a credential: without ``confirm`` the row only asks; with it
  ``GoogleAccess.remove_grant`` runs and the row says ``Removed.`` and what happened to the
  token at Google.

Any other form raises ``MalformedForm`` (400), an unknown ``grant`` included.

**A grant row's state** (``grant_state_text``): ``Not granted``; ``Granted for <account>,
YYYY-MM-DD HH:MM UTC``; ``Needs re-consent: Google refused the stored grant on YYYY-MM-DD HH:MM
UTC. Grant it again.``; or the unreadable text. While a flow waits, the row says ``Waiting for
consent in your browser until HH:MM:SS UTC.`` and polls itself (``hx-get`` with
``hx-trigger="every 2s"`` and ``hx-swap="outerHTML"``); no other row polls. A failed flow reads
``Consent failed: <reason text>.`` (``role="alert"``, ``FAILURE_TEXTS``), followed by what
happened to the token (``REVOCATION_NOTES``). Grant and Remove appear only while the client is
imported and no flow waits; Remove only while there is a grant.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nextron.app.setup.api import (
    ClientStatus,
    ConsentOutcome,
    CredentialState,
    GrantState,
    GrantStatus,
    Revocation,
)
from nextron.domain.google_oauth import (
    GRANTS,
    INTERNAL_NOTIFY_DOMAIN,
    ConsentFailure,
)
from nextron.web.credentials_page import (
    CONFIRM_FIELD,
    CONFIRMED,
    REMOVED,
    SHOWN_TIME_FORMAT,
    STORE_FAILED,
    UNREADABLE,
    Notice,
    Role,
    single_value,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import WRONG_FIELDS, FormFields, MalformedForm

CLIENT_ROW_TEMPLATE: Final = "google_client_row.html"
GRANT_ROW_TEMPLATE: Final = "google_grant_row.html"
PATH_FIELD: Final = "path"
GRANT_FIELD: Final = "grant"
WAITING_TIME_FORMAT: Final = "%H:%M:%S"

IMPORTED_DELETED: Final = "Imported. The file was deleted."
IMPORTED_KEPT: Final = "Imported, but the file could not be deleted: delete it yourself."
IMPORT_FIRST: Final = "Import the Google Desktop client first."
NOT_IMPORTED: Final = "Not imported"
IMPORTED_TIME_UNKNOWN: Final = "Imported, import time unknown"
NOT_GRANTED: Final = "Not granted"
GRANTED_UNKNOWN: Final = "Granted, account and time unknown"

FAILURE_TEXTS: Final = {
    ConsentFailure.TIMED_OUT: "no answer from Google within 5 minutes",
    ConsentFailure.STATE_MISMATCH: (
        "the answer that reached Nextron did not match this request, so it was ignored"
    ),
    ConsentFailure.DECLINED: "consent was declined or cancelled on Google's page",
    ConsentFailure.EXCHANGE_FAILED: "Google did not accept the answer when Nextron redeemed it",
    ConsentFailure.NO_ACCOUNT: "Google did not confirm a verified account address",
    ConsentFailure.SCOPE_NOT_GRANTED: (
        "the permission was not ticked on Google's consent page; grant it again and tick it"
    ),
    ConsentFailure.NO_REFRESH_TOKEN: "Google returned no refresh token",
    ConsentFailure.NOT_INTERNAL_DOMAIN: INTERNAL_NOTIFY_DOMAIN,
    ConsentFailure.NOT_ALLOWLISTED: (
        "outside prod, the account must be on the Dev and test allowlist (Settings, Engine)"
    ),
    ConsentFailure.STORE_FAILED: "Windows Credential Manager failed, so nothing was stored",
    ConsentFailure.BROWSER_FAILED: "the default browser could not be opened",
    ConsentFailure.LISTENER_FAILED: "the local listener for Google's answer could not start",
    ConsentFailure.CRASHED: "an unexpected error stopped it",
}
REVOCATION_NOTES: Final = {
    Revocation.REVOKED: "Google access was revoked.",
    Revocation.FAILED: (
        "Revoking it at Google failed: remove Nextron at myaccount.google.com/permissions."
    ),
    Revocation.SHARED: (
        "It was not revoked, because a stored grant uses the same Google account, and revoking "
        "it would end that grant too."
    ),
}
REPLACED: Final = "The previous grant was replaced."


@dataclass(frozen=True, slots=True)
class ClientRow:
    """The client row, as ``google_client_row.html`` renders it."""

    state: str
    notice: Notice | None = None
    reload_hint: bool = False  # after an import: the Grant buttons appear on reload


@dataclass(frozen=True, slots=True)
class GrantRow:
    """One grant's row, as ``google_grant_row.html`` renders it. It never holds a token."""

    grant: str
    label: str
    scope: str
    state: str
    waiting: str | None  # "Waiting for consent ..." while a flow waits
    notice: Notice | None = None
    can_grant: bool = False
    removable: bool = False
    grant_again: bool = False  # the Grant button reads "Grant again"
    confirming: bool = False  # the row asks whether to remove the grant


def _utc(at: datetime, shown: str = SHOWN_TIME_FORMAT) -> str:
    return f"{at.astimezone(UTC):{shown}} UTC"


def client_state_text(status: ClientStatus) -> str:
    """The client row's state."""
    if status.state is CredentialState.UNKNOWN:
        return UNREADABLE
    if status.state is CredentialState.NOT_SET:
        return NOT_IMPORTED
    if status.updated_at is None:
        return IMPORTED_TIME_UNKNOWN
    return f"Imported, last updated {_utc(status.updated_at)}"


def client_row(
    status: ClientStatus, *, notice: Notice | None = None, reload_hint: bool = False
) -> ClientRow:
    return ClientRow(client_state_text(status), notice, reload_hint)


def grant_state_text(status: GrantStatus) -> str:
    """A grant row's state, as the Operator reads it."""
    if status.state is GrantState.UNKNOWN:
        return UNREADABLE
    if status.state is GrantState.NOT_GRANTED:
        return NOT_GRANTED
    if status.state is GrantState.NEEDS_RECONSENT and status.needs_reconsent_at is not None:
        return (
            f"Needs re-consent: Google refused the stored grant on "
            f"{_utc(status.needs_reconsent_at)}. Grant it again."
        )
    if status.account is None or status.granted_at is None:
        return GRANTED_UNKNOWN
    return f"Granted for {status.account}, {_utc(status.granted_at)}"


def _with_note(text: str, revocation: Revocation | None) -> str:
    return f"{text} {REVOCATION_NOTES[revocation]}" if revocation is not None else text


def outcome_notice(outcome: ConsentOutcome | None) -> Notice | None:
    """What the last flow's outcome says on the row, if anything."""
    if outcome is None:
        return None
    if outcome.failure is not None:
        text = f"Consent failed: {FAILURE_TEXTS[outcome.failure]}."
        return Notice(Role.ALERT, _with_note(text, outcome.revocation))
    if outcome.revocation is None:
        return None  # granted, and no earlier grant was replaced: the state says it all
    role = Role.ALERT if outcome.revocation is Revocation.FAILED else Role.STATUS
    return Notice(role, _with_note(REPLACED, outcome.revocation))


def removed_notice(revocation: Revocation | None) -> Notice:
    role = Role.ALERT if revocation is Revocation.FAILED else Role.STATUS
    return Notice(role, _with_note(REMOVED, revocation))


def import_first() -> Notice:
    return Notice(Role.ALERT, IMPORT_FIRST)


def store_failed() -> Notice:
    return Notice(Role.ALERT, STORE_FAILED)


def imported(file_deleted: bool) -> Notice:
    if file_deleted:
        return Notice(Role.STATUS, IMPORTED_DELETED)
    return Notice(Role.ALERT, IMPORTED_KEPT)


def not_imported(reason: str) -> Notice:
    """Why nothing was imported; ``reason`` never quotes the file or the path."""
    return Notice(Role.ALERT, f"Nothing was imported: {reason}.")


def grant_row(
    status: GrantStatus,
    client: ClientStatus,
    *,
    notice: Notice | None = None,
    confirming: bool = False,
) -> GrantRow:
    """The row of ``status``; ``notice`` replaces the outcome's notice when given."""
    waiting = status.waiting_until is not None
    has_grant = status.state in (GrantState.GRANTED, GrantState.NEEDS_RECONSENT)
    actions = client.state is CredentialState.SET and not waiting
    return GrantRow(
        grant=str(status.spec.grant),
        label=status.spec.label,
        scope=status.spec.scope,
        state=grant_state_text(status),
        waiting=(
            f"Waiting for consent in your browser until "
            f"{_utc(status.waiting_until, WAITING_TIME_FORMAT)}."
            if status.waiting_until is not None
            else None
        ),
        notice=notice if notice is not None else outcome_notice(status.outcome),
        can_grant=actions,
        removable=actions and has_grant,
        grant_again=has_grant,
        confirming=confirming,
    )


def grant_rows(statuses: Iterable[GrantStatus], client: ClientStatus) -> list[GrantRow]:
    return [grant_row(status, client) for status in statuses]


def _grant_value(text: str) -> str:
    if text not in {str(spec.grant) for spec in GRANTS}:
        raise MalformedForm(WRONG_FIELDS)
    return text


def submitted_path(fields: FormFields) -> str:
    """The ``path`` of a client import. Raises ``MalformedForm`` unless, ``csrf_token`` aside,
    the only field is ``path``, given once."""
    if set(fields) - {CSRF_FIELD} != {PATH_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    return single_value(fields, PATH_FIELD)


def submitted_grant(fields: FormFields) -> str:
    """The ``grant`` of a Grant click. Raises ``MalformedForm`` unless, ``csrf_token`` aside,
    the only field is ``grant``, given once, naming a grant."""
    if set(fields) - {CSRF_FIELD} != {GRANT_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    return _grant_value(single_value(fields, GRANT_FIELD))


def submitted_remove(fields: FormFields) -> tuple[str, bool]:
    """The ``grant`` of a removal and whether it is confirmed. Raises ``MalformedForm`` unless,
    ``csrf_token`` aside, the fields are ``grant`` and optionally ``confirm``, each given once,
    ``grant`` names a grant, and ``confirm`` is exactly ``yes``."""
    names = set(fields) - {CSRF_FIELD}
    if names not in ({GRANT_FIELD}, {GRANT_FIELD, CONFIRM_FIELD}):
        raise MalformedForm(WRONG_FIELDS)
    grant = single_value(fields, GRANT_FIELD)
    confirmed = CONFIRM_FIELD in names
    if confirmed and single_value(fields, CONFIRM_FIELD) != CONFIRMED:
        raise MalformedForm(WRONG_FIELDS)
    return _grant_value(grant), confirmed


def is_grant(text: str) -> bool:
    return text in {str(spec.grant) for spec in GRANTS}
