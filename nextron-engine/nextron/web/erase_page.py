"""The erase confirmation step (FR-52, UX-DR3, UX-DR17, AD-13, AD-17; Stories 3.13 and 3.14):
the view of ``create_app``'s two erase routes, which stay thin. Both declare
``@personal_data(True)`` (the step names the Contact), so both send ``Cache-Control:
no-store``. This module reads nothing itself, and imports from ``nextron.app`` only
``nextron.app.queries.*``.

- ``GET /leads/{lead_id}/contacts/{contact_id}/erase`` (``contact_erase_page``) renders
  ``contact_erase.html`` (the Lead's name, linked to its page, then ``contact_erase_panel.html``)
  in the GET's read-only unit of work. The Lead page links it from each Contact that isn't
  erased ("Erase (data subject request)", ``erase_href``).
- ``POST`` to the same path (``contact_erase``) answers the panel alone, which HTMX swaps in
  (``hx-target="#erase-panel"``, ``hx-swap="outerHTML"``).

A ``lead_id`` that ``queries.discovery.survivor_of`` doesn't resolve, an unknown ``contact_id``
(a text that isn't a ULID included) and a Contact that isn't on the Lead's survivor all answer
404 with ``lead_missing.html``, which echoes no id.

**The panel** (``panel_context``), in order: the notice of the last POST, when there is one
(``role="status"``, or ``role="alert"`` for a refusal or a failure); then, for a Contact that
isn't erased, the Contact named (name, role, email and LinkedIn URL, "—" when blank), what
erasure does and that it can't be undone, and the form: ``method="post"``, ``hx-post``,
``csrf_field()``, one ``request_ref`` text input (at most 200 characters; its hint says "a
ticket or letter reference, never the person's name or address") and the button "Erase
permanently". For an erased Contact the panel shows only "This Contact was erased on
<YYYY-MM-DD>." and no value of it, then its erasure receipts (Story 3.14), oldest first. The
Contact's values and the notice sit in elements carrying ``hx-disable`` and render through
``|plain``; the form is outside them, so HTMX still posts it.

**The POST** takes exactly ``csrf_token`` and one ``request_ref`` (``submitted_ref``): any other
field, a missing or a repeated one raises ``MalformedForm`` (400). The reference is checked
first (``check_request_ref`` with the Contact's name, or none once it is erased), and a
``ValueError`` re-renders the panel with its message (``refused``) and the typed reference.
Otherwise it runs ``Erasures.erase_on_request(contact_id, request_ref)`` in its own unit of
work: ``erased`` gives "Erased. Request <ref> completed." (``erased``), and ``erased=False``
"This Contact was already erased; nothing more was erased, and this request has its own
receipt." (``already_erased``); each links to the
receipt it wrote on the Lead page ("See the receipt on the Lead page", ``receipt_href``:
``/leads/<lead id>#receipt-<receipt id>``). Any exception logs
``contact_erasure_failed`` (``contact_id`` and ``error_type``, the exception's class name) and
gives "Erasure failed and was rolled back: nothing was erased, and the request stays open
(<ErrorType>)." (``failed``). Each answer is 200, read in a read-only unit of work after the
write, so a success never shows the erased values.

**Receipts (Story 3.14).** ``receipt_view(receipt)`` is how both the Lead page (under an erased
Contact's tombstone line) and this step show one erasure receipt, through
``erasure_receipt.html``: the heading "Receipt <YYYY-MM-DD>: <state>" (the state from
``nextron.domain.erasure_receipt.receipt_state``: "Complete", "In progress" or "Incomplete: N
failed"), under it the request it answers ("Request <ref>" for a ``dsr`` receipt, "Retention"
for a retention one, then ", already erased" for an ``already_erased`` one), and one line per
store, in ``ErasureStore`` order: its label, its status's label, its
detail and its time (``YYYY-MM-DD HH:MM UTC``). The receipt's element id is
``receipt-<receipt id>`` (``receipt_anchor``), which ``receipt_href`` links to. Every text is
built by code from counts, dates, ids and codes; the details still render through ``|plain``
inside ``hx-disable``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.queries.compliance import StoredReceipt
from nextron.app.queries.discovery import StoredLead
from nextron.app.queries.research import StoredContact
from nextron.domain.erasure_receipt import (
    ErasureStore,
    ReceiptOutcome,
    StoreStatus,
    receipt_state,
)
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "contact_erase.html"
PANEL_TEMPLATE: Final = "contact_erase_panel.html"
REQUEST_REF_FIELD: Final = "request_ref"
CSRF_FIELD: Final = "csrf_token"
ERASE_LABEL: Final = "Erase (data subject request)"
DATE_FORMAT: Final = "%Y-%m-%d"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
RECEIPT_LINK: Final = "See the receipt on the Lead page"
ALREADY_ERASED_TEXT: Final = (
    "This Contact was already erased; nothing more was erased, and this request has its own "
    "receipt."
)
RETENTION_LINE: Final = "Retention"
ALREADY_ERASED_MARK: Final = "already erased"
NO_VALUE: Final = "—"
STATUS: Final = "status"
ALERT: Final = "alert"
FAILED_EVENT: Final = "contact_erasure_failed"


@dataclass(frozen=True, slots=True)
class Notice:
    """The outcome of the last POST: its ARIA role and its text."""

    role: str
    text: str
    receipt_href: str | None = None
    receipt_label: str = RECEIPT_LINK


@dataclass(frozen=True, slots=True)
class ContactSummary:
    """The Contact as the step names it; a blank value shows "—"."""

    name: str
    role: str
    email: str
    linkedin_url: str


@dataclass(frozen=True, slots=True)
class StoreLine:
    """One store of a receipt as a page shows it."""

    label: str
    status: str
    detail: str
    at: str


@dataclass(frozen=True, slots=True)
class ReceiptView:
    """One erasure receipt as a page shows it: its anchor, its heading and its stores."""

    id: str
    anchor: str
    heading: str
    stores: tuple[StoreLine, ...]
    request: str = ""


def receipt_anchor(receipt_id: str) -> str:
    """The element id of the receipt ``receipt_id``."""
    return f"receipt-{receipt_id}"


def receipt_href(lead_id: str, receipt_id: str) -> str:
    """The Lead page's link to the receipt ``receipt_id``."""
    return f"/leads/{lead_id}#{receipt_anchor(receipt_id)}"


def _store_label(store: str) -> str:
    try:
        return ErasureStore(store).label
    except ValueError:  # a code the domain no longer knows renders as its own text
        return store


def _status_label(status: str) -> str:
    try:
        return StoreStatus(status).label
    except ValueError:
        return status


def receipt_view(receipt: StoredReceipt) -> ReceiptView:
    """The receipt as both pages show it; see the module docstring."""
    reading = receipt_state(row.status for row in receipt.stores)
    ref = receipt.request_ref
    request = RETENTION_LINE if ref is None else f"Request {ref}"
    if receipt.outcome == str(ReceiptOutcome.ALREADY_ERASED):
        request = f"{request}, {ALREADY_ERASED_MARK}"
    return ReceiptView(
        id=receipt.id,
        anchor=receipt_anchor(receipt.id),
        heading=f"Receipt {day(receipt.created_at)}: {reading.label}",
        stores=tuple(
            StoreLine(
                label=_store_label(row.store),
                status=_status_label(row.status),
                detail=row.detail,
                at=format(row.at, TIME_FORMAT),
            )
            for row in receipt.stores
        ),
        request=request,
    )


def erase_href(lead_id: str, contact_id: str) -> str:
    """The erase step's path for the Contact ``contact_id`` on the Lead ``lead_id``."""
    return f"/leads/{lead_id}/contacts/{contact_id}/erase"


def day(at: datetime) -> str:
    """``at`` as ``YYYY-MM-DD`` (UTC)."""
    return f"{at:{DATE_FORMAT}}"


def erased_text(erased_at: datetime) -> str:
    """The panel's whole content for an erased Contact."""
    return f"This Contact was erased on {day(erased_at)}."


def submitted_ref(fields: FormFields) -> str:
    """The one ``request_ref`` the form posts; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {REQUEST_REF_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[REQUEST_REF_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    return values[0]


def refused(message: str) -> Notice:
    return Notice(ALERT, message)


def erased(request_ref: str, receipt: str | None = None) -> Notice:
    """The success notice; ``receipt`` is the link to its receipt."""
    return Notice(STATUS, f"Erased. Request {request_ref} completed.", receipt_href=receipt)


def already_erased(receipt: str | None = None) -> Notice:
    """The notice of a repeat request; ``receipt`` is the link to its receipt."""
    return Notice(STATUS, ALREADY_ERASED_TEXT, receipt_href=receipt)


def failed(error_type: str) -> Notice:
    return Notice(
        ALERT,
        "Erasure failed and was rolled back: nothing was erased, and the request stays open "
        f"({error_type}).",
    )


def _shown(value: str | None) -> str:
    return value if value else NO_VALUE


def panel_context(
    lead: StoredLead,
    contact: StoredContact,
    *,
    notice: Notice | None = None,
    request_ref: str = "",
    receipts: Iterable[StoredReceipt] = (),
) -> dict[str, object]:
    """What ``contact_erase.html`` and ``contact_erase_panel.html`` render; see the module
    docstring. ``receipts`` are the Contact's erasure receipts, shown once it is erased."""
    context: dict[str, object] = {
        "lead_id": lead.id,
        "lead_name": lead.name,
        "notice": notice,
        "action": erase_href(lead.id, contact.id),
    }
    if contact.erased_at is not None:
        context["erased_text"] = erased_text(contact.erased_at)
        context["receipts"] = tuple(receipt_view(receipt) for receipt in receipts)
        return context
    context["contact"] = ContactSummary(
        name=_shown(contact.name),
        role=_shown(contact.role),
        email=_shown(contact.email),
        linkedin_url=_shown(contact.linkedin_url),
    )
    context["request_ref"] = request_ref
    return context
