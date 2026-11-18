"""The Channel Permission page of a Contact (FR-24, FR-47, FR-41, UX-DR22, AD-13; Story 10.7):
the view of ``create_app``'s Channel Permission routes, which stay thin. Every route declares
``@personal_data(True)`` (the page names the Contact and shows their number or handle), so each
answer sends ``Cache-Control: no-store``. This module reads nothing itself, and imports from
``nextron.app`` only ``nextron.app.queries.*`` and the types of
``nextron.app.pipeline.channel_permissions``. It builds no link to WhatsApp or Instagram.

- ``GET /leads/{lead_id}/contacts/{contact_id}/permissions`` (``contact_permissions_page``)
  renders ``channel_permissions.html`` (the Contact and its Lead, then
  ``channel_permissions_panel.html``) in the GET's read-only unit of work. The Lead page's
  Contacts panel links it from each Contact that isn't erased ("Channel permissions",
  ``permissions_href``), in the Action cell.
- ``POST`` to the same path (``contact_permission_record``) records one;
  ``POST .../permissions/{permission_id}/inbound`` (``contact_permission_inbound``),
  ``.../revoke`` (``contact_permission_revoke``) and ``.../do-not-contact``
  (``contact_permission_do_not_contact``) act on one. Each answers the panel alone (200), which HTMX
  swaps in (``hx-target="#permissions-panel"``, ``hx-swap="outerHTML"``).

A ``lead_id`` that ``queries.discovery.survivor_of`` doesn't resolve, an unknown ``contact_id``
and a Contact that isn't on the Lead's survivor all answer 404 with ``lead_missing.html``, which
echoes no id.

**The panel** (``panel_context``), in order: the notice of the last POST (``role="status"``, or
``role="alert"`` with "Nothing was changed." and the form-level problem for a refusal); for an
erased Contact only "This Contact was erased: it has no Channel Permissions to show." and no
form; otherwise the Contact's permissions, newest first, each with its channel, what the
prospect did, when, its number or handle, its Evidence, its status (and when it was revoked)
and, for an active Instagram one, "24-hour window closes at <time>" or "24-hour window closed
at <time>: don't send" (that line also of class ``window-closed``); an active Instagram one has the "Update last inbound message" form
(``last_inbound_at``), an active one "Revoke", and every one "Opted out on this channel". Then
the record form: the channel (WhatsApp or Instagram), what the prospect did (only
``VALID_ACTIONS``, grouped by channel), the date and time, the Evidence (at most 300
characters), the WhatsApp number and the Instagram handle, under the warning
``NOT_QUALIFYING`` ("An email reply, or a number in a signature or on a website, doesn't
qualify", ``role="note"``). Times are shown and read in the ``sender_timezone`` setting; while
it is unset they show in UTC and the page says to set it. Every form posts with ``method="post"``
and ``hx-post`` and carries the CSRF field. A refused form is re-rendered with the submitted
values, each refused field marked ``aria-invalid="true"`` with its message beside it. Stored
and typed text renders through ``|plain`` inside elements carrying ``hx-disable``; the forms sit
outside them.

**The POSTs** take exactly ``csrf_token`` and their fields, each once (``submitted``): the
record form ``channel``, ``prospect_action``, ``occurred_at``, ``evidence``,
``whatsapp_number`` and ``instagram_handle``; the inbound form ``last_inbound_at``; revoke and
opt-out nothing else. Anything else raises ``MalformedForm`` (400). For an erased Contact
nothing runs ("This Contact was erased; nothing changed."). Otherwise each runs its
``ChannelPermissions`` use case in its own unit of work and renders the panel in a read-only
one: "Permission recorded.", "Last inbound message updated.", "Permission revoked." or
"Opted out: <n> suppression entries written, <e> entries already there, <m> permissions revoked.
Every channel is blocked for this person from now on." (each count singular at 1), or, when no
suppression key is known for the person, "Opted out: <m> permissions revoked. No suppression key
is known for this person, so nothing was added to the Suppression List."; or, for a
``PermissionRefused``, the refusal.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, tzinfo
from typing import Final

from nextron.app.pipeline.channel_permissions import OptedOut, PermissionsView
from nextron.app.queries.discovery import StoredLead
from nextron.app.queries.pipeline import StoredPermission
from nextron.app.queries.research import StoredContact, StoredEvidence
from nextron.domain.channel_permission import (
    ACTION_FIELD,
    ACTION_LABELS,
    CHANNEL_FIELD,
    CHANNEL_LABELS,
    EVIDENCE_FIELD,
    FORM_FIELD,
    HANDLE_FIELD,
    INBOUND_FIELD,
    MAX_EVIDENCE,
    MAX_HANDLE_LENGTH,
    NOT_QUALIFYING,
    NUMBER_FIELD,
    OCCURRED_FIELD,
    VALID_ACTIONS,
    PermissionChannel,
    PermissionStatus,
    ProspectAction,
    local_text,
    window_open,
    window_text,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "channel_permissions.html"
PANEL_TEMPLATE: Final = "channel_permissions_panel.html"
LINK_LABEL: Final = "Channel permissions"
RECORD_FIELDS: Final = frozenset(
    {CHANNEL_FIELD, ACTION_FIELD, OCCURRED_FIELD, EVIDENCE_FIELD, NUMBER_FIELD, HANDLE_FIELD}
)
INBOUND_FIELDS: Final = frozenset({INBOUND_FIELD})
NO_FIELDS: Final[frozenset[str]] = frozenset()
RECORD: Final = "record"
INBOUND: Final = "inbound"
REVOKE: Final = "revoke"
OPT_OUT: Final = "opt_out"
STATUS: Final = "status"
ALERT: Final = "alert"
NOTHING_CHANGED: Final = "Nothing was changed."
ERASED_TEXT: Final = "This Contact was erased: it has no Channel Permissions to show."
ERASED_UNCHANGED: Final = "This Contact was erased; nothing changed."
ZONE_UNSET: Final = (
    "The sender's timezone isn't set: times show in UTC here, and recording waits until it is "
    "set on the Settings page."
)
NO_VALUE: Final = "—"
LABELS: Final[Mapping[str, str]] = {
    CHANNEL_FIELD: "Channel",
    ACTION_FIELD: "What the prospect did",
    OCCURRED_FIELD: "When",
    EVIDENCE_FIELD: "Evidence",
    NUMBER_FIELD: "WhatsApp number",
    HANDLE_FIELD: "Instagram handle",
    INBOUND_FIELD: "Last inbound message",
}
HINTS: Final[Mapping[str, str]] = {
    EVIDENCE_FIELD: (
        f"What the prospect did, in at most {MAX_EVIDENCE} characters, such as the first "
        "words of their message."
    ),
    NUMBER_FIELD: "For WhatsApp: + and 8 to 15 digits, such as +447700900123.",
    HANDLE_FIELD: (
        f"For Instagram: 1 to {MAX_HANDLE_LENGTH} letters, digits, dots or underscores; "
        "a leading @ is dropped."
    ),
}


@dataclass(frozen=True, slots=True)
class Notice:
    """The outcome of the last POST: its ARIA role and its text."""

    role: str
    text: str


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused POST: which form (``record``, ``inbound``, ``revoke`` or ``opt_out``), its
    permission (``None`` for record), the problems by field (``form`` for the whole form) and
    the submitted values."""

    action: str
    permission_id: str | None
    problems: Mapping[str, str]
    values: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class FormField:
    """One field of a form."""

    name: str
    label: str
    value: str
    hint: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Option:
    """One ``<option>``."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class OptionGroup:
    """One ``<optgroup>``: a channel and its actions."""

    label: str
    options: tuple[Option, ...]


@dataclass(frozen=True, slots=True)
class PermissionRow:
    """One permission as the panel shows it, with the actions it offers."""

    id: str
    channel: str
    action: str
    occurred: str
    identifier_label: str
    identifier: str
    evidence: str
    status: str
    revoked: str | None
    window: str | None
    window_closed: bool
    inbound: FormField | None
    can_revoke: bool
    base_href: str


@dataclass(frozen=True, slots=True)
class RecordForm:
    """The record form: its channel, action, time and text fields."""

    channel: FormField
    channels: tuple[Option, ...]
    action: FormField
    groups: tuple[OptionGroup, ...]
    occurred: FormField
    evidence: FormField
    number: FormField
    handle: FormField


def permissions_href(lead_id: str, contact_id: str) -> str:
    """The Channel Permission page's path for the Contact on the Lead."""
    return f"/leads/{lead_id}/contacts/{contact_id}/permissions"


def submitted(fields: FormFields, expected: frozenset[str]) -> dict[str, str]:
    """The form's fields, each given once, exactly ``expected``; ``MalformedForm`` otherwise."""
    names = set(fields) - {CSRF_FIELD}
    if names != expected:
        raise MalformedForm(WRONG_FIELDS)
    single = {}
    for name in sorted(names):
        values = fields[name]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        single[name] = values[0]
    return single


def record_arguments(values: Mapping[str, str]) -> tuple[str, str, str, str, str, str]:
    """The record form's values in ``ChannelPermissions.record``'s order (after the Contact)."""
    return (
        values[CHANNEL_FIELD],
        values[ACTION_FIELD],
        values[OCCURRED_FIELD],
        values[EVIDENCE_FIELD],
        values[NUMBER_FIELD],
        values[HANDLE_FIELD],
    )


def recorded() -> Notice:
    return Notice(STATUS, "Permission recorded.")


def inbound_updated() -> Notice:
    return Notice(STATUS, "Last inbound message updated.")


def revoked() -> Notice:
    return Notice(STATUS, "Permission revoked.")


def _count(number: int, singular: str, plural: str) -> str:
    return f"{number} {singular if number == 1 else plural}"


def opted_out(result: OptedOut) -> Notice:
    """The opt-out's notice; see the module docstring."""
    revoked_text = _count(len(result.revoked), "permission", "permissions")
    if not result.written and not result.existing:
        return Notice(
            STATUS,
            f"Opted out: {revoked_text} revoked. No suppression key is known for this person, "
            "so nothing was added to the Suppression List.",
        )
    return Notice(
        STATUS,
        f"Opted out: {_count(result.written, 'suppression entry', 'suppression entries')} "
        f"written, {_count(result.existing, 'entry', 'entries')} already there, "
        f"{revoked_text} revoked. Every channel is blocked for this person from now on.",
    )


def erased_unchanged() -> Notice:
    return Notice(STATUS, ERASED_UNCHANGED)


def refused_notice(refusal: Refusal) -> Notice:
    """The ``role="alert"`` notice of a refused POST, with the form-level problem."""
    problem = refusal.problems.get(FORM_FIELD)
    return Notice(ALERT, f"{NOTHING_CHANGED} {problem}." if problem else NOTHING_CHANGED)


def _field(name: str, values: Mapping[str, str], errors: Mapping[str, str]) -> FormField:
    return FormField(name, LABELS[name], values.get(name, ""), HINTS.get(name), errors.get(name))


def _record_form(refusal: Refusal | None, zone_label: str) -> RecordForm:
    refused = refusal is not None and refusal.action == RECORD
    values = dict(refusal.values) if refusal is not None and refused else {}
    errors = dict(refusal.problems) if refusal is not None and refused else {}
    occurred = _field(OCCURRED_FIELD, values, errors)
    occurred = FormField(
        occurred.name,
        occurred.label,
        occurred.value,
        f"The date and time the prospect did it, your local time ({zone_label}); not in the "
        "future.",
        occurred.error,
    )
    return RecordForm(
        channel=_field(CHANNEL_FIELD, values, errors),
        channels=tuple(Option(str(channel), CHANNEL_LABELS[channel]) for channel in VALID_ACTIONS),
        action=_field(ACTION_FIELD, values, errors),
        groups=tuple(
            OptionGroup(
                CHANNEL_LABELS[channel],
                tuple(Option(str(action), ACTION_LABELS[action]) for action in actions),
            )
            for channel, actions in VALID_ACTIONS.items()
        ),
        occurred=occurred,
        evidence=_field(EVIDENCE_FIELD, values, errors),
        number=_field(NUMBER_FIELD, values, errors),
        handle=_field(HANDLE_FIELD, values, errors),
    )


def _label[K: str](labels: Mapping[K, str], code: str, kind: type[K]) -> str:
    try:
        return labels[kind(code)]
    except (KeyError, ValueError):
        return code


def _row(
    permission: StoredPermission,
    href: str,
    evidence: Mapping[str, str],
    view: PermissionsView,
    zone: tzinfo,
    refusal: Refusal | None,
) -> PermissionRow:
    active = permission.status == PermissionStatus.ACTIVE.value
    window_opener = permission.channel == PermissionChannel.INSTAGRAM.value
    if window_opener:
        identifier_label, identifier = LABELS[HANDLE_FIELD], permission.instagram_handle
    else:
        identifier_label, identifier = LABELS[NUMBER_FIELD], permission.whatsapp_number
    window = None
    window_closed = False
    inbound = None
    if active and window_opener and permission.last_inbound_at is not None:
        window = window_text(permission.last_inbound_at, view.now, zone)
        window_closed = not window_open(permission.last_inbound_at, view.now)
        refused = (
            refusal is not None
            and refusal.action == INBOUND
            and refusal.permission_id == permission.id
        )
        inbound = FormField(
            INBOUND_FIELD,
            LABELS[INBOUND_FIELD],
            refusal.values.get(INBOUND_FIELD, "") if refusal is not None and refused else "",
            "When the prospect last messaged on Instagram, your local time; now at the latest. "
            f"Stored: {local_text(permission.last_inbound_at, zone)}.",
            refusal.problems.get(INBOUND_FIELD) if refusal is not None and refused else None,
        )
    return PermissionRow(
        id=permission.id,
        channel=_label(CHANNEL_LABELS, permission.channel, PermissionChannel),
        action=_label(ACTION_LABELS, permission.prospect_action, ProspectAction),
        occurred=local_text(permission.occurred_at, zone),
        identifier_label=identifier_label,
        identifier=identifier or NO_VALUE,
        evidence=evidence.get(permission.evidence_id) or NO_VALUE,
        status=permission.status,
        revoked=None if permission.revoked_at is None else local_text(permission.revoked_at, zone),
        window=window,
        window_closed=window_closed,
        inbound=inbound,
        can_revoke=active,
        base_href=f"{href}/{permission.id}",
    )


def evidence_quotes(contact_id: str, rows: tuple[StoredEvidence, ...]) -> dict[str, str]:
    """Each of the Contact's quote Evidence rows, by id, to its quote."""
    return {
        row.id: row.quote
        for row in rows
        if row.person_contact_id == contact_id and row.quote is not None
    }


def panel_context(
    lead: StoredLead,
    contact: StoredContact,
    view: PermissionsView,
    evidence: Mapping[str, str],
    *,
    notice: Notice | None = None,
    refusal: Refusal | None = None,
) -> dict[str, object]:
    """What ``channel_permissions.html`` and ``channel_permissions_panel.html`` render; see the
    module docstring."""
    href = permissions_href(lead.id, contact.id)
    shown = notice if refusal is None else refused_notice(refusal)
    context: dict[str, object] = {
        "lead_id": lead.id,
        "lead_name": lead.name,
        "contact_name": contact.name if contact.erased_at is None else None,
        "notice": shown,
        "action": href,
    }
    if contact.erased_at is not None:
        context["erased_text"] = ERASED_TEXT
        return context
    zone: tzinfo = view.zone if view.zone is not None else UTC
    context.update(
        zone_unset=ZONE_UNSET if view.zone is None else None,
        not_qualifying=NOT_QUALIFYING,
        permissions=tuple(
            _row(permission, href, evidence, view, zone, refusal)
            for permission in view.permissions
        ),
        form=_record_form(refusal, str(zone)),
        max_evidence=MAX_EVIDENCE,
        max_handle=MAX_HANDLE_LENGTH,
    )
    return context

