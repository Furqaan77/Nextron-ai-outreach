"""The stores an erasure reaches outside the database (FR-52, AD-10, AD-15, AD-17, NFR-3,
NFR-17, Story 3.14), part of the ``compliance`` service.

An erasure (``nextron.app.compliance.erasure``) writes a receipt whose external stores start
``pending`` and enqueues one ``erase_external`` job per store (``EXTERNAL_STORES``). Each job
calls ``ErasureStores(clock, notify, sink).run(contact_id, store, last_attempt=...)``, which
runs that store's handler and settles its row.

**``@use_case run(contact_id, store, *, last_attempt) -> StoreStatus | None``**:

1. **Prepare**, in one transaction: if the Contact's receipts hold no ``pending`` row of
   ``store`` it returns ``None`` (idempotent: an earlier run, or a job of a later receipt,
   settled it). Otherwise it reads the Contact (unknown: ``LookupError``) and the handler's
   ``prepare(contact, now)``.
2. **Act**, outside any transaction (AD-3): ``act(plan) -> StoreResult``.
3. **Settle**, in a last transaction: every ``pending`` row of ``store`` on the Contact's
   receipts gets the result's status, detail and time (``transition(pending, status)``), with
   one ``events`` row per settled row: entity ``erasure_receipt_stores``, ``pending`` to the
   status, cause ``settled``, ``cause_ref`` the receipt, actor ``system:compliance``.

**Errors.** Before the last attempt, a retryable ``AdapterError`` from steps 1 or 2 propagates,
and any exception that isn't an ``AdapterError`` (a locked database, say), from any step, the
settle transaction included, is raised as the retryable ``AdapterError`` ``erasure_store_retry``
(``STORE_RETRY``, its message holding no value, from the error), so the job retries and the rows
stay ``pending``. A non-retryable ``AdapterError`` from steps 1 or 2, and on the last attempt
(``last_attempt``) any error from them, settles the rows ``failed`` with "Failed: <code>"
(``jobqueue.error_code`` of the original error) and, after that commits, logs
``erasure_store_failed`` (WARNING: ``contact_id``, ``store``, ``code``). The health panel then
shows it (``queries.compliance.health_issues``). An error in the last attempt's settle
transaction propagates (the job dies, its rows ``pending``). A ``contact_id`` that isn't a
ULID, or a ``store`` that isn't one of ``EXTERNAL_STORES``, raises ``ValueError`` before
anything is read.

**``handlers``** is a read-only ``{store: handler}`` for exactly ``EXTERNAL_STORES``; each
handler has ``prepare(contact, now)``, called in the prepare transaction (reads only), and
``act(plan) -> StoreResult``, called outside it:

- ``mailboxes``: ``MAILBOX_PLACEHOLDER``, ``none_held`` "The Contact has no Campaign Threads".
  Story 6.7 replaces it with the handler that moves the Contact's Threads to Trash through
  ``MailboxPort`` (``tests/contracts/test_erasure_contracts.py`` fails once a ``threads`` table
  exists while it is still the placeholder).
- ``crawled_pages``: research's hook nulled the text of the pages of the Contact's Lead and of
  every Lead merged into it (content hash kept). It counts those Leads' pages whose text is
  NULL and those that hold text again (crawled since, or merged in later): ``done`` when every
  page's text is NULL, ``residual`` with both counts when one holds text, ``none_held`` with no
  page.
- ``telegram``: the Telegram alerts about the Contact (``queries.notify.alerts_about``, subject
  the Contact's ULID). A ``sent`` alert with a message id whose ``updated_at`` (its send time)
  is under 48 hours old is deleted through ``notify.delete_message`` (the chat id is the
  ``alert_chat_id`` setting, read in prepare: an unusable one settles ``failed``). Any other
  ``sent`` alert counts as "older than 48 hours"; an ``unknown`` or ``pending`` one as "may have
  been delivered without an id"; a ``failed`` one never reached Telegram and is ignored.
  ``none_held`` with no alerts, ``done`` when every one was deleted, ``residual`` otherwise.
- ``backups``: always ``residual``. The verified backups started from the Contact's
  ``created_at`` to its ``erased_at`` that still have a local copy or an off-PC copy
  (``queries.ops.copies_holding``) are counted: each local copy ages out at its ``started_at``
  plus the ``backup_rotation_days`` setting (35 when it can't be read), each off-PC copy at its
  ``started_at`` plus ``OFFPC_ROTATION_DAYS``. The detail gives the counts and the first and
  last age-out dates (or that no recorded copy holds the Contact), and the bound every copy
  taken before the erasure ages out by (``erased_at`` plus 35 days), which also covers a backup
  still running or one not yet recorded.
- ``sink_files``: ``sink.erase_recipient(<the Contact's address hash>)`` (``SinkFilesPort``);
  ``done`` with the messages and files changed, or ``none_held`` (no match, no address, or no
  sink: ``nextron.main`` passes ``None`` in ``prod``).
- ``ai_test_sets`` (the merge of Stories 3.14 and 4.6): the Contact's Reply override cases
  (``queries.playbook.override_case_counts``), which playbook's ``ContactErased`` hook erased
  and retired in the erasure's transaction: ``none_held`` with none, ``done`` with the count
  when every one is erased, ``residual`` otherwise.
- ``anthropic``, ``worker_kv`` and ``nextronai_inbox``: ``residual`` with the AD-17 text; ``logs``, ``restore_work`` and ``erasure_ledger``: ``none_held`` with their fixed
  text (``nextron.domain.erasure_receipt.FIXED_RESULTS``).

Nothing logged or written to ``events`` holds a name, an address, a chat id or a message id.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, Protocol

from nextron.app.jobqueue.api import error_code
from nextron.app.notify.api import Notify
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.research import StoredContact
from nextron.db import eventlog
from nextron.db.repos.compliance import erasure_receipts as receipts_repo
from nextron.db.uow import current, use_case
from nextron.domain import erasure_receipt as receipt
from nextron.domain.alert import AlertStatus
from nextron.domain.backup import DEFAULT_ROTATION_DAYS, OFFPC_ROTATION_DAYS, OffpcStatus
from nextron.domain.erasure import tombstone_hash
from nextron.domain.erasure_receipt import (
    EXTERNAL_STORES,
    ErasureStore,
    StoreResult,
    StoreStatus,
    age_out,
    is_recent_message,
    transition,
)
from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.sink import SinkFilesPort

log = logging.getLogger(__name__)

ENTITY: Final = "erasure_receipt_stores"
ACTOR: Final = "system:compliance"
ERASURE_STORE_FAILED: Final = "erasure_store_failed"
STORE_RETRY: Final = "erasure_store_retry"
RETRY_MESSAGE: Final = "an erasure store's job failed and will be retried"


class StoreHandler(Protocol):
    """One external store's handler: ``prepare`` reads in a transaction, ``act`` runs outside
    one."""

    def prepare(self, contact: StoredContact, now: datetime) -> object: ...

    def act(self, plan: object) -> StoreResult: ...


class _Fixed:
    """A store whose result is known without reading or calling anything."""

    def __init__(self, result: StoreResult) -> None:
        self._result = result

    def prepare(self, contact: StoredContact, now: datetime) -> object:
        return self._result

    def act(self, plan: object) -> StoreResult:
        return self._result


class _Ready:
    """A store whose result ``prepare`` works out from the database alone."""

    def act(self, plan: object) -> StoreResult:
        if not isinstance(plan, StoreResult):
            raise TypeError("the plan must be a StoreResult")
        return plan


# Story 6.7 replaces it; the contract test fails once a threads table exists.
MAILBOX_PLACEHOLDER: Final = _Fixed(receipt.mailboxes_none())


def _lead_ids(contact: StoredContact) -> tuple[str, ...]:
    """The Contact's Lead and every Lead merged into it."""
    return (contact.lead_id, *discovery_queries.merged_lead_ids(contact.lead_id))


class _CrawledPages(_Ready):
    def prepare(self, contact: StoredContact, now: datetime) -> object:
        nulled = kept = 0
        for lead_id in _lead_ids(contact):
            for page in research_queries.crawled_pages(lead_id):
                if page.text is None:
                    nulled += 1
                else:
                    kept += 1  # crawled since the erasure, or merged in later
        return receipt.crawled_pages(nulled, kept)


@dataclass(frozen=True, slots=True)
class _TelegramPlan:
    """The messages to delete, and the alerts that remain whatever happens."""

    chat_id: str | None
    message_ids: tuple[str, ...]
    older: int
    unconfirmed: int


class _Telegram:
    def __init__(self, notify: Notify) -> None:
        self._notify = notify

    def prepare(self, contact: StoredContact, now: datetime) -> object:
        recent: list[str] = []
        older = unconfirmed = 0
        for alert in notify_queries.alerts_about(contact.id):
            status = AlertStatus(alert.status)
            if status is AlertStatus.FAILED:
                continue  # it never reached Telegram
            if status is not AlertStatus.SENT:
                unconfirmed += 1  # pending or unknown: may have been delivered without an id
            elif alert.provider_message_id is not None and is_recent_message(
                alert.updated_at, now
            ):
                recent.append(alert.provider_message_id)
            else:
                older += 1
        chat_id = str(setup_queries.get(Key.ALERT_CHAT_ID)) if recent else None
        return _TelegramPlan(chat_id, tuple(recent), older, unconfirmed)

    def act(self, plan: object) -> StoreResult:
        if not isinstance(plan, _TelegramPlan):
            raise TypeError("the plan must be a Telegram plan")
        if plan.message_ids and plan.chat_id is None:
            raise TypeError("a Telegram plan with messages to delete needs the chat id")
        deleted = refused = 0
        for message_id in plan.message_ids:
            if self._notify.delete_message(str(plan.chat_id), message_id):
                deleted += 1
            else:
                refused += 1
        return receipt.telegram(
            deleted=deleted, refused=refused, older=plan.older, unconfirmed=plan.unconfirmed
        )


def _rotation_days() -> int:
    """The ``backup_rotation_days`` setting, or 35 when it can't be read."""
    try:
        days = setup_queries.get(Key.BACKUP_ROTATION_DAYS)
    except DomainError:  # not set or not valid: the longest rotation
        return DEFAULT_ROTATION_DAYS
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        return DEFAULT_ROTATION_DAYS
    return days


class _Backups(_Ready):
    def prepare(self, contact: StoredContact, now: datetime) -> object:
        until = contact.erased_at if contact.erased_at is not None else now
        rotation = _rotation_days()
        ages: list[date] = []
        local = offpc = 0
        for row in ops_queries.copies_holding(contact.created_at, until):
            if row.local_deleted_at is None:
                local += 1
                ages.append(age_out(row.started_at, rotation))
            if row.offpc_status == OffpcStatus.COPIED and row.offpc_deleted_at is None:
                offpc += 1
                ages.append(age_out(row.started_at, OFFPC_ROTATION_DAYS))
        if not ages:
            return receipt.backups(local=0, offpc=0, first=None, last=None, erased_at=until)
        return receipt.backups(
            local=local, offpc=offpc, first=min(ages), last=max(ages), erased_at=until
        )


class _AiTestSets(_Ready):
    def prepare(self, contact: StoredContact, now: datetime) -> object:
        cases, erased = playbook_queries.override_case_counts(contact.id)
        return receipt.ai_test_sets(cases=cases, erased=erased)


@dataclass(frozen=True, slots=True)
class _SinkPlan:
    address_hash: str


class _SinkFiles:
    def __init__(self, sink: SinkFilesPort | None) -> None:
        self._sink = sink

    def prepare(self, contact: StoredContact, now: datetime) -> object:
        if self._sink is None:
            return receipt.sink_in_prod()
        if contact.email is None:
            return receipt.sink_no_address()
        if contact.erased_at is not None:
            return _SinkPlan(contact.email)  # an erased Contact's email is its address hash
        return _SinkPlan(tombstone_hash("address", contact.email))

    def act(self, plan: object) -> StoreResult:
        if isinstance(plan, StoreResult):
            return plan
        if not isinstance(plan, _SinkPlan) or self._sink is None:
            raise TypeError("the plan must be a sink plan")
        erased = self._sink.erase_recipient(plan.address_hash)
        return receipt.sink_files(files=erased.files, lines=erased.lines)


def _store(store: object) -> ErasureStore:
    if isinstance(store, ErasureStore):
        found = store
    elif isinstance(store, str):
        try:
            found = ErasureStore(store)
        except ValueError:
            raise ValueError("store must be an external erasure store") from None
    else:
        raise TypeError(f"store must be an ErasureStore or a str, not {type(store).__name__}")
    if found not in EXTERNAL_STORES:
        raise ValueError("store must be an external erasure store")
    return found


class ErasureStores:
    """The external stores' handlers and the ``erase_external`` use case; see the module
    docstring."""

    def __init__(self, clock: Clock, notify: Notify, sink: SinkFilesPort | None) -> None:
        self._clock = clock
        built: dict[ErasureStore, StoreHandler] = {
            ErasureStore.MAILBOXES: MAILBOX_PLACEHOLDER,
            ErasureStore.CRAWLED_PAGES: _CrawledPages(),
            ErasureStore.TELEGRAM: _Telegram(notify),
            ErasureStore.BACKUPS: _Backups(),
            ErasureStore.AI_TEST_SETS: _AiTestSets(),
            ErasureStore.SINK_FILES: _SinkFiles(sink),
        }
        for store, result in receipt.FIXED_RESULTS.items():
            built[store] = _Fixed(result)
        self._handlers: Mapping[ErasureStore, StoreHandler] = MappingProxyType(
            {store: built[store] for store in EXTERNAL_STORES}
        )

    @property
    def handlers(self) -> Mapping[ErasureStore, StoreHandler]:
        return self._handlers

    @use_case
    def run(
        self, contact_id: str, store: ErasureStore | str, *, last_attempt: bool
    ) -> StoreStatus | None:
        """Run the store's handler for the Contact and settle its rows; see the module
        docstring."""
        found = _store(store)
        if not (isinstance(contact_id, str) and is_ulid(contact_id)):
            raise ValueError("run: contact_id must be a ULID")
        handler = self._handlers[found]
        uow = current()
        error: BaseException | None = None
        result: StoreResult | None = None
        try:
            with uow.transaction():
                if receipts_repo.pending_count(contact_id, str(found)) == 0:
                    return None
                contact = research_queries.contact(contact_id)
                if contact is None:
                    raise LookupError("run: the Contact is unknown")
                plan = handler.prepare(contact, self._clock.now())
            result = handler.act(plan)
        except AdapterError as exc:
            if exc.retryable and not last_attempt:
                raise  # the job retries; the rows stay pending
            error = exc
        except Exception as exc:  # noqa: BLE001 - retried, then settles the store failed
            if not last_attempt:
                raise AdapterError(True, STORE_RETRY, RETRY_MESSAGE) from exc
            error = exc
        code = None if error is None else error_code(error)
        if code is not None or result is None:
            result = receipt.failed(code or "no_result")
        try:
            with uow.transaction():
                self._settle(contact_id, found, result)
        except AdapterError:
            raise
        except Exception as exc:  # a locked database, say: the job retries, rows still pending
            if not last_attempt:
                raise AdapterError(True, STORE_RETRY, RETRY_MESSAGE) from exc
            raise
        if code is not None:
            log.warning(
                ERASURE_STORE_FAILED,
                extra={"contact_id": contact_id, "store": str(found), "code": code},
            )
        return result.status

    def _settle(self, contact_id: str, store: ErasureStore, result: StoreResult) -> None:
        """Settle every ``pending`` row of ``store`` on the Contact's receipts with
        ``result``."""
        change = transition(StoreStatus.PENDING, result.status)
        rows = receipts_repo.pending_rows(contact_id, str(store))
        now = self._clock.now()
        count = receipts_repo.settle(contact_id, str(store), str(change.to), result.detail, now)
        if count != len(rows):
            raise RuntimeError(f"settling changed {count} rows; expected {len(rows)}")
        for row in rows:
            eventlog.append(
                entity=ENTITY,
                entity_id=row.id,
                from_status=str(StoreStatus.PENDING),
                to_status=str(change.to),
                cause=str(change.cause),
                cause_ref=row.receipt_id,
                actor=ACTOR,
                at=now,
            )
