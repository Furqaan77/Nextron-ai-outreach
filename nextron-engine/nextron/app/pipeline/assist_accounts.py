"""The Settings page's LinkedIn account commands (FR-49, PRD OQ-5, AD-3, AD-4; Story 10.2).

``AssistAccounts(clock)`` owns ``linkedin_accounts`` (``nextron.db.repos.pipeline.
assist_accounts``): the account a person sends LinkedIn Tasks from by hand, its owner's voice
and its caps. It sends, fetches and schedules nothing. Each method is the one ``@use_case`` a
web handler calls: it checks the fields (``nextron.domain.linkedin_caps.check_fields``) before
anything is read, then opens one transaction that checks the stored rows, writes the row and
appends one ``events`` row (entity ``linkedin_accounts``, actor ``operator``, at the clock's
now; IDs and codes only, so no personal data). A refusal raises ``AccountRefused`` with each
refused field's message (``form`` for the whole form) and stores nothing; the table's CHECKs
and unique indexes repeat the rules.

- ``add_account(label, owner_name, owner_role, caps) -> id``: stores a new ``inactive`` account;
  ``caps`` maps ``tasks_per_weekday``, ``daily_invite_cap`` and ``weekly_invite_cap`` to the typed
  text (blank or missing is the default: 10, 10 and 100). Event ``None`` to ``inactive``, cause
  ``account_added``. A label another account has is refused (``DUPLICATE_LABEL``).
- ``save_account(account_id, label, owner_name, owner_role, status, caps)``: rewrites the
  account. ``status`` must be ``active`` or ``inactive`` (``BAD_STATUS``); an unknown account
  is refused (``UNKNOWN_ACCOUNT``, on ``form``), and so is making it ``active`` while another
  account is (``ONE_ACCOUNT``, on ``form``: v1 supports one account). Event from the stored
  status to the new one, cause ``account_saved``.
"""

from collections.abc import Mapping
from typing import Final

from nextron.db import eventlog
from nextron.db.repos.pipeline import assist_accounts as accounts_repo
from nextron.db.uow import current, use_case
from nextron.domain.ids import is_ulid
from nextron.domain.linkedin_caps import (
    BAD_STATUS,
    DUPLICATE_LABEL,
    FORM_FIELD,
    LABEL,
    ONE_ACCOUNT,
    STATUS,
    UNKNOWN_ACCOUNT,
    AccountFields,
    AccountRefused,
    AccountStatus,
    check_fields,
)
from nextron.ports.clock import Clock

ENTITY: Final = "linkedin_accounts"
ACTOR: Final = "operator"
ADDED: Final = "account_added"
SAVED: Final = "account_saved"


def _checked(
    label: object,
    owner_name: object,
    owner_role: object,
    caps: Mapping[str, object],
    status: object = AccountStatus.INACTIVE,
) -> tuple[AccountFields, AccountStatus]:
    """The checked fields and status; ``AccountRefused`` with every problem."""
    problems: dict[str, str] = {}
    try:
        fields = check_fields(label, owner_name, owner_role, caps)
    except AccountRefused as refused:
        problems.update(refused.problems)
        fields = None
    chosen = status if isinstance(status, str) and status in set(AccountStatus) else None
    if chosen is None:
        problems[STATUS] = BAD_STATUS
    if problems or fields is None or chosen is None:
        raise AccountRefused(problems)
    return fields, AccountStatus(chosen)


class AssistAccounts:
    """The LinkedIn account commands; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def add_account(
        self, label: str, owner_name: str, owner_role: str, caps: Mapping[str, str]
    ) -> str:
        """Store a new ``inactive`` account; return its id."""
        fields, status = _checked(label, owner_name, owner_role, caps)
        now = self._clock.now()
        with current().transaction():
            if accounts_repo.id_by_label(fields.label) is not None:
                raise AccountRefused({LABEL: DUPLICATE_LABEL})
            account_id = accounts_repo.insert(
                label=fields.label,
                owner_name=fields.owner_name,
                owner_role=fields.owner_role,
                status=str(status),
                tasks_per_weekday=fields.caps.tasks_per_weekday,
                daily_invite_cap=fields.caps.daily_invite_cap,
                weekly_invite_cap=fields.caps.weekly_invite_cap,
                at=now,
            )
            eventlog.append(
                entity=ENTITY,
                entity_id=account_id,
                from_status=None,
                to_status=str(status),
                cause=ADDED,
                cause_ref=None,
                actor=ACTOR,
                at=now,
            )
        return account_id

    @use_case
    def save_account(
        self,
        account_id: str,
        label: str,
        owner_name: str,
        owner_role: str,
        status: str,
        caps: Mapping[str, str],
    ) -> None:
        """Rewrite the account ``account_id``; see the module docstring."""
        fields, chosen = _checked(label, owner_name, owner_role, caps, status)
        now = self._clock.now()
        with current().transaction():
            stored = accounts_repo.get(account_id) if is_ulid(account_id) else None
            if stored is None:
                raise AccountRefused({FORM_FIELD: UNKNOWN_ACCOUNT})
            holder = accounts_repo.id_by_label(fields.label)
            if holder is not None and holder != stored.id:
                raise AccountRefused({LABEL: DUPLICATE_LABEL})
            if chosen is AccountStatus.ACTIVE and any(
                other != stored.id for other in accounts_repo.active_ids()
            ):
                raise AccountRefused({FORM_FIELD: ONE_ACCOUNT})
            accounts_repo.update(
                stored.id,
                label=fields.label,
                owner_name=fields.owner_name,
                owner_role=fields.owner_role,
                status=str(chosen),
                tasks_per_weekday=fields.caps.tasks_per_weekday,
                daily_invite_cap=fields.caps.daily_invite_cap,
                weekly_invite_cap=fields.caps.weekly_invite_cap,
                at=now,
            )
            eventlog.append(
                entity=ENTITY,
                entity_id=stored.id,
                from_status=stored.status,
                to_status=str(chosen),
                cause=SAVED,
                cause_ref=None,
                actor=ACTOR,
                at=now,
            )
