"""Public command API of the ``sending`` service (AD-3, AD-4, AD-14, AD-21, FR-32).

Other services and the delivery layers change ``sending`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Sending(clock, *, env)`` owns the ``sending_domains`` and ``sending_inboxes`` tables and the
SendingInbox machine (``nextron.domain.sending_inbox``). ``env`` is the engine's environment:
outside ``prod`` only allowlisted test inboxes can be registered (AD-14).

- ``@use_case register_inbox(address, label) -> RegisteredInbox(inbox_id, sending_domain_id,
  domain, domain_created)``:

  1. ``check_address`` and ``check_label`` run before any transaction (a non-``str`` argument
     raises ``TypeError``).
  2. Then one transaction, in order:

     - outside ``prod``, the ``sink_allowlist`` check (``allowlist_refusal``); an allowlist
       whose stored text no longer parses counts as empty, so the inbox is refused;
     - the duplicate-address check, over every status (a ``removed`` inbox's address stays
       registered);
     - ``from_name`` is ``queries.setup.get(Key.SENDER_NAME)``; ``SettingNotSet`` or
       ``InvalidSettingValue`` is ``sender_name_unset``;
     - the ``sending_domains`` row of the address's registrable domain is found or inserted
       (``domain_created``);
     - the inbox is inserted at ``CREATION.to`` (``connecting``), ``created_at`` = now;
     - one ``events`` row: entity ``sending_inboxes``, ``None`` to ``connecting``, cause
       ``registered``, no ``cause_ref``, actor ``operator``.

  3. Every refusal raises ``InboxRefused`` (``.refusal``; its message is
     ``REFUSAL_MESSAGES[refusal]``) and rolls the whole transaction back: nothing is stored.
  4. After it, the use case logs ``sending_inbox_registered`` (``inbox_id``,
     ``sending_domain_id`` and ``domain_created``) or ``sending_inbox_refused`` (``refusal``),
     never the address or the label.

This story only registers inboxes: the Gmail connection, warm-up, caps, pause, resume and
remove commands arrive with Epic 6, and enrollment (Story 4.8) binds a Sequence to one of
``queries.sending.inboxes_for_binding()``.
"""

import logging
from dataclasses import dataclass
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.sending import domains as domains_repo
from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.uow import current, use_case
from nextron.domain.sending_inbox import (
    CREATION,
    InboxAddress,
    InboxRefused,
    Refusal,
    allowlist_refusal,
    check_address,
    check_label,
)
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.domain.sink import sink_applies
from nextron.ports.clock import Clock

ENTITY: Final = "sending_inboxes"
ACTOR: Final = "operator"
INBOX_REGISTERED: Final = "sending_inbox_registered"
INBOX_REFUSED: Final = "sending_inbox_refused"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegisteredInbox:
    """A registered inbox: its id, its Sending Domain's id and domain, and whether that Sending
    Domain was created for it."""

    inbox_id: str
    sending_domain_id: str
    domain: str
    domain_created: bool


class Sending:
    """Sending Inboxes and their Sending Domains; see the module docstring."""

    def __init__(self, clock: Clock, *, env: str) -> None:
        self._clock = clock
        self._env = env

    @use_case
    def register_inbox(self, address: str, label: str) -> RegisteredInbox:
        """Register a Sending Inbox at ``connecting``; see the module docstring."""
        try:
            checked = check_address(address)
            shown_label = check_label(label)
            with current().transaction():
                registered = self._register(checked, shown_label)
        except InboxRefused as refused:
            log.info(INBOX_REFUSED, extra={"refusal": str(refused.refusal)})
            raise
        log.info(
            INBOX_REGISTERED,
            extra={
                "inbox_id": registered.inbox_id,
                "sending_domain_id": registered.sending_domain_id,
                "domain_created": registered.domain_created,
            },
        )
        return registered

    def _register(self, checked: InboxAddress, label: str) -> RegisteredInbox:
        allowlist = self._allowlist() if sink_applies(self._env) else ()
        refusal = allowlist_refusal(checked.address, self._env, allowlist)
        if refusal is not None:
            raise InboxRefused(refusal)
        if inboxes_repo.id_by_address(checked.address) is not None:
            raise InboxRefused(Refusal.ALREADY_REGISTERED)
        try:
            from_name = str(setup_queries.get(Key.SENDER_NAME))
        except (SettingNotSet, InvalidSettingValue):
            raise InboxRefused(Refusal.SENDER_NAME_UNSET) from None
        now = self._clock.now()
        found = domains_repo.by_domain(checked.domain)
        if found is None:
            domain_id = domains_repo.insert(domain=checked.domain, created_at=now)
        else:
            domain_id = found.id
        inbox_id = inboxes_repo.insert(
            address=checked.address,
            label=label,
            sending_domain_id=domain_id,
            from_name=from_name,
            status=CREATION.to,
            created_at=now,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=inbox_id,
            from_status=None,
            to_status=CREATION.to,
            cause=CREATION.cause,
            cause_ref=None,
            actor=ACTOR,
            at=now,
        )
        return RegisteredInbox(inbox_id, domain_id, checked.domain, found is None)

    @staticmethod
    def _allowlist() -> tuple[object, ...]:
        """The ``sink_allowlist`` entries; a stored list that no longer parses counts as
        empty."""
        try:
            return tuple(setup_queries.get(Key.SINK_ALLOWLIST))
        except InvalidSettingValue:
            return ()
