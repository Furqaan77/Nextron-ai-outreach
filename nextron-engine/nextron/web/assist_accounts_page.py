"""The LinkedIn account panel of the Settings page (FR-49, PRD OQ-5, UX-DR23; Story 10.2).

``templates/settings.html`` includes ``templates/assist_accounts_panel.html``
(``id="assist-accounts-panel"``, heading "LinkedIn account") after the settings form, whose
LinkedIn section holds the ``linkedin_owner_chat`` setting. ``create_app`` builds its context
with ``panel_context`` from ``nextron.app.queries.pipeline.accounts()`` in the GET's read-only
unit of work. The routes are ``@personal_data(True)``: the label and owner's name may name a
Nextron staff member.

**The panel.** It says Tasks are sent by hand and go only to ``linkedin_owner_chat``, and that
v1 supports one active account. Each account, oldest first, has a save form prefilled with its
label, owner's name and role, status (``active`` or ``inactive``) and its three caps; below
them, an add form with the label, owner's name and role and the caps left blank (blank is the
default: 10 Tasks per weekday, 10 invitations a day, 100 in a rolling week). Every form posts
with ``hx-post``, ``hx-target="#assist-accounts-panel"`` and ``hx-swap="outerHTML"`` and
carries the CSRF field; there is no delete (Story 10.4's Tasks reference the account).

**The POSTs** (``nextron.web.app``): ``/settings/linkedin/add`` takes exactly ``label``,
``owner_name``, ``owner_role``, ``tasks_per_weekday``, ``daily_invite_cap`` and
``weekly_invite_cap``; ``/settings/linkedin/save`` takes those, ``account`` (a ULID) and
``status``. Each field once; anything else raises ``MalformedForm`` (400). Each runs its
``AssistAccounts`` use case in its own unit of work, then renders the panel alone in a
read-only one.

**Outcomes.** A ``role="status"`` notice, "Added <label>: it is inactive until you make it
active." or "Saved <label>.". An ``AccountRefused`` answers 200 with a ``role="alert"``
"Nothing was saved." followed by the form-level problem (such as "v1 supports one LinkedIn
account; its Tasks go to linkedin_owner_chat (PRD OQ-5)"), and the refused form re-rendered
with the submitted values, each refused field marked ``aria-invalid="true"`` with its message
beside it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from nextron.app.queries.pipeline import AssistAccount
from nextron.domain.ids import is_ulid
from nextron.domain.linkedin_caps import (
    CAP_FIELDS,
    DAILY_INVITES,
    FORM_FIELD,
    LABEL,
    OWNER_NAME,
    OWNER_ROLE,
    STATUS,
    TASKS_PER_WEEKDAY,
    TEXT_FIELDS,
    WEEKLY_INVITES,
    AccountStatus,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PANEL_TEMPLATE: Final = "assist_accounts_panel.html"
PANEL: Final = "assist_accounts_panel"  # the context name the template reads
ACCOUNT: Final = "account"
ADD: Final = "add"
SAVE: Final = "save"
ADD_FIELDS: Final = frozenset({*TEXT_FIELDS, *CAP_FIELDS})
SAVE_FIELDS: Final = frozenset({ACCOUNT, STATUS, *ADD_FIELDS})
NOTHING_SAVED: Final = "Nothing was saved."
STATUSES: Final = tuple(str(status) for status in AccountStatus)
LABELS: Final[Mapping[str, str]] = {
    LABEL: "Label",
    OWNER_NAME: "Owner's name",
    OWNER_ROLE: "Owner's role",
    STATUS: "Status",
    TASKS_PER_WEEKDAY: "Tasks per weekday",
    DAILY_INVITES: "Invitations a day",
    WEEKLY_INVITES: "Invitations in a rolling week",
}
HINTS: Final[Mapping[str, str]] = {
    LABEL: "A name for this account, such as Sales LinkedIn.",
    OWNER_NAME: "The person who sends the Tasks by hand; Drafts are written in their voice.",
    OWNER_ROLE: "Their role, such as Founder.",
    TASKS_PER_WEEKDAY: "Blank is 10.",
    DAILY_INVITES: "Blank is 10; at most the weekly cap.",
    WEEKLY_INVITES: "Blank is 100; at most 100.",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    """A use case that went through: ``add`` or ``save``, and the account's id."""

    action: str
    account_id: str


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused POST: which form (``add`` or ``save``), its account (``None`` for add), the
    problems by field (``form`` for the whole form) and the submitted values."""

    action: str
    account_id: str | None
    problems: Mapping[str, str]
    values: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class FormField:
    """One field of an account form."""

    name: str
    label: str
    value: str
    hint: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AccountForm:
    """An add or save form: its element-id ``prefix``, its account (``None`` for add), its text
    and cap fields, and its status (``None`` for add)."""

    prefix: str
    account_id: str | None
    title: str
    fields: tuple[FormField, ...]
    status: FormField | None = None
    statuses: tuple[str, ...] = field(default=STATUSES)


@dataclass(frozen=True, slots=True)
class Panel:
    """What ``assist_accounts_panel.html`` renders."""

    notice: str | None
    alert: str | None
    accounts: tuple[AccountForm, ...]
    add: AccountForm


# Submitted forms ---------------------------------------------------------------------------------


def _single(fields: FormFields, expected: frozenset[str]) -> dict[str, str]:
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


def submitted_add(fields: FormFields) -> dict[str, str]:
    """The add form's fields."""
    return _single(fields, ADD_FIELDS)


def submitted_save(fields: FormFields) -> dict[str, str]:
    """The save form's fields; ``account`` must be a ULID."""
    single = _single(fields, SAVE_FIELDS)
    if not is_ulid(single[ACCOUNT]):
        raise MalformedForm(WRONG_FIELDS)
    return single


def caps_of(submitted: Mapping[str, str]) -> dict[str, str]:
    """The submitted cap texts, by field."""
    return {name: submitted[name] for name in CAP_FIELDS}


# The panel ---------------------------------------------------------------------------------------


def _fields(values: Mapping[str, str], errors: Mapping[str, str]) -> tuple[FormField, ...]:
    return tuple(
        FormField(name, LABELS[name], values.get(name, ""), HINTS.get(name), errors.get(name))
        for name in (*TEXT_FIELDS, *CAP_FIELDS)
    )


def _stored_values(account: AssistAccount) -> dict[str, str]:
    return {
        LABEL: account.label,
        OWNER_NAME: account.owner_name,
        OWNER_ROLE: account.owner_role,
        STATUS: str(account.status),
        TASKS_PER_WEEKDAY: str(account.caps.tasks_per_weekday),
        DAILY_INVITES: str(account.caps.daily_invite_cap),
        WEEKLY_INVITES: str(account.caps.weekly_invite_cap),
    }


def _account_form(account: AssistAccount, refusal: Refusal | None) -> AccountForm:
    prefix = f"assist-account-{account.id}"
    refused = refusal is not None and refusal.action == SAVE and refusal.account_id == account.id
    values = dict(refusal.values) if refusal is not None and refused else _stored_values(account)
    errors = dict(refusal.problems) if refusal is not None and refused else {}
    status = FormField(STATUS, LABELS[STATUS], values.get(STATUS, ""), None, errors.get(STATUS))
    return AccountForm(
        prefix, account.id, account.label, _fields(values, errors), status=status
    )


def _add_form(refusal: Refusal | None) -> AccountForm:
    prefix = "assist-account-new"
    if refusal is not None and refusal.action == ADD:
        return AccountForm(
            prefix, None, "Add an account", _fields(refusal.values, refusal.problems)
        )
    return AccountForm(prefix, None, "Add an account", _fields({}, {}))


def outcome_notice(outcome: Outcome, label: str) -> str:
    """The ``role="status"`` notice of a use case that went through."""
    if outcome.action == ADD:
        return f"Added {label}: it is inactive until you make it active."
    return f"Saved {label}."


def refused_notice(refusal: Refusal) -> str:
    """The ``role="alert"`` notice of a refused POST, with the form-level problem."""
    problem = refusal.problems.get(FORM_FIELD)
    return f"{NOTHING_SAVED} {problem}." if problem else NOTHING_SAVED


def panel_context(
    accounts: Sequence[AssistAccount],
    *,
    outcome: Outcome | None = None,
    refusal: Refusal | None = None,
) -> dict[str, object]:
    """The context ``assist_accounts_panel.html`` reads (``assist_accounts_panel``)."""
    labels = {account.id: account.label for account in accounts}
    notice = None
    if outcome is not None:
        notice = outcome_notice(outcome, labels.get(outcome.account_id, "the account"))
    panel = Panel(
        notice=notice,
        alert=None if refusal is None else refused_notice(refusal),
        accounts=tuple(_account_form(account, refusal) for account in accounts),
        add=_add_form(refusal),
    )
    return {PANEL: panel}
