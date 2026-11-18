"""Set up LinkedIn Tasks (Story 10.2) on a test database, so ``queries.pipeline.health_issues``
raises no ``linkedin_tasks_off`` issue in tests that assert the health panel's other issues.

``assist_ready(units)`` adds one active account and sets ``linkedin_owner_chat``, each in a unit
of work of its own."""

from nextron.app.pipeline.assist_accounts import AssistAccounts
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory
from tests.fakes.clock import FrozenClock

OWNER_CHAT = "123456789"
BLANK_CAPS = {"tasks_per_weekday": "", "daily_invite_cap": "", "weekly_invite_cap": ""}


def assist_ready(units: UnitOfWorkFactory) -> str:
    """Add an active account and set the owner's chat; return the account's id."""
    accounts = AssistAccounts(FrozenClock())
    with units.open():
        account_id = accounts.add_account("Main", "Furqaan Ahmed", "Founder", BLANK_CAPS)
    with units.open():
        accounts.save_account(account_id, "Main", "Furqaan Ahmed", "Founder", "active", BLANK_CAPS)
    with units.open():
        Setup(FrozenClock()).save({"linkedin_owner_chat": OWNER_CHAT})
    return account_id
