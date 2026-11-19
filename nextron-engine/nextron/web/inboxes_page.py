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

**Warm-up and Daily Caps (Story 6.4, FR-33, FR-34, UX-DR26).** After the table, the panel has
one ``<section class="inbox-warmup">`` per listed inbox, headed "Warm-up: <label>", with its
facts (``WARMUP_FACTS``): Recorded warm-up ("N days" or "Not recorded"), Today's cap (the cold
cap from ``Sending.inbox_caps()``, plus "(inbox not active)" for an inbox that isn't), Cap used
("0 of N": nothing is counted until Story 6.10) and Ceiling ("N a day, M of them warm-up tool
sends"); then its warm-up form (``method="post"``, ``hx-post="/inboxes/warmup"``, the CSRF
field, a hidden ``inbox_id`` and the six fields ``WARMUP_FIELDS``, prefilled from storage:
blank start date, method and warm level while unrecorded, and the stored ramp).

``POST /inboxes/warmup`` (``inbox_warmup``, ``@personal_data(True)``: it answers with the whole
panel) takes exactly ``inbox_id`` (a ULID) and the six fields, each once (``csrf_token`` aside;
``submitted_warmup``); anything else raises ``MalformedForm`` (400). It runs
``Sending.record_warmup`` in its own unit of work, then renders ``inboxes_panel.html`` alone in
a read-only one: ``Warm-up saved.`` (``role="status"``) with every form prefilled from storage,
or the ``WarmupRefused`` message (``role="alert"``, 200) with that inbox's form keeping the
typed values.

**Sending Domain authentication (Story 6.5).** ``inboxes.html`` includes
``inbox_auth_panel.html`` after the panel: ``<section id="domain-auth">``, headed "Sending Domain
authentication", which HTMX re-fetches every 30 seconds from ``GET /inboxes/auth``
(``inbox_auth_panel``) so a Recheck's result appears without resetting the registration form.
``auth_context`` renders it from ``DomainChecks.auths()`` (``queries.sending.domain_auths`` at
the clock's now): one row per listed Sending Domain, by domain, with the Sending Domain, Last
checked (``YYYY-MM-DD HH:MM UTC`` or "Not checked yet"), SPF, DKIM, DMARC and Alignment (each
``Pass``, ``Fail: <reason text>`` or ``Not checked yet``), DNSBL and Spam rate (Story 6.6:
``reputation.dnsbl_text``, such as "Clean", "Listed (dbl.spamhaus.org)" or "Query refused", and
``reputation.spam_rate_text``, such as "0.12%" or "No data"; "Not checked yet" for no check or
one recorded before Story 6.6; a cell whose result paused the inboxes has
``class="auth-blocked"``), Cold sends (the verdict text; when blocked the cell has
``class="auth-blocked"``) and a Recheck form. With no listed domain it
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

from nextron.app.queries.sending import DomainAuthView, InboxCapView, SendingInboxView
from nextron.domain.domain_auth import REASON_TEXTS, AuthItem, AuthReason
from nextron.domain.ids import is_ulid
from nextron.domain.reputation import SpamStatus, ZoneOutcome, dnsbl_text, spam_rate_text
from nextron.domain.sending_inbox import (
    CONNECTING_NOTE,
    InboxRefused,
    InboxStatus,
    WarmupRefused,
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
# The warm-up form (Story 6.4), in form order after the hidden inbox_id.
INBOX_ID_FIELD: Final = "inbox_id"
STARTED_ON_FIELD: Final = "warmup_started_on"
METHOD_FIELD: Final = "warmup_method"
WARM_LEVEL_FIELD: Final = "warm_level"
TOOL_DAILY_FIELD: Final = "warmup_tool_daily"
RAMP_STEP_FIELD: Final = "ramp_step"
RAMP_TARGET_FIELD: Final = "ramp_target"
WARMUP_FIELDS: Final = (
    STARTED_ON_FIELD,
    METHOD_FIELD,
    WARM_LEVEL_FIELD,
    TOOL_DAILY_FIELD,
    RAMP_STEP_FIELD,
    RAMP_TARGET_FIELD,
)
WARMUP_FORM_FIELDS: Final = frozenset({INBOX_ID_FIELD, *WARMUP_FIELDS})
WARMUP_SAVED: Final = "Warm-up saved."
NOT_RECORDED: Final = "Not recorded"
NOT_ACTIVE: Final = " (inbox not active)"
WARMUP_FACTS: Final = ("Recorded warm-up", "Today's cap", "Cap used", "Ceiling")


@dataclass(frozen=True, slots=True)
class WarmupForm:
    """The warm-up form's values for one inbox, as text."""

    inbox_id: str
    warmup_started_on: str
    warmup_method: str
    warm_level: str
    warmup_tool_daily: str
    ramp_step: str
    ramp_target: str


@dataclass(frozen=True, slots=True)
class InboxRow:
    """One inbox as the panel shows it: its table cells, warm-up facts and warm-up form."""

    label: str
    address: str
    domain: str
    status: str
    note: str | None  # CONNECTING_NOTE for a connecting inbox
    id: str
    facts: tuple[tuple[str, str], ...]
    warmup: WarmupForm


def _text(value: object) -> str:
    return "" if value is None else str(value)


def stored_warmup(inbox: SendingInboxView) -> WarmupForm:
    """The warm-up form prefilled from ``inbox``'s stored values."""
    started = inbox.warmup_started_on
    return WarmupForm(
        inbox_id=inbox.id,
        warmup_started_on="" if started is None else started.isoformat(),
        warmup_method=_text(inbox.warmup_method),
        warm_level=_text(inbox.warm_level),
        warmup_tool_daily=str(inbox.warmup_tool_daily),
        ramp_step=str(inbox.ramp_step),
        ramp_target=str(inbox.ramp_target),
    )


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def warmup_facts(inbox: SendingInboxView, cap: InboxCapView) -> tuple[tuple[str, str], ...]:
    """The panel's warm-up facts for ``inbox``, in ``WARMUP_FACTS`` order."""
    recorded = NOT_RECORDED if cap.warmup_days is None else _days(cap.warmup_days)
    today = str(cap.cold_cap)
    if inbox.status is not InboxStatus.ACTIVE:
        today += NOT_ACTIVE
    values = (
        recorded,
        today,
        f"{cap.cap_used} of {cap.cold_cap}",
        f"{cap.ceiling} a day, {cap.tool_sends} of them warm-up tool sends",
    )
    return tuple(zip(WARMUP_FACTS, values, strict=True))


def inbox_row(
    inbox: SendingInboxView,
    cap: InboxCapView | None = None,
    typed: WarmupForm | None = None,
) -> InboxRow:
    """``inbox`` as a row, with its warm-up facts (none without ``cap``) and form (``typed``
    when it is this inbox's, else the stored values)."""
    note = CONNECTING_NOTE if inbox.status is InboxStatus.CONNECTING else None
    warmup = typed if typed is not None and typed.inbox_id == inbox.id else stored_warmup(inbox)
    facts = () if cap is None else warmup_facts(inbox, cap)
    return InboxRow(
        inbox.label, inbox.address, inbox.domain, str(inbox.status), note, inbox.id, facts, warmup
    )


def registered() -> Notice:
    return Notice(Role.STATUS, REGISTERED)


def refused(refusal: InboxRefused | WarmupRefused) -> Notice:
    return Notice(Role.ALERT, refusal.message)


def warmup_saved() -> Notice:
    return Notice(Role.STATUS, WARMUP_SAVED)


def panel_context(
    inboxes: Iterable[SendingInboxView],
    *,
    caps: Iterable[InboxCapView] = (),
    notice: Notice | None = None,
    address: str = "",
    label: str = "",
    typed_warmup: WarmupForm | None = None,
) -> dict[str, object]:
    """What ``inboxes_panel.html`` renders: the notice, the crowding warnings, the form's values
    and the rows, each with its warm-up facts (from ``caps``) and form (``typed_warmup`` for
    the inbox whose save was refused)."""
    listed = list(inboxes)
    by_inbox = {cap.inbox_id: cap for cap in caps}
    crowded = crowded_domains(inbox.domain for inbox in listed)
    return {
        "notice": notice,
        "warnings": [crowding_warning(domain) for domain in crowded],
        "address": address,
        "label": label,
        "rows": [inbox_row(inbox, by_inbox.get(inbox.id), typed_warmup) for inbox in listed],
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


def submitted_warmup(fields: FormFields) -> WarmupForm:
    """The ``inbox_id`` (a ULID) and the six warm-up fields the form posts, each once;
    ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != WARMUP_FORM_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    if any(len(fields[name]) != 1 for name in WARMUP_FORM_FIELDS):
        raise MalformedForm(REPEATED_FIELD)
    inbox_id = fields[INBOX_ID_FIELD][0]
    if not is_ulid(inbox_id):
        raise MalformedForm(WRONG_FIELDS)
    return WarmupForm(inbox_id, *(fields[name][0] for name in WARMUP_FIELDS))


# Sending Domain authentication (Story 6.5) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthRow:
    """One listed Sending Domain as the authentication table shows it; ``items`` are the SPF,
    DKIM, DMARC and Alignment cells, in that order; ``dnsbl`` and ``spam_rate`` the DNSBL and
    Spam rate cells (Story 6.6), ``dnsbl_paused`` and ``spam_paused`` whether each pauses."""

    sending_domain_id: str
    domain: str
    last_checked: str
    items: tuple[str, ...]
    cold_sends: str
    blocked: bool
    dnsbl: str = NOT_CHECKED
    spam_rate: str = NOT_CHECKED
    dnsbl_paused: bool = False
    spam_paused: bool = False


def _item_cell(reason: AuthReason | None) -> str:
    if reason is None:
        return NOT_CHECKED
    if reason is AuthReason.OK:
        return PASS
    return f"{FAIL_PREFIX}{REASON_TEXTS[reason]}"


def auth_row(view: DomainAuthView) -> AuthRow:
    """``view`` as a table row."""
    latest = view.latest
    dnsbl = spam = NOT_CHECKED
    dnsbl_paused = spam_paused = False
    if latest is None:
        last_checked = NOT_CHECKED
        items = tuple(_item_cell(None) for _ in AuthItem)
    else:
        last_checked = f"{latest.checked_at.astimezone(UTC):{LAST_CHECKED_FORMAT}}"
        items = tuple(_item_cell(latest.reasons.get(item)) for item in AuthItem)
        if latest.dnsbl is not None:  # recorded since Story 6.6
            dnsbl = dnsbl_text(latest.dnsbl)
            dnsbl_paused = latest.dnsbl_outcome is ZoneOutcome.LISTED
        if latest.spam_status is not None:
            spam = spam_rate_text(latest.spam_rate)
            spam_paused = latest.spam_status is SpamStatus.PAUSE
    return AuthRow(
        view.sending_domain_id,
        view.domain,
        last_checked,
        items,
        view.verdict.text,
        view.verdict.blocked,
        dnsbl,
        spam,
        dnsbl_paused,
        spam_paused,
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
