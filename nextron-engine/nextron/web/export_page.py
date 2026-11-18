"""The Lead page's subject access export (FR-52, AD-13, AD-17, UX-DR3, Story 3.15): one "Export
data (JSON)" form per Contact, its POST, and the one-time download. Views and helpers for
``create_app``'s routes, which stay thin; this module reads nothing itself and imports from
``nextron.app`` only ``nextron.app.queries.*``.

**The form** (``contact_export_form.html``): the Lead page's "Data subject requests" section,
after the Contacts table and outside any ``hx-disable``, holds one per Contact. Each is
``method="post"`` with ``hx-post="/contacts/<id>/export"`` and ``{{ csrf_field() }}``, names the
Contact (``ExportForm.label``: its name, else its email) in a ``<span hx-disable>`` through
``|plain``, and has one ``request_ref`` input (``maxlength`` 200) and the button "Export data
(JSON)". ``form_context(contact_id, name, email, notice=None)`` is what the template renders
alone (``export_form``); ``lead_forms(contacts)`` gives the Lead page one ``ExportForm`` per
Contact (``export_forms``), from the Lead page's ``ContactRow`` rows.

**The POST** (``POST /contacts/{contact_id}/export``, ``contact_export``): its fields, apart
from ``csrf_token``, must be exactly one ``request_ref`` (``submitted_reference``; anything else
is ``MalformedForm``, 400). A reference Story 3.13's ``check_request_ref`` refuses (its
``ValueError``, checked before any unit of work) answers 200 with the form alone and the refusal
(``role="alert"``), which HTMX swaps in for the form (``hx-target="this"
hx-swap="outerHTML"``); the typed reference is never rendered back. An unknown Contact answers
a plain 404, ``CONTACT_NOT_FOUND``. Success answers 204 with ``HX-Redirect: /exports/<ticket>``:
HTMX swaps nothing and points the browser at the download, which it saves as an attachment, so
the page stays.

**Tickets.** ``ExportTickets(monotonic)`` holds each built file in memory only, under a lock,
for one fetch within ``TICKET_SECONDS`` (60) seconds of ``issue(csrf_token, export)``, bound to
the session that asked (its CSRF token, compared with ``hmac.compare_digest``). A ticket is
``secrets.token_urlsafe(32)``. At most ``MAX_TICKETS`` (16) are held: issuing one more drops the
oldest. ``take(ticket, csrf_token)`` removes the ticket whatever happens next, so a ticket is
presented once: it answers the file only when the ticket is held, younger than 60 s and bound to
that session, else ``None``. Expired tickets are dropped on every ``issue`` and ``take``.

**The download** (``GET /exports/{ticket}``, ``contact_export_download``): like ``GET /enter``,
it changes in-memory state only (the ticket) and never the database. ``download_response``
answers 200 ``application/json`` with ``Content-Disposition: attachment;
filename="contact-<ULID>-export.json"`` and ``Cache-Control: no-store``; a missing, expired,
used or other-session ticket answers a plain 404, ``EXPORT_EXPIRED``, also ``no-store``.

Nothing here logs: no value, file name, ticket or exported content. A ticket in a URL is a run
of 43 ``[A-Za-z0-9_-]`` characters, which the log redaction (rule 9) masks as ``<key>`` in the
server's access log.
"""

import hmac
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, Protocol

from fastapi.responses import Response

from nextron.domain.contact_export import ContactExport
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

FORM_TEMPLATE: Final = "contact_export_form.html"
REFERENCE_FIELD: Final = "request_ref"
TICKET_SECONDS: Final = 60.0
MAX_TICKETS: Final = 16
TICKET_BYTES: Final = 32
JSON_MEDIA_TYPE: Final = "application/json"
CONTACT_NOT_FOUND: Final = "Contact not found"
EXPORT_EXPIRED: Final = "Export expired: export again from the Lead page"
EXPORT_PATH: Final = "/contacts/{contact_id}/export"
DOWNLOAD_PATH: Final = "/exports/{ticket}"


class ContactLike(Protocol):
    """What a form needs of a Contact: its id, its name and its email."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def email(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ExportForm:
    """One Contact's "Export data (JSON)" form: where it posts, whom it names and the refusal
    to show, if any."""

    contact_id: str
    label: str
    notice: str | None = None

    @property
    def action(self) -> str:
        return EXPORT_PATH.format(contact_id=self.contact_id)

    @property
    def input_id(self) -> str:
        return f"export-ref-{self.contact_id}"


def export_form(
    contact_id: str, name: str | None, email: str | None, notice: str | None = None
) -> ExportForm:
    """The form of the Contact ``contact_id``, named by its name, else its email."""
    return ExportForm(contact_id, name or email or "", notice)


def form_context(
    contact_id: str, name: str | None, email: str | None, *, notice: str | None = None
) -> dict[str, object]:
    """What ``contact_export_form.html`` renders alone: the one form, with ``notice``."""
    return {"export_form": export_form(contact_id, name, email, notice)}


def lead_forms(contacts: Iterable[ContactLike]) -> tuple[ExportForm, ...]:
    """One form per Contact, in the order given."""
    return tuple(export_form(contact.id, contact.name, contact.email) for contact in contacts)


def submitted_reference(fields: FormFields) -> str:
    """The one ``request_ref`` the form posts; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {REFERENCE_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    if len(fields[REFERENCE_FIELD]) != 1:
        raise MalformedForm(REPEATED_FIELD)
    return fields[REFERENCE_FIELD][0]


@dataclass(frozen=True, slots=True)
class _Held:
    csrf_token: str
    issued_at: float
    export: ContactExport


class ExportTickets:
    """Built export files, held in memory for one fetch by the session that asked; see the
    module docstring."""

    def __init__(
        self,
        monotonic: Callable[[], float],
        *,
        lifetime: float = TICKET_SECONDS,
        capacity: int = MAX_TICKETS,
    ) -> None:
        self._monotonic = monotonic
        self._lifetime = lifetime
        self._capacity = capacity
        self._lock = threading.Lock()
        self._held: OrderedDict[str, _Held] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._held)

    def _drop_expired(self, now: float) -> None:
        expired = [
            ticket
            for ticket, held in self._held.items()
            if now - held.issued_at >= self._lifetime
        ]
        for ticket in expired:
            del self._held[ticket]

    def issue(self, csrf_token: str, export: ContactExport) -> str:
        """Hold ``export`` for the session of ``csrf_token``; return its new ticket."""
        if not isinstance(csrf_token, str) or not csrf_token:
            raise ValueError("a ticket is bound to a session's CSRF token")
        if not isinstance(export, ContactExport):
            raise TypeError(f"a ticket holds a ContactExport, not {type(export).__name__}")
        ticket = secrets.token_urlsafe(TICKET_BYTES)
        with self._lock:
            now = self._monotonic()
            self._drop_expired(now)
            while len(self._held) >= self._capacity:
                self._held.popitem(last=False)  # the oldest first
            self._held[ticket] = _Held(csrf_token, now, export)
        return ticket

    def take(self, ticket: str, csrf_token: str | None) -> ContactExport | None:
        """The file held under ``ticket`` for this session, once, within the lifetime; else
        ``None``. The ticket is dropped either way."""
        with self._lock:
            now = self._monotonic()
            held = self._held.pop(ticket, None) if isinstance(ticket, str) else None
            self._drop_expired(now)
        if held is None or not isinstance(csrf_token, str):
            return None
        if now - held.issued_at >= self._lifetime:
            return None
        if not hmac.compare_digest(held.csrf_token.encode(), csrf_token.encode()):
            return None
        return held.export


def download_response(export: ContactExport) -> Response:
    """The file as an attachment: ``application/json``, its name, and ``no-store``."""
    return Response(
        export.body,
        media_type=JSON_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "Cache-Control": "no-store",
        },
    )
