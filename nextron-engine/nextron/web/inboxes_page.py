"""The Sending Inboxes panel (FR-32, AD-14, AD-21, UX-DR3, UX-DR26): register the inboxes that
Sequences bind to, and list them.

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
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from nextron.app.queries.sending import SendingInboxView
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
