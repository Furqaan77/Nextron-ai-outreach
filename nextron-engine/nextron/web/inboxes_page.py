"""The Sending Inboxes panel (FR-32, AD-14, AD-21, UX-DR3, UX-DR26): register the inboxes that
Sequences bind to, and list them; and its Sending Domain authentication section (Story 6.5,
FR-64).

``create_app`` serves it from ``queries.sending.inboxes()`` through ``panel_context``; both
routes are ``@personal_data(True)`` (the panel shows inbox addresses), so every answer is
``no-store``:

- ``GET /inboxes`` (``inboxes_page``) renders ``inboxes.html``: the layout around ``{% include
  "inboxes_panel.html" %}``. The Settings page links to it; it isn't in the navigation, whose
  sections UX-DR1 fixes.
- ``POST /inboxes`` (``inbox_register``) takes exactly the fields ``address`` and ``label``,
  each once (``csrf_token`` aside; ``submitted_inbox``); any other form raises
  ``MalformedForm`` (400). It runs ``Sending.register_inbox`` in its own unit of work, then
  renders ``inboxes_panel.html`` alone in a read-only one, which HTMX swaps in for the panel
  (``hx-target="#inboxes-panel" hx-swap="outerHTML"``): registered, with ``Inbox registered.``
  (``role="status"``) and the form cleared; refused (``InboxRefused``), with the refusal's
  message (``role="alert"``) and the typed values kept in the form. A refusal answers 200,
  because HTMX swaps only 2xx responses. The form has ``hx-sync="this:drop"``, so a double
  click sends one POST, not a second whose "Inbox already registered" would hide the first's
  notice.

In order, the panel shows the notice of the last POST, when there is one; one warning per
crowded Sending Domain (``crowding_warning``: "More than 3 inboxes on <domain> raises
deliverability risk"); the form (Address, Label, "Add inbox"); then a table with Label, Address,
Sending Domain and Status, oldest first, where a ``connecting`` inbox's status adds
``CONNECTING_NOTE`` ("Gmail connection and warm-up not set up yet; nothing can be sent from
it"). With no inboxes the table is replaced by "No Sending Inboxes registered yet." Every value
is autoescaped; nothing here is marked safe.

**Sending Domain authentication (Story 6.5).** ``inboxes.html`` includes
``inbox_auth_panel.html`` after the panel: ``<section id="domain-auth">``, headed "Sending Domain
authentication", which HTMX re-fetches every 30 seconds from ``GET /inboxes/auth``
(``inbox_auth_panel``) so a Recheck's result appears without resetting the registration form.
``auth_context`` renders it from ``DomainChecks.auths()`` (``queries.sending.domain_auths`` at
the clock's now): one row per listed Sending Domain, by domain, with the Sending Domain, Last
checked (``YYYY-MM-DD HH:MM UTC`` or "Not checked yet"), SPF, DKIM, DMARC and Alignment (each
``Pass``, ``Fail: <reason text>`` or ``Not checked yet``), Cold sends (the verdict text; when
blocked the cell has ``class="auth-blocked"``) and a Recheck form. With no listed domain it
shows "No Sending Domains to check yet." Both its routes are ``@personal_data(False)``: they
show domain names and check results only.

``POST /inboxes/auth/recheck`` (``inbox_auth_recheck``) takes exactly one ``sending_domain_id``,
a ULID (``csrf_token`` aside; ``submitted_recheck``); anything else raises ``MalformedForm``
(400). It runs ``DomainChecks.recheck`` in its own unit of work, then renders the section alone
in a read-only one with ``Recheck queued for <domain>; the result appears here when it lands.``
(``role="status"``) or ``That Sending Domain has no inboxes to check.`` (``role="alert"``).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from typing import Final

from nextron.app.queries.sending import DomainAuthView, SendingInboxView
from nextron.domain.domain_auth import REASON_TEXTS, AuthItem, AuthReason
from nextron.domain.ids import is_ulid
from nextron.domain.sending_inbox import (
    CONNECTING_NOTE,
    InboxRefused,
    InboxStatus,
    crowded_domains,
    crowding_warning,
)
from nextron.web.credentials_page import Notice, Role
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "inboxes.html"
PANEL_TEMPLATE: Final = "inboxes_panel.html"
AUTH_TEMPLATE: Final = "inbox_auth_panel.html"
SENDING_DOMAIN_ID_FIELD: Final = "sending_domain_id"
NO_DOMAINS: Final = "No Sending Domains to check yet."
PASS: Final = "Pass"
FAIL_PREFIX: Final = "Fail: "
NOT_CHECKED: Final = "Not checked yet"
LAST_CHECKED_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
RECHECK_QUEUED: Final = "Recheck queued for {domain}; the result appears here when it lands."
RECHECK_REFUSED: Final = "That Sending Domain has no inboxes to check."
ADDRESS_FIELD: Final = "address"
LABEL_FIELD: Final = "label"
FORM_FIELDS: Final = frozenset({ADDRESS_FIELD, LABEL_FIELD})
REGISTERED: Final = "Inbox registered."
NO_INBOXES: Final = "No Sending Inboxes registered yet."


@dataclass(frozen=True, slots=True)
class InboxRow:
    """One inbox as the panel's table shows it."""

    label: str
    address: str
    domain: str
    status: str
    note: str | None  # CONNECTING_NOTE for a connecting inbox


def inbox_row(inbox: SendingInboxView) -> InboxRow:
    """``inbox`` as a table row."""
    note = CONNECTING_NOTE if inbox.status is InboxStatus.CONNECTING else None
    return InboxRow(inbox.label, inbox.address, inbox.domain, str(inbox.status), note)


def registered() -> Notice:
    return Notice(Role.STATUS, REGISTERED)


def refused(refusal: InboxRefused) -> Notice:
    return Notice(Role.ALERT, refusal.message)


def panel_context(
    inboxes: Iterable[SendingInboxView],
    *,
    notice: Notice | None = None,
    address: str = "",
    label: str = "",
) -> dict[str, object]:
    """What ``inboxes_panel.html`` renders: the notice, the crowding warnings, the form's values
    and the rows."""
    listed = list(inboxes)
    crowded = crowded_domains(inbox.domain for inbox in listed)
    return {
        "notice": notice,
        "warnings": [crowding_warning(domain) for domain in crowded],
        "address": address,
        "label": label,
        "rows": [inbox_row(inbox) for inbox in listed],
        "no_inboxes": NO_INBOXES,
    }


def submitted_inbox(fields: FormFields) -> tuple[str, str]:
    """The ``address`` and ``label`` the form posts, each once; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != FORM_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    address = fields[ADDRESS_FIELD]
    label = fields[LABEL_FIELD]
    if len(address) != 1 or len(label) != 1:
        raise MalformedForm(REPEATED_FIELD)
    return address[0], label[0]


# Sending Domain authentication (Story 6.5) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthRow:
    """One listed Sending Domain as the authentication table shows it; ``items`` are the SPF,
    DKIM, DMARC and Alignment cells, in that order."""

    sending_domain_id: str
    domain: str
    last_checked: str
    items: tuple[str, ...]
    cold_sends: str
    blocked: bool


def _item_cell(reason: AuthReason | None) -> str:
    if reason is None:
        return NOT_CHECKED
    if reason is AuthReason.OK:
        return PASS
    return f"{FAIL_PREFIX}{REASON_TEXTS[reason]}"


def auth_row(view: DomainAuthView) -> AuthRow:
    """``view`` as a table row."""
    latest = view.latest
    if latest is None:
        last_checked = NOT_CHECKED
        items = tuple(_item_cell(None) for _ in AuthItem)
    else:
        last_checked = f"{latest.checked_at.astimezone(UTC):{LAST_CHECKED_FORMAT}}"
        items = tuple(_item_cell(latest.reasons.get(item)) for item in AuthItem)
    return AuthRow(
        view.sending_domain_id,
        view.domain,
        last_checked,
        items,
        view.verdict.text,
        view.verdict.blocked,
    )


def recheck_queued(domain: str) -> Notice:
    return Notice(Role.STATUS, RECHECK_QUEUED.format(domain=domain))


def recheck_refused() -> Notice:
    return Notice(Role.ALERT, RECHECK_REFUSED)


def auth_context(
    views: Iterable[DomainAuthView], *, notice: Notice | None = None
) -> dict[str, object]:
    """What ``inbox_auth_panel.html`` renders: the Recheck notice and the rows."""
    return {
        "auth_notice": notice,
        "auth_rows": [auth_row(view) for view in views],
        "no_domains": NO_DOMAINS,
    }


def submitted_recheck(fields: FormFields) -> str:
    """The one ``sending_domain_id`` ULID a Recheck form posts; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {SENDING_DOMAIN_ID_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[SENDING_DOMAIN_ID_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    if not is_ulid(values[0]):
        raise MalformedForm(WRONG_FIELDS)
    return values[0]
